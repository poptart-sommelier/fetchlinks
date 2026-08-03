import type {
  Subreddit,
  SubredditListFilters,
  SubredditStatus,
} from "../models/subreddits";
import { escapeLikeValue, SqlParams, utcIso, type SqlClient } from "./sql";

type SubredditRow = {
  id: number;
  name: string;
  normalizedName: string;
  enabled: boolean;
  addedAt: string;
  deletedAt: string | null;
  lastFetchedAt: string | null;
  latestPostAt: string | null;
  postSource: string | null;
};

const REDDIT_URL_PREFIX = "https://www.reddit.com/r/";

const SELECT_COLUMNS = `
  s.subreddit_id::int       AS id,
  s.name                    AS name,
  s.normalized_name         AS "normalizedName",
  s.enabled                 AS enabled,
  ${utcIso("s.added_at")}   AS "addedAt",
  ${utcIso("s.deleted_at")} AS "deletedAt",
  ${utcIso("rs.observed_at")} AS "lastFetchedAt",
  ${utcIso(`(
    SELECT MAX(p.posted_at) FROM content.posts p
    WHERE p.source_type = 'reddit'
      AND LOWER(p.source) = '${REDDIT_URL_PREFIX}' || s.normalized_name
  )`)}                      AS "latestPostAt",
  (
    SELECT p.source FROM content.posts p
    WHERE p.source_type = 'reddit'
      AND LOWER(p.source) = '${REDDIT_URL_PREFIX}' || s.normalized_name
    LIMIT 1
  )                         AS "postSource"
`;

const FROM_CLAUSE =
  "FROM catalog.subreddits s " +
  "LEFT JOIN content.reddit_state rs ON rs.subreddit = s.normalized_name";

function rowToSubreddit(row: SubredditRow): Subreddit {
  const status: SubredditStatus = row.deletedAt
    ? "removed"
    : row.enabled
      ? "active"
      : "disabled";
  return {
    id: row.id,
    name: row.name,
    normalizedName: row.normalizedName,
    enabled: row.enabled,
    addedAt: row.addedAt,
    deletedAt: row.deletedAt,
    lastFetchedAt: row.lastFetchedAt,
    latestPostAt: row.latestPostAt,
    postSource: row.postSource,
    status,
  };
}

export async function listSubreddits(
  sql: SqlClient,
  filters: SubredditListFilters = {},
): Promise<Subreddit[]> {
  const clauses: string[] = [];
  const params = new SqlParams();
  const status = filters.status ?? "all";

  if (status === "active") {
    clauses.push("s.enabled AND s.deleted_at IS NULL");
  } else if (status === "disabled") {
    clauses.push("NOT s.enabled AND s.deleted_at IS NULL");
  } else if (status === "removed") {
    clauses.push("s.deleted_at IS NOT NULL");
  }

  const q = filters.q?.trim();
  if (q) {
    const pattern = params.next(`%${escapeLikeValue(q.toLowerCase())}%`);
    clauses.push(
      `(LOWER(s.name) LIKE ${pattern} ESCAPE '\\' OR LOWER(s.normalized_name) LIKE ${pattern} ESCAPE '\\')`,
    );
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = await sql.query<SubredditRow>(
    `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} ${where} ` +
      "ORDER BY s.enabled DESC, (s.deleted_at IS NOT NULL) ASC, s.normalized_name ASC",
    params.toArray(),
  );

  return rows.map(rowToSubreddit);
}

export type SubredditCounts = {
  active: number;
  disabled: number;
  removed: number;
  total: number;
};

export async function countSubredditsByStatus(
  sql: SqlClient,
): Promise<SubredditCounts> {
  const rows = await sql.query<SubredditCounts>(
    `SELECT
      COALESCE(SUM(CASE WHEN deleted_at IS NULL AND enabled THEN 1 ELSE 0 END), 0)::int AS active,
      COALESCE(SUM(CASE WHEN deleted_at IS NULL AND NOT enabled THEN 1 ELSE 0 END), 0)::int AS disabled,
      COALESCE(SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END), 0)::int AS removed,
      COUNT(*)::int AS total
    FROM catalog.subreddits`,
  );

  return rows[0] ?? { active: 0, disabled: 0, removed: 0, total: 0 };
}

export type AddSubredditResult =
  | { status: "added"; subreddit: Subreddit }
  | { status: "exists"; subreddit: Subreddit }
  | { status: "invalid"; reason: string };

// Reddit subreddit names: 2-21 chars, letters/digits/underscores only.
const SUBREDDIT_NAME_PATTERN = /^[A-Za-z0-9_]{2,21}$/;

export function cleanSubredditName(raw: string): string {
  let value = raw.trim();
  if (value.toLowerCase().startsWith("/r/")) {
    value = value.slice(3);
  } else if (value.toLowerCase().startsWith("r/")) {
    value = value.slice(2);
  }
  return value.replace(/^\/+|\/+$/g, "");
}

export function normalizeSubredditName(raw: string): string {
  return cleanSubredditName(raw).toLowerCase();
}

async function selectByNormalized(
  sql: SqlClient,
  normalized: string,
): Promise<SubredditRow | undefined> {
  const rows = await sql.query<SubredditRow>(
    `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} WHERE s.normalized_name = $1`,
    [normalized],
  );

  return rows[0];
}

export async function addSubreddit(
  sql: SqlClient,
  rawName: string,
  now: Date = new Date(),
): Promise<AddSubredditResult> {
  const cleaned = cleanSubredditName(rawName);
  if (!cleaned) {
    return { status: "invalid", reason: "Subreddit name is required." };
  }
  if (!SUBREDDIT_NAME_PATTERN.test(cleaned)) {
    return {
      status: "invalid",
      reason:
        "Use 2-21 letters, digits, or underscores (an optional r/ prefix is allowed).",
    };
  }
  const normalized = cleaned.toLowerCase();

  const inserted = await sql.query<{ id: number }>(
    `INSERT INTO catalog.subreddits (name, normalized_name, enabled, added_at)
     VALUES ($1, $2, true, $3)
     ON CONFLICT (normalized_name) DO NOTHING
     RETURNING subreddit_id::int AS id`,
    [cleaned, normalized, now],
  );

  const row = await selectByNormalized(sql, normalized);
  if (!row) {
    return { status: "invalid", reason: "The subreddit could not be saved." };
  }

  return {
    status: inserted.length > 0 ? "added" : "exists",
    subreddit: rowToSubreddit(row),
  };
}

export async function softDeleteSubreddit(
  sql: SqlClient,
  subredditId: number,
  now: Date = new Date(),
): Promise<boolean> {
  const rows = await sql.query<{ id: number }>(
    `UPDATE catalog.subreddits
     SET deleted_at = $1, enabled = false
     WHERE subreddit_id = $2 AND deleted_at IS NULL
     RETURNING subreddit_id::int AS id`,
    [now, subredditId],
  );

  return rows.length > 0;
}

export async function restoreSubreddit(
  sql: SqlClient,
  subredditId: number,
): Promise<boolean> {
  const rows = await sql.query<{ id: number }>(
    `UPDATE catalog.subreddits
     SET deleted_at = NULL, enabled = true
     WHERE subreddit_id = $1 AND deleted_at IS NOT NULL
     RETURNING subreddit_id::int AS id`,
    [subredditId],
  );

  return rows.length > 0;
}

import { DatabaseSync } from "node:sqlite";

import type { AppConfig } from "./config";
import { loadAppConfig } from "./config";
import {
  openWritableFetchlinksDatabase,
  withWritableFetchlinksDatabase,
  type WritableFetchlinksDatabase,
} from "./feeds";
import type {
  Subreddit,
  SubredditListFilters,
  SubredditStatus,
} from "../models/subreddits";

type DbConfig = Pick<AppConfig, "fetchlinksDbPath">;

type Env = Partial<Record<string, string | undefined>>;

type SubredditRow = {
  id: number;
  name: string;
  normalizedName: string;
  enabled: number;
  addedAt: string;
  deletedAt: string | null;
  lastFetchedAt: string | null;
  latestPostAt: string | null;
  postSource: string | null;
};

const REDDIT_URL_PREFIX = "https://www.reddit.com/r/";

const SELECT_COLUMNS = `
  s.subreddit_id    AS id,
  s.name            AS name,
  s.normalized_name AS normalizedName,
  s.enabled         AS enabled,
  s.added_at        AS addedAt,
  s.deleted_at      AS deletedAt,
  rs.time_created   AS lastFetchedAt,
  (
    SELECT MAX(p.date_created) FROM posts p
    WHERE p.source_type = 'reddit'
      AND LOWER(p.source) = '${REDDIT_URL_PREFIX}' || s.normalized_name
  )                 AS latestPostAt,
  (
    SELECT p.source FROM posts p
    WHERE p.source_type = 'reddit'
      AND LOWER(p.source) = '${REDDIT_URL_PREFIX}' || s.normalized_name
    LIMIT 1
  )                 AS postSource
`;

const FROM_CLAUSE =
  "FROM subreddits s LEFT JOIN reddit_state rs ON rs.subreddit = s.normalized_name";

// Idempotent guard so the web admin can read/write the subreddit list even
// if the ingest bootstrap hasn't created the table yet. Mirrors
// table_subreddits_configure in ingest/db_setup.py.
function ensureSubredditsSchema(database: WritableFetchlinksDatabase): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS subreddits (
      subreddit_id    INTEGER PRIMARY KEY,
      name            TEXT NOT NULL,
      normalized_name TEXT NOT NULL UNIQUE,
      enabled         INTEGER NOT NULL DEFAULT 1,
      added_at        TEXT NOT NULL,
      deleted_at      TEXT
    )
  `);
  database.exec(
    "CREATE INDEX IF NOT EXISTS idx_subreddits_live ON subreddits(enabled, deleted_at)",
  );
  // reddit_state and posts are read via LEFT JOIN / subqueries; make sure
  // reddit_state exists so the JOIN doesn't error on a fresh DB.
  database.exec(`
    CREATE TABLE IF NOT EXISTS reddit_state (
      subreddit TEXT PRIMARY KEY,
      last_seen_fullname TEXT,
      time_created TEXT
    )
  `);
}

export function openWritableSubredditsDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  const database = openWritableFetchlinksDatabase(config);
  ensureSubredditsSchema(database);
  return database;
}

export function withWritableSubredditsDatabase<T>(
  config: DbConfig,
  callback: (database: WritableFetchlinksDatabase) => T,
): T {
  return withWritableFetchlinksDatabase(config, (database) => {
    ensureSubredditsSchema(database);
    return callback(database);
  });
}

export function openConfiguredWritableSubredditsDatabase(
  env: Env = process.env,
): WritableFetchlinksDatabase {
  return openWritableSubredditsDatabase(loadAppConfig(env));
}

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
    enabled: row.enabled === 1,
    addedAt: row.addedAt,
    deletedAt: row.deletedAt,
    lastFetchedAt: row.lastFetchedAt,
    latestPostAt: row.latestPostAt,
    postSource: row.postSource,
    status,
  };
}

export function listSubreddits(
  database: DatabaseSync,
  filters: SubredditListFilters = {},
): Subreddit[] {
  const clauses: string[] = [];
  const params: (string | number)[] = [];
  const status = filters.status ?? "all";

  if (status === "active") {
    clauses.push("s.enabled = 1 AND s.deleted_at IS NULL");
  } else if (status === "disabled") {
    clauses.push("s.enabled = 0 AND s.deleted_at IS NULL");
  } else if (status === "removed") {
    clauses.push("s.deleted_at IS NOT NULL");
  }

  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${escapeLikeValue(q.toLowerCase())}%`;
    clauses.push(
      "(LOWER(s.name) LIKE ? ESCAPE '\\' OR LOWER(s.normalized_name) LIKE ? ESCAPE '\\')",
    );
    params.push(pattern, pattern);
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = database
    .prepare(
      `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} ${where} ` +
        "ORDER BY s.enabled DESC, s.deleted_at IS NOT NULL ASC, s.normalized_name ASC",
    )
    .all(...params) as SubredditRow[];
  return rows.map(rowToSubreddit);
}

export type SubredditCounts = {
  active: number;
  disabled: number;
  removed: number;
  total: number;
};

export function countSubredditsByStatus(
  database: DatabaseSync,
): SubredditCounts {
  const row = database
    .prepare(
      `SELECT
        SUM(CASE WHEN deleted_at IS NULL AND enabled = 1 THEN 1 ELSE 0 END) AS active,
        SUM(CASE WHEN deleted_at IS NULL AND enabled = 0 THEN 1 ELSE 0 END) AS disabled,
        SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS removed,
        COUNT(*) AS total
      FROM subreddits`,
    )
    .get() as {
      active: number | null;
      disabled: number | null;
      removed: number | null;
      total: number | null;
    };
  return {
    active: row.active ?? 0,
    disabled: row.disabled ?? 0,
    removed: row.removed ?? 0,
    total: row.total ?? 0,
  };
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

function selectByNormalized(
  database: DatabaseSync,
  normalized: string,
): SubredditRow | undefined {
  return database
    .prepare(
      `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} WHERE s.normalized_name = ?`,
    )
    .get(normalized) as SubredditRow | undefined;
}

export function addSubreddit(
  database: WritableFetchlinksDatabase,
  rawName: string,
  now: Date = new Date(),
): AddSubredditResult {
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

  const existing = selectByNormalized(database, normalized);
  if (existing) {
    return { status: "exists", subreddit: rowToSubreddit(existing) };
  }

  const addedAt = formatTimestamp(now);
  database
    .prepare(
      `INSERT INTO subreddits (name, normalized_name, enabled, added_at)
       VALUES (?, ?, 1, ?)`,
    )
    .run(cleaned, normalized, addedAt);

  const inserted = selectByNormalized(database, normalized) as SubredditRow;
  return { status: "added", subreddit: rowToSubreddit(inserted) };
}

export function softDeleteSubreddit(
  database: WritableFetchlinksDatabase,
  subredditId: number,
  now: Date = new Date(),
): boolean {
  const result = database
    .prepare(
      `UPDATE subreddits
       SET deleted_at = ?, enabled = 0
       WHERE subreddit_id = ? AND deleted_at IS NULL`,
    )
    .run(formatTimestamp(now), subredditId);
  return Number(result.changes) > 0;
}

export function restoreSubreddit(
  database: WritableFetchlinksDatabase,
  subredditId: number,
): boolean {
  const result = database
    .prepare(
      `UPDATE subreddits
       SET deleted_at = NULL, enabled = 1
       WHERE subreddit_id = ? AND deleted_at IS NOT NULL`,
    )
    .run(subredditId);
  return Number(result.changes) > 0;
}

function formatTimestamp(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
  );
}

function escapeLikeValue(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll("%", "\\%")
    .replaceAll("_", "\\_");
}

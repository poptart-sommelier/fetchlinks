import type {
  RssFeed,
  RssFeedListFilters,
  RssFeedStatus,
} from "../models/rss-feeds";
import { escapeLikeValue, SqlParams, utcIso, type SqlClient } from "./sql";

type RssFeedRow = {
  id: number;
  feedUrl: string;
  normalizedUrl: string;
  enabled: boolean;
  addedAt: string;
  deletedAt: string | null;
  lastFetchedAt: string | null;
  lastSuccessAt: string | null;
  lastStatus: number | null;
  lastError: string | null;
  consecutiveFailures: number;
  etag: string | null;
  lastModified: string | null;
  latestEntryAt: string | null;
  siteLink: string | null;
};

// Feed identity lives in `catalog`, which the admin owns and writes. Per-feed
// health lives in `content`, which only the publisher writes. They join on
// `normalized_url` — the natural key — rather than on the catalog's surrogate
// id, so health survives a feed being removed and re-added and neither schema
// depends on the other's row numbering.
const SELECT_COLUMNS = `
  f.feed_id::int                      AS id,
  f.feed_url                          AS "feedUrl",
  f.normalized_url                    AS "normalizedUrl",
  f.enabled                           AS enabled,
  ${utcIso("f.added_at")}             AS "addedAt",
  ${utcIso("f.deleted_at")}           AS "deletedAt",
  ${utcIso("h.last_fetched_at")}      AS "lastFetchedAt",
  ${utcIso("h.last_success_at")}      AS "lastSuccessAt",
  h.last_status                       AS "lastStatus",
  h.last_error                        AS "lastError",
  COALESCE(h.consecutive_failures, 0) AS "consecutiveFailures",
  h.etag                              AS etag,
  h.last_modified                     AS "lastModified",
  ${utcIso("h.latest_entry_at")}      AS "latestEntryAt",
  h.site_link                         AS "siteLink"
`;

const FROM_CLAUSE =
  "FROM catalog.rss_feeds f " +
  "LEFT JOIN content.rss_feed_health h ON h.normalized_url = f.normalized_url";

// Live feeds first, then disabled, then tombstoned. Matches the order the admin
// list presents and keeps the rows an operator most likely wants at the top.
const ORDER_BY =
  "ORDER BY f.enabled DESC, (f.deleted_at IS NOT NULL) ASC, f.feed_url ASC";

function rowToFeed(row: RssFeedRow): RssFeed {
  const status: RssFeedStatus = row.deletedAt
    ? "removed"
    : row.enabled
      ? "active"
      : "disabled";
  return {
    id: row.id,
    feedUrl: row.feedUrl,
    normalizedUrl: row.normalizedUrl,
    enabled: row.enabled,
    addedAt: row.addedAt,
    deletedAt: row.deletedAt,
    lastFetchedAt: row.lastFetchedAt,
    lastSuccessAt: row.lastSuccessAt,
    lastStatus: row.lastStatus,
    lastError: row.lastError,
    consecutiveFailures: row.consecutiveFailures,
    etag: row.etag,
    lastModified: row.lastModified,
    latestEntryAt: row.latestEntryAt,
    siteLink: row.siteLink,
    status,
  };
}

export async function listRssFeeds(
  sql: SqlClient,
  filters: RssFeedListFilters = {},
): Promise<RssFeed[]> {
  const clauses: string[] = [];
  const params = new SqlParams();
  const status = filters.status ?? "all";

  if (status === "active") {
    clauses.push("f.enabled AND f.deleted_at IS NULL");
  } else if (status === "disabled") {
    clauses.push("NOT f.enabled AND f.deleted_at IS NULL");
  } else if (status === "removed") {
    clauses.push("f.deleted_at IS NOT NULL");
  }

  if (filters.errors) {
    clauses.push(
      "f.enabled AND f.deleted_at IS NULL AND " +
        "(COALESCE(h.consecutive_failures, 0) > 0 OR COALESCE(h.last_status, 0) >= 400)",
    );
  }

  const q = filters.q?.trim();
  if (q) {
    const pattern = params.next(`%${escapeLikeValue(q.toLowerCase())}%`);
    clauses.push(
      `(LOWER(f.feed_url) LIKE ${pattern} ESCAPE '\\' OR LOWER(f.normalized_url) LIKE ${pattern} ESCAPE '\\')`,
    );
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = await sql.query<RssFeedRow>(
    `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} ${where} ${ORDER_BY}`,
    params.toArray(),
  );

  return rows.map(rowToFeed);
}

export type RssFeedCounts = {
  active: number;
  disabled: number;
  removed: number;
  errors: number;
  total: number;
};

export async function countRssFeedsByStatus(
  sql: SqlClient,
): Promise<RssFeedCounts> {
  // COUNT and SUM return bigint, and SUM over no rows returns NULL. Both are
  // resolved in SQL so the caller always gets a plain number.
  const rows = await sql.query<RssFeedCounts>(
    `SELECT
      COALESCE(SUM(CASE WHEN f.deleted_at IS NULL AND f.enabled THEN 1 ELSE 0 END), 0)::int AS active,
      COALESCE(SUM(CASE WHEN f.deleted_at IS NULL AND NOT f.enabled THEN 1 ELSE 0 END), 0)::int AS disabled,
      COALESCE(SUM(CASE WHEN f.deleted_at IS NOT NULL THEN 1 ELSE 0 END), 0)::int AS removed,
      COALESCE(SUM(CASE
            WHEN f.deleted_at IS NULL
              AND f.enabled
              AND (COALESCE(h.consecutive_failures, 0) > 0 OR COALESCE(h.last_status, 0) >= 400)
            THEN 1 ELSE 0
          END), 0)::int AS errors,
      COUNT(*)::int AS total
    ${FROM_CLAUSE}`,
  );

  return (
    rows[0] ?? { active: 0, disabled: 0, removed: 0, errors: 0, total: 0 }
  );
}

export type AddFeedResult =
  | { status: "added"; feed: RssFeed }
  | { status: "exists"; feed: RssFeed }
  | { status: "invalid"; reason: string };

export function normalizeFeedUrl(rawUrl: string): string {
  const trimmed = rawUrl.trim();
  if (!trimmed) return "";
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "";
    }
    url.hash = "";
    // Lowercase the host. Preserve path/query exactly so different feed
    // paths on the same host remain distinct.
    url.hostname = url.hostname.toLowerCase();
    return url.toString();
  } catch {
    return "";
  }
}

async function selectFeedByNormalizedUrl(
  sql: SqlClient,
  normalized: string,
): Promise<RssFeedRow | undefined> {
  const rows = await sql.query<RssFeedRow>(
    `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} WHERE f.normalized_url = $1`,
    [normalized],
  );

  return rows[0];
}

export async function addRssFeed(
  sql: SqlClient,
  rawUrl: string,
  now: Date = new Date(),
): Promise<AddFeedResult> {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return { status: "invalid", reason: "URL is required." };
  }
  const normalized = normalizeFeedUrl(trimmed);
  if (!normalized) {
    return {
      status: "invalid",
      reason: "URL must be an absolute http(s) URL.",
    };
  }

  // Let the unique index decide whether this is new rather than checking first
  // and inserting second: two admins submitting the same feed at once then get
  // "exists" instead of one of them getting a constraint violation.
  const inserted = await sql.query<{ id: number }>(
    `INSERT INTO catalog.rss_feeds (feed_url, normalized_url, enabled, added_at)
     VALUES ($1, $2, true, $3)
     ON CONFLICT (normalized_url) DO NOTHING
     RETURNING feed_id::int AS id`,
    [trimmed, normalized, now],
  );

  const feedRow = await selectFeedByNormalizedUrl(sql, normalized);
  if (!feedRow) {
    return { status: "invalid", reason: "The feed could not be saved." };
  }

  return {
    status: inserted.length > 0 ? "added" : "exists",
    feed: rowToFeed(feedRow),
  };
}

export async function softDeleteRssFeed(
  sql: SqlClient,
  feedId: number,
  now: Date = new Date(),
): Promise<boolean> {
  const rows = await sql.query<{ id: number }>(
    `UPDATE catalog.rss_feeds
     SET deleted_at = $1, enabled = false
     WHERE feed_id = $2 AND deleted_at IS NULL
     RETURNING feed_id::int AS id`,
    [now, feedId],
  );

  return rows.length > 0;
}

export async function restoreRssFeed(
  sql: SqlClient,
  feedId: number,
): Promise<boolean> {
  // Identity only: the failure counter lives in `content`, which the web role
  // cannot write, and resets on the next successful publish anyway.
  const rows = await sql.query<{ id: number }>(
    `UPDATE catalog.rss_feeds
     SET deleted_at = NULL, enabled = true
     WHERE feed_id = $1 AND deleted_at IS NOT NULL
     RETURNING feed_id::int AS id`,
    [feedId],
  );

  return rows.length > 0;
}

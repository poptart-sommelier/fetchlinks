import { DatabaseSync } from "node:sqlite";

import type { AppConfig } from "./config";
import { loadAppConfig } from "./config";
import type {
  RssFeed,
  RssFeedListFilters,
  RssFeedStatus,
} from "../models/rss-feeds";

type DbConfig = Pick<AppConfig, "fetchlinksDbPath">;

type Env = Partial<Record<string, string | undefined>>;

type RssFeedRow = {
  id: number;
  feedUrl: string;
  normalizedUrl: string;
  enabled: number;
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
};

export type WritableFetchlinksDatabase = DatabaseSync;

const SELECT_COLUMNS = `
  feed_id              AS id,
  feed_url             AS feedUrl,
  normalized_url       AS normalizedUrl,
  enabled              AS enabled,
  added_at             AS addedAt,
  deleted_at           AS deletedAt,
  last_fetched_at      AS lastFetchedAt,
  last_success_at      AS lastSuccessAt,
  last_status          AS lastStatus,
  last_error           AS lastError,
  consecutive_failures AS consecutiveFailures,
  etag                 AS etag,
  last_modified        AS lastModified,
  latest_entry_at      AS latestEntryAt
`;

export function openWritableFetchlinksDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  const database = new DatabaseSync(config.fetchlinksDbPath, {
    timeout: 5000,
  });
  // Make sure we honour foreign keys and don't block forever on a writer.
  database.exec("PRAGMA foreign_keys = ON");
  database.exec("PRAGMA busy_timeout = 5000");
  return database;
}

export function openConfiguredWritableFetchlinksDatabase(
  env: Env = process.env,
): WritableFetchlinksDatabase {
  return openWritableFetchlinksDatabase(loadAppConfig(env));
}

export function withWritableFetchlinksDatabase<T>(
  config: DbConfig,
  callback: (database: WritableFetchlinksDatabase) => T,
): T {
  const database = openWritableFetchlinksDatabase(config);
  try {
    return callback(database);
  } finally {
    if (database.isOpen) {
      database.close();
    }
  }
}

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
    enabled: row.enabled === 1,
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
    status,
  };
}

export function listRssFeeds(
  database: DatabaseSync,
  filters: RssFeedListFilters = {},
): RssFeed[] {
  const clauses: string[] = [];
  const params: (string | number)[] = [];
  const status = filters.status ?? "all";

  if (status === "active") {
    clauses.push("enabled = 1 AND deleted_at IS NULL");
  } else if (status === "disabled") {
    clauses.push("enabled = 0 AND deleted_at IS NULL");
  } else if (status === "removed") {
    clauses.push("deleted_at IS NOT NULL");
  }

  if (filters.errors) {
    clauses.push(
      "enabled = 1 AND deleted_at IS NULL AND (consecutive_failures > 0 OR last_status >= 400)",
    );
  }

  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${escapeLikeValue(q.toLowerCase())}%`;
    clauses.push(
      "(LOWER(feed_url) LIKE ? ESCAPE '\\' OR LOWER(normalized_url) LIKE ? ESCAPE '\\')",
    );
    params.push(pattern, pattern);
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = database
    .prepare(
      `SELECT ${SELECT_COLUMNS} FROM rss_feeds ${where} ORDER BY enabled DESC, deleted_at IS NOT NULL ASC, feed_url ASC`,
    )
    .all(...params) as RssFeedRow[];
  return rows.map(rowToFeed);
}

export type RssFeedCounts = {
  active: number;
  disabled: number;
  removed: number;
  errors: number;
  total: number;
};

export function countRssFeedsByStatus(database: DatabaseSync): RssFeedCounts {
  const row = database
    .prepare(
      `SELECT
        SUM(CASE WHEN deleted_at IS NULL AND enabled = 1 THEN 1 ELSE 0 END) AS active,
        SUM(CASE WHEN deleted_at IS NULL AND enabled = 0 THEN 1 ELSE 0 END) AS disabled,
        SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS removed,
        SUM(CASE
              WHEN deleted_at IS NULL
                AND enabled = 1
                AND (consecutive_failures > 0 OR last_status >= 400)
              THEN 1 ELSE 0
            END) AS errors,
        COUNT(*) AS total
      FROM rss_feeds`,
    )
    .get() as {
      active: number | null;
      disabled: number | null;
      removed: number | null;
      errors: number | null;
      total: number | null;
    };
  return {
    active: row.active ?? 0,
    disabled: row.disabled ?? 0,
    removed: row.removed ?? 0,
    errors: row.errors ?? 0,
    total: row.total ?? 0,
  };
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

export function addRssFeed(
  database: WritableFetchlinksDatabase,
  rawUrl: string,
  now: Date = new Date(),
): AddFeedResult {
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

  const existing = database
    .prepare(`SELECT ${SELECT_COLUMNS} FROM rss_feeds WHERE normalized_url = ?`)
    .get(normalized) as RssFeedRow | undefined;
  if (existing) {
    return { status: "exists", feed: rowToFeed(existing) };
  }

  const addedAt = formatTimestamp(now);
  database
    .prepare(
      `INSERT INTO rss_feeds
        (feed_url, normalized_url, enabled, added_at)
       VALUES (?, ?, 1, ?)`,
    )
    .run(trimmed, normalized, addedAt);

  const inserted = database
    .prepare(`SELECT ${SELECT_COLUMNS} FROM rss_feeds WHERE normalized_url = ?`)
    .get(normalized) as RssFeedRow;
  return { status: "added", feed: rowToFeed(inserted) };
}

export function setRssFeedEnabled(
  database: WritableFetchlinksDatabase,
  feedId: number,
  enabled: boolean,
): boolean {
  const result = database
    .prepare(
      `UPDATE rss_feeds
       SET enabled = ?,
           consecutive_failures = CASE WHEN ? = 1 THEN 0 ELSE consecutive_failures END
       WHERE feed_id = ?
         AND deleted_at IS NULL`,
    )
    .run(enabled ? 1 : 0, enabled ? 1 : 0, feedId);
  return Number(result.changes) > 0;
}

export function softDeleteRssFeed(
  database: WritableFetchlinksDatabase,
  feedId: number,
  now: Date = new Date(),
): boolean {
  const result = database
    .prepare(
      `UPDATE rss_feeds
       SET deleted_at = ?, enabled = 0
       WHERE feed_id = ? AND deleted_at IS NULL`,
    )
    .run(formatTimestamp(now), feedId);
  return Number(result.changes) > 0;
}

export function restoreRssFeed(
  database: WritableFetchlinksDatabase,
  feedId: number,
): boolean {
  const result = database
    .prepare(
      `UPDATE rss_feeds
       SET deleted_at = NULL,
           enabled = 1,
           consecutive_failures = 0
       WHERE feed_id = ? AND deleted_at IS NOT NULL`,
    )
    .run(feedId);
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

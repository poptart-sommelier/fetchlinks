import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import type { AppConfig } from "./config";
import { loadAppConfig, toReadOnlySqliteUri } from "./config";
import type {
  RssFeed,
  RssFeedListFilters,
  RssFeedStatus,
} from "../models/rss-feeds";

type DbConfig = Pick<AppConfig, "fetchlinksDbPath"> &
  Partial<Pick<AppConfig, "controlDbPath">>;

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
  siteLink: string | null;
};

export type WritableFetchlinksDatabase = DatabaseSync;

// Feed identity lives in the control DB (main schema). Per-feed health lives
// in the data DB, attached read-only as `data` and joined on normalized_url
// (the natural key, never an autoincrement id). In single-host installs the
// control and data paths are the same physical file, so the join resolves
// within one file; the ATTACH is still issued so the SQL is identical in
// both modes.
const SELECT_COLUMNS = `
  f.feed_id                          AS id,
  f.feed_url                         AS feedUrl,
  f.normalized_url                   AS normalizedUrl,
  f.enabled                          AS enabled,
  f.added_at                         AS addedAt,
  f.deleted_at                       AS deletedAt,
  h.last_fetched_at                  AS lastFetchedAt,
  h.last_success_at                  AS lastSuccessAt,
  h.last_status                      AS lastStatus,
  h.last_error                       AS lastError,
  COALESCE(h.consecutive_failures, 0) AS consecutiveFailures,
  h.etag                             AS etag,
  h.last_modified                    AS lastModified,
  h.latest_entry_at                  AS latestEntryAt,
  h.site_link                        AS siteLink
`;

const FROM_CLAUSE =
  "FROM rss_feeds f " +
  "LEFT JOIN data.rss_feed_health h ON h.normalized_url = f.normalized_url";

// Control-DB schema (admin-owned identity). Safe to create on the writable
// connection in any mode. Mirrors the identity columns in
// ingest/db_setup.py; the health columns are kept for single-host
// compatibility with older databases (ingest now writes them to
// rss_feed_health instead).
function ensureControlSchema(database: WritableFetchlinksDatabase): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS rss_feeds (
      feed_id              INTEGER PRIMARY KEY,
      feed_url             TEXT NOT NULL,
      normalized_url       TEXT NOT NULL UNIQUE,
      enabled              INTEGER NOT NULL DEFAULT 1,
      added_at             TEXT NOT NULL,
      deleted_at           TEXT,
      last_fetched_at      TEXT,
      last_success_at      TEXT,
      last_status          INTEGER,
      last_error           TEXT,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      etag                 TEXT,
      last_modified        TEXT,
      latest_entry_at      TEXT,
      site_link            TEXT
    )
  `);
  database.exec(
    "CREATE INDEX IF NOT EXISTS idx_rss_feeds_live ON rss_feeds(enabled, deleted_at)",
  );
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
}

// Data-DB tables the admin reads (health, ingest state, follows). Only
// created when control and data are the same physical file (single-host),
// since the data DB is attached read-only in two-host mode. Mirrors
// ingest/db_setup.py so the web admin doesn't error on a not-yet-ingested
// single-host DB. `posts` is intentionally not created here (it is owned by
// ingest and the read paths assume it exists, matching prior behaviour).
function ensureDataSchema(database: WritableFetchlinksDatabase): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS rss_feed_health (
      normalized_url       TEXT PRIMARY KEY,
      last_fetched_at      TEXT,
      last_success_at      TEXT,
      last_status          INTEGER,
      last_error           TEXT,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      etag                 TEXT,
      last_modified        TEXT,
      latest_entry_at      TEXT,
      site_link            TEXT
    )
  `);
  database.exec(`
    CREATE TABLE IF NOT EXISTS reddit_state (
      subreddit          TEXT PRIMARY KEY,
      last_seen_fullname TEXT,
      time_created       TEXT
    )
  `);
  database.exec(`
    CREATE TABLE IF NOT EXISTS bluesky_follows (
      did          TEXT PRIMARY KEY,
      handle       TEXT NOT NULL,
      display_name TEXT,
      synced_at    TEXT NOT NULL
    )
  `);
  database.exec(
    "CREATE INDEX IF NOT EXISTS idx_bluesky_follows_handle ON bluesky_follows(handle)",
  );
  database.exec(`
    CREATE TABLE IF NOT EXISTS mastodon_follows (
      instance_name TEXT NOT NULL,
      account_id    TEXT NOT NULL,
      acct          TEXT NOT NULL,
      display_name  TEXT,
      url           TEXT,
      synced_at     TEXT NOT NULL,
      PRIMARY KEY (instance_name, account_id)
    )
  `);
  database.exec(
    "CREATE INDEX IF NOT EXISTS idx_mastodon_follows_instance ON mastodon_follows(instance_name)",
  );
}

export function openWritableFetchlinksDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  const dataPath = config.fetchlinksDbPath;
  const controlPath = config.controlDbPath ?? dataPath;
  const singleHost = path.resolve(controlPath) === path.resolve(dataPath);

  const database = new DatabaseSync(controlPath, {
    timeout: 5000,
  });
  // Make sure we honour foreign keys and don't block forever on a writer.
  database.exec("PRAGMA foreign_keys = ON");
  database.exec("PRAGMA busy_timeout = 5000");
  ensureControlSchema(database);
  if (singleHost) {
    // Same file: create the data-side tables here before attaching, so the
    // joins below resolve on a fresh DB.
    ensureDataSchema(database);
  }
  // Attach the data DB read-only so identity/health joins use one connection
  // and the web can never write to the Pi-owned replica.
  database
    .prepare("ATTACH DATABASE ? AS data")
    .run(toReadOnlySqliteUri(dataPath));
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
    siteLink: row.siteLink,
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
    clauses.push("f.enabled = 1 AND f.deleted_at IS NULL");
  } else if (status === "disabled") {
    clauses.push("f.enabled = 0 AND f.deleted_at IS NULL");
  } else if (status === "removed") {
    clauses.push("f.deleted_at IS NOT NULL");
  }

  if (filters.errors) {
    clauses.push(
      "f.enabled = 1 AND f.deleted_at IS NULL AND " +
        "(COALESCE(h.consecutive_failures, 0) > 0 OR COALESCE(h.last_status, 0) >= 400)",
    );
  }

  const q = filters.q?.trim();
  if (q) {
    const pattern = `%${escapeLikeValue(q.toLowerCase())}%`;
    clauses.push(
      "(LOWER(f.feed_url) LIKE ? ESCAPE '\\' OR LOWER(f.normalized_url) LIKE ? ESCAPE '\\')",
    );
    params.push(pattern, pattern);
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = database
    .prepare(
      `SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} ${where} ORDER BY f.enabled DESC, f.deleted_at IS NOT NULL ASC, f.feed_url ASC`,
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
        SUM(CASE WHEN f.deleted_at IS NULL AND f.enabled = 1 THEN 1 ELSE 0 END) AS active,
        SUM(CASE WHEN f.deleted_at IS NULL AND f.enabled = 0 THEN 1 ELSE 0 END) AS disabled,
        SUM(CASE WHEN f.deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS removed,
        SUM(CASE
              WHEN f.deleted_at IS NULL
                AND f.enabled = 1
                AND (COALESCE(h.consecutive_failures, 0) > 0 OR COALESCE(h.last_status, 0) >= 400)
              THEN 1 ELSE 0
            END) AS errors,
        COUNT(*) AS total
      ${FROM_CLAUSE}`,
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
    .prepare(`SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} WHERE f.normalized_url = ?`)
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
    .prepare(`SELECT ${SELECT_COLUMNS} ${FROM_CLAUSE} WHERE f.normalized_url = ?`)
    .get(normalized) as RssFeedRow;
  return { status: "added", feed: rowToFeed(inserted) };
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
  // Identity only: the failure counter lives in the (read-only) data DB and
  // resets naturally on the next successful ingest fetch.
  const result = database
    .prepare(
      `UPDATE rss_feeds
       SET deleted_at = NULL,
           enabled = 1
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

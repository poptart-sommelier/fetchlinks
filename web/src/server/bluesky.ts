import { DatabaseSync } from "node:sqlite";

import type { AppConfig } from "./config";
import { loadAppConfig } from "./config";
import {
  openWritableFetchlinksDatabase,
  withWritableFetchlinksDatabase,
  type WritableFetchlinksDatabase,
} from "./feeds";
import type {
  BlueskyFollow,
  BlueskyFollowsSnapshot,
} from "../models/bluesky-follows";

type DbConfig = Pick<AppConfig, "fetchlinksDbPath">;

type Env = Partial<Record<string, string | undefined>>;

type BlueskyFollowRow = {
  did: string;
  handle: string;
  displayName: string | null;
  syncedAt: string;
  latestPostAt: string | null;
  postSource: string | null;
};

const BLUESKY_PROFILE_PREFIX = "https://bsky.app/profile/";

const SELECT_COLUMNS = `
  f.did          AS did,
  f.handle       AS handle,
  f.display_name AS displayName,
  f.synced_at    AS syncedAt,
  (
    SELECT MAX(p.date_created) FROM posts p
    WHERE p.source_type = 'bluesky'
      AND LOWER(p.source) = '${BLUESKY_PROFILE_PREFIX}' || LOWER(f.handle)
  )              AS latestPostAt,
  (
    SELECT p.source FROM posts p
    WHERE p.source_type = 'bluesky'
      AND LOWER(p.source) = '${BLUESKY_PROFILE_PREFIX}' || LOWER(f.handle)
    LIMIT 1
  )              AS postSource
`;

// Idempotent guard so the web admin can read the snapshot even if the
// ingest bootstrap hasn't created the table yet. Mirrors
// table_bluesky_follows_configure in ingest/db_setup.py.
function ensureBlueskyFollowsSchema(database: WritableFetchlinksDatabase): void {
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
}

export function openWritableBlueskyDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  const database = openWritableFetchlinksDatabase(config);
  ensureBlueskyFollowsSchema(database);
  return database;
}

export function withWritableBlueskyDatabase<T>(
  config: DbConfig,
  callback: (database: WritableFetchlinksDatabase) => T,
): T {
  return withWritableFetchlinksDatabase(config, (database) => {
    ensureBlueskyFollowsSchema(database);
    return callback(database);
  });
}

export function openConfiguredWritableBlueskyDatabase(
  env: Env = process.env,
): WritableFetchlinksDatabase {
  return openWritableBlueskyDatabase(loadAppConfig(env));
}

function rowToFollow(row: BlueskyFollowRow): BlueskyFollow {
  return {
    did: row.did,
    handle: row.handle,
    displayName: row.displayName,
    syncedAt: row.syncedAt,
    latestPostAt: row.latestPostAt,
    postSource: row.postSource,
  };
}

export function getBlueskyFollows(database: DatabaseSync): BlueskyFollowsSnapshot {
  const rows = database
    .prepare(
      `SELECT ${SELECT_COLUMNS} FROM bluesky_follows f ORDER BY LOWER(f.handle) ASC`,
    )
    .all() as BlueskyFollowRow[];
  const follows = rows.map(rowToFollow);
  const lastSyncedAt = follows.reduce<string | null>((latest, follow) => {
    if (!latest || follow.syncedAt > latest) return follow.syncedAt;
    return latest;
  }, null);
  return { follows, lastSyncedAt };
}

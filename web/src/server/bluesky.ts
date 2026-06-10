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

type DbConfig = Pick<AppConfig, "fetchlinksDbPath"> &
  Partial<Pick<AppConfig, "controlDbPath">>;

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
    SELECT MAX(p.date_created) FROM data.posts p
    WHERE p.source_type = 'bluesky'
      AND LOWER(p.source) = '${BLUESKY_PROFILE_PREFIX}' || LOWER(f.handle)
  )              AS latestPostAt,
  (
    SELECT p.source FROM data.posts p
    WHERE p.source_type = 'bluesky'
      AND LOWER(p.source) = '${BLUESKY_PROFILE_PREFIX}' || LOWER(f.handle)
    LIMIT 1
  )              AS postSource
`;

export function openWritableBlueskyDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  return openWritableFetchlinksDatabase(config);
}

export function withWritableBlueskyDatabase<T>(
  config: DbConfig,
  callback: (database: WritableFetchlinksDatabase) => T,
): T {
  return withWritableFetchlinksDatabase(config, callback);
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
      `SELECT ${SELECT_COLUMNS} FROM data.bluesky_follows f ORDER BY LOWER(f.handle) ASC`,
    )
    .all() as BlueskyFollowRow[];
  const follows = rows.map(rowToFollow);
  const lastSyncedAt = follows.reduce<string | null>((latest, follow) => {
    if (!latest || follow.syncedAt > latest) return follow.syncedAt;
    return latest;
  }, null);
  return { follows, lastSyncedAt };
}

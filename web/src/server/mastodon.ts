import { DatabaseSync } from "node:sqlite";

import type { AppConfig } from "./config";
import { loadAppConfig } from "./config";
import {
  openWritableFetchlinksDatabase,
  withWritableFetchlinksDatabase,
  type WritableFetchlinksDatabase,
} from "./feeds";
import type {
  MastodonFollow,
  MastodonFollowsSnapshot,
} from "../models/mastodon-follows";

type DbConfig = Pick<AppConfig, "fetchlinksDbPath">;

type Env = Partial<Record<string, string | undefined>>;

type MastodonFollowRow = {
  instanceName: string;
  accountId: string;
  acct: string;
  displayName: string | null;
  url: string | null;
  syncedAt: string;
  latestPostAt: string | null;
  postSource: string | null;
};

const SELECT_COLUMNS = `
  f.instance_name AS instanceName,
  f.account_id    AS accountId,
  f.acct          AS acct,
  f.display_name  AS displayName,
  f.url           AS url,
  f.synced_at     AS syncedAt,
  (
    SELECT MAX(p.date_created) FROM posts p
    WHERE p.source_type = 'mastodon'
      AND p.source = f.url
  )               AS latestPostAt,
  (
    SELECT p.source FROM posts p
    WHERE p.source_type = 'mastodon'
      AND p.source = f.url
    LIMIT 1
  )               AS postSource
`;

// Idempotent guard so the web admin can read the snapshot even if the
// ingest bootstrap hasn't created the table yet. Mirrors
// table_mastodon_follows_configure in ingest/db_setup.py.
function ensureMastodonFollowsSchema(
  database: WritableFetchlinksDatabase,
): void {
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

export function openWritableMastodonDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  const database = openWritableFetchlinksDatabase(config);
  ensureMastodonFollowsSchema(database);
  return database;
}

export function withWritableMastodonDatabase<T>(
  config: DbConfig,
  callback: (database: WritableFetchlinksDatabase) => T,
): T {
  return withWritableFetchlinksDatabase(config, (database) => {
    ensureMastodonFollowsSchema(database);
    return callback(database);
  });
}

export function openConfiguredWritableMastodonDatabase(
  env: Env = process.env,
): WritableFetchlinksDatabase {
  return openWritableMastodonDatabase(loadAppConfig(env));
}

function rowToFollow(row: MastodonFollowRow): MastodonFollow {
  return {
    instanceName: row.instanceName,
    accountId: row.accountId,
    acct: row.acct,
    displayName: row.displayName,
    url: row.url,
    syncedAt: row.syncedAt,
    latestPostAt: row.latestPostAt,
    postSource: row.postSource,
  };
}

export function getMastodonFollows(
  database: DatabaseSync,
): MastodonFollowsSnapshot {
  const rows = database
    .prepare(
      `SELECT ${SELECT_COLUMNS} FROM mastodon_follows f ` +
        "ORDER BY f.instance_name ASC, LOWER(f.acct) ASC",
    )
    .all() as MastodonFollowRow[];
  const follows = rows.map(rowToFollow);
  const lastSyncedAt = follows.reduce<string | null>((latest, follow) => {
    if (!latest || follow.syncedAt > latest) return follow.syncedAt;
    return latest;
  }, null);
  return { follows, lastSyncedAt };
}

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

type DbConfig = Pick<AppConfig, "fetchlinksDbPath"> &
  Partial<Pick<AppConfig, "controlDbPath">>;

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
    SELECT MAX(p.date_created) FROM data.posts p
    WHERE p.source_type = 'mastodon'
      AND p.source = f.url
  )               AS latestPostAt,
  (
    SELECT p.source FROM data.posts p
    WHERE p.source_type = 'mastodon'
      AND p.source = f.url
    LIMIT 1
  )               AS postSource
`;

export function openWritableMastodonDatabase(
  config: DbConfig,
): WritableFetchlinksDatabase {
  return openWritableFetchlinksDatabase(config);
}

export function withWritableMastodonDatabase<T>(
  config: DbConfig,
  callback: (database: WritableFetchlinksDatabase) => T,
): T {
  return withWritableFetchlinksDatabase(config, callback);
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
      `SELECT ${SELECT_COLUMNS} FROM data.mastodon_follows f ` +
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

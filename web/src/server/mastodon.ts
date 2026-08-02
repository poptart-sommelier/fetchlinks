import type {
  MastodonFollow,
  MastodonFollowsSnapshot,
} from "../models/mastodon-follows";
import { utcIso, type SqlClient } from "./sql";

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
  f.instance_name           AS "instanceName",
  f.account_id              AS "accountId",
  f.acct                    AS acct,
  f.display_name            AS "displayName",
  f.url                     AS url,
  ${utcIso("f.synced_at")}  AS "syncedAt",
  ${utcIso(`(
    SELECT MAX(p.posted_at) FROM content.posts p
    WHERE p.source_type = 'mastodon'
      AND p.source = f.url
  )`)}                      AS "latestPostAt",
  (
    SELECT p.source FROM content.posts p
    WHERE p.source_type = 'mastodon'
      AND p.source = f.url
    LIMIT 1
  )                         AS "postSource"
`;

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

export async function getMastodonFollows(
  sql: SqlClient,
): Promise<MastodonFollowsSnapshot> {
  const rows = await sql.query<MastodonFollowRow>(
    `SELECT ${SELECT_COLUMNS} FROM content.mastodon_follows f ` +
      "ORDER BY f.instance_name ASC, LOWER(f.acct) ASC",
  );
  const follows = rows.map(rowToFollow);
  const lastSyncedAt = follows.reduce<string | null>((latest, follow) => {
    if (!latest || follow.syncedAt > latest) return follow.syncedAt;
    return latest;
  }, null);

  return { follows, lastSyncedAt };
}

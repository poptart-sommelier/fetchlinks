import type {
  BlueskyFollow,
  BlueskyFollowsSnapshot,
} from "../models/bluesky-follows";
import { utcIso, type SqlClient } from "./sql";

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
  f.did                     AS did,
  f.handle                  AS handle,
  f.display_name            AS "displayName",
  ${utcIso("f.synced_at")}  AS "syncedAt",
  ${utcIso(`(
    SELECT MAX(p.posted_at) FROM content.posts p
    WHERE p.source_type = 'bluesky'
      AND LOWER(p.source) = '${BLUESKY_PROFILE_PREFIX}' || LOWER(f.handle)
  )`)}                      AS "latestPostAt",
  (
    SELECT p.source FROM content.posts p
    WHERE p.source_type = 'bluesky'
      AND LOWER(p.source) = '${BLUESKY_PROFILE_PREFIX}' || LOWER(f.handle)
    LIMIT 1
  )                         AS "postSource"
`;

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

export async function getBlueskyFollows(
  sql: SqlClient,
): Promise<BlueskyFollowsSnapshot> {
  const rows = await sql.query<BlueskyFollowRow>(
    `SELECT ${SELECT_COLUMNS} FROM content.bluesky_follows f ORDER BY LOWER(f.handle) ASC`,
  );
  const follows = rows.map(rowToFollow);
  const lastSyncedAt = follows.reduce<string | null>((latest, follow) => {
    if (!latest || follow.syncedAt > latest) return follow.syncedAt;
    return latest;
  }, null);

  return { follows, lastSyncedAt };
}

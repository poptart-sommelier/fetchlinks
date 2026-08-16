import {
  composeTargetKey,
  lookupKey,
  type CurationTarget,
} from "./curation";
import type { SqlClient } from "./sql";

export type CatalogSource = {
  id: number;
  kind: "rss" | "subreddit";
  status: "active" | "disabled" | "removed";
  targetKey: string;
};

type CatalogSourceRow = {
  id: number;
  kind: CatalogSource["kind"];
  normalizedKey: string;
  enabled: boolean;
  deleted: boolean;
};

export async function getCatalogSourcesFor(
  sql: SqlClient,
  targets: readonly Pick<CurationTarget, "type" | "key">[],
): Promise<Map<string, CatalogSource>> {
  const rssKeys = channelKeysFor(targets, "rss");
  const subredditKeys = channelKeysFor(targets, "reddit");
  const result = new Map<string, CatalogSource>();

  if (rssKeys.length === 0 && subredditKeys.length === 0) {
    return result;
  }

  const rows = await sql.query<CatalogSourceRow>(
    `
      SELECT
        feed_id::int AS id,
        'rss'::text AS kind,
        normalized_url AS "normalizedKey",
        enabled,
        deleted_at IS NOT NULL AS deleted
      FROM catalog.rss_feeds
      WHERE normalized_url = ANY($1::text[])
      UNION ALL
      SELECT
        subreddit_id::int AS id,
        'subreddit'::text AS kind,
        normalized_name AS "normalizedKey",
        enabled,
        deleted_at IS NOT NULL AS deleted
      FROM catalog.subreddits
      WHERE normalized_name = ANY($2::text[])
    `,
    [rssKeys, subredditKeys],
  );

  for (const row of rows) {
    const sourceType = row.kind === "rss" ? "rss" : "reddit";
    const targetKey = composeTargetKey(sourceType, row.normalizedKey);

    result.set(lookupKey("channel", targetKey), {
      id: row.id,
      kind: row.kind,
      status: row.deleted ? "removed" : row.enabled ? "active" : "disabled",
      targetKey,
    });
  }

  return result;
}

export async function resolveCatalogSource(
  sql: SqlClient,
  target: Pick<CurationTarget, "type" | "key">,
): Promise<CatalogSource> {
  const source = (await getCatalogSourcesFor(sql, [target])).get(
    lookupKey(target.type, target.key),
  );

  if (!source) {
    throw new Error("That target is not an RSS feed or subreddit in the catalog.");
  }

  return source;
}

function channelKeysFor(
  targets: readonly Pick<CurationTarget, "type" | "key">[],
  sourceType: "rss" | "reddit",
): string[] {
  const prefix = `${sourceType}\u001f`;

  return [
    ...new Set(
      targets
        .filter(
          (target) =>
            target.type === "channel" &&
            target.key.startsWith(prefix) &&
            target.key.length > prefix.length,
        )
        .map((target) => target.key.slice(prefix.length)),
    ),
  ];
}

import type { SqlClient } from "./sql";

/**
 * Stable things the owner may manage from an article.
 *
 * Channels and actors include the source type in their key; `netsec` on Reddit
 * and the same text on Mastodon are unrelated identities.
 */
export type CurationTargetType = "post" | "channel" | "actor" | "domain";

export type CurationTarget = {
  type: CurationTargetType;
  key: string;
  label: string;
};

export type ThumbsDownState = {
  /** Number of distinct articles that prompted this feedback. */
  count: number;
  /** Which articles on the page carry their own active thumbs down. */
  activePostUniqueIds: string[];
};

const TARGET_TYPES: readonly CurationTargetType[] = [
  "post",
  "channel",
  "actor",
  "domain",
];

/**
 * A unit separator cannot occur in a URL, subreddit name, DID or domain, so one
 * source cannot forge another source's target by including the separator.
 */
export const KEY_SEPARATOR = "\u001f";

export function composeTargetKey(sourceType: string, key: string): string {
  return `${sourceType}${KEY_SEPARATOR}${key}`;
}

export function isCurationTargetType(
  value: unknown,
): value is CurationTargetType {
  return TARGET_TYPES.includes(value as CurationTargetType);
}

/**
 * Record one article's evidence. Repeating the same action is idempotent, while
 * another article may add another row for the same target.
 */
export async function setThumbsDown(
  sql: SqlClient,
  {
    postUniqueId,
    target,
  }: {
    postUniqueId: string;
    target: CurationTarget;
  },
): Promise<void> {
  assertTarget(target);

  if (!postUniqueId.trim()) {
    throw new Error("Thumbs-down evidence needs the article it came from.");
  }

  await sql.query(
    `
      INSERT INTO curation.thumbs_downs
        (post_unique_id, target_type, target_key, target_label)
      VALUES ($1, $2, $3, $4)
      ON CONFLICT ON CONSTRAINT thumbs_downs_evidence_identity DO UPDATE SET
        target_label = EXCLUDED.target_label,
        -- Touching copied legacy evidence turns it into a deliberate Manage
        -- action, so the dormant transition trigger no longer owns the row.
        legacy_rating_id = NULL,
        updated_at = now()
      RETURNING thumbs_down_id
    `,
    [postUniqueId, target.type, target.key, target.label],
  );
}

/** Clear this article's evidence without disturbing the target's other count. */
export async function clearThumbsDown(
  sql: SqlClient,
  {
    postUniqueId,
    target,
  }: {
    postUniqueId: string;
    target: Pick<CurationTarget, "type" | "key">;
  },
): Promise<void> {
  assertTarget(target);

  if (!postUniqueId.trim()) {
    throw new Error("Thumbs-down evidence needs the article it came from.");
  }

  await sql.query(
    `
      DELETE FROM curation.thumbs_downs
      WHERE post_unique_id = $1
        AND target_type = $2
        AND target_key = $3
      RETURNING thumbs_down_id
    `,
    [postUniqueId, target.type, target.key],
  );
}

/** Apply one explicit owner decision. Repeating it only refreshes the label. */
export async function setMute(
  sql: SqlClient,
  target: CurationTarget,
): Promise<void> {
  assertTarget(target);

  await sql.query(
    `
      INSERT INTO curation.mutes (target_type, target_key, target_label)
      VALUES ($1, $2, $3)
      ON CONFLICT ON CONSTRAINT mutes_target_identity DO UPDATE SET
        target_label = EXCLUDED.target_label
      RETURNING mute_id
    `,
    [target.type, target.key, target.label],
  );
}

/** Remove one explicit mute without changing its thumbs-down evidence. */
export async function clearMute(
  sql: SqlClient,
  target: Pick<CurationTarget, "type" | "key">,
): Promise<void> {
  assertTarget(target);

  await sql.query(
    `
      DELETE FROM curation.mutes
      WHERE target_type = $1
        AND target_key = $2
      RETURNING mute_id
    `,
    [target.type, target.key],
  );
}

type ThumbsDownRow = {
  targetType: CurationTargetType;
  targetKey: string;
  count: number;
  activePostUniqueIds: unknown;
};

type MuteRow = {
  targetType: CurationTargetType;
  targetKey: string;
};

/**
 * Counts and page-local pressed states in one owner-only query.
 *
 * The count spans retained evidence, including articles no longer stored. The
 * JSON aggregate returns only ids on the current page, which is all rendering
 * needs to decide whether each button is pressed.
 */
export async function getThumbsDownsFor(
  sql: SqlClient,
  targets: readonly Pick<CurationTarget, "type" | "key">[],
  pagePostUniqueIds: readonly string[],
): Promise<Map<string, ThumbsDownState>> {
  const result = new Map<string, ThumbsDownState>();

  if (targets.length === 0) {
    return result;
  }

  const rows = await sql.query<ThumbsDownRow>(
    `
      SELECT
        target_type AS "targetType",
        target_key AS "targetKey",
        count(*)::int AS "count",
        COALESCE(
          jsonb_agg(post_unique_id)
            FILTER (WHERE post_unique_id = ANY($3::text[])),
          '[]'::jsonb
        ) AS "activePostUniqueIds"
      FROM curation.thumbs_downs
      WHERE (target_type, target_key) IN (
        SELECT * FROM unnest($1::text[], $2::text[])
      )
      GROUP BY target_type, target_key
    `,
    [
      targets.map((target) => target.type),
      targets.map((target) => target.key),
      pagePostUniqueIds,
    ],
  );

  for (const row of rows) {
    result.set(lookupKey(row.targetType, row.targetKey), {
      count: row.count,
      activePostUniqueIds: asTextArray(row.activePostUniqueIds),
    });
  }

  return result;
}

/** The active decisions among the targets rendered on this owner page. */
export async function getMutesFor(
  sql: SqlClient,
  targets: readonly Pick<CurationTarget, "type" | "key">[],
): Promise<Set<string>> {
  const result = new Set<string>();

  if (targets.length === 0) {
    return result;
  }

  const rows = await sql.query<MuteRow>(
    `
      SELECT
        target_type AS "targetType",
        target_key AS "targetKey"
      FROM curation.mutes
      WHERE (target_type, target_key) IN (
        SELECT * FROM unnest($1::text[], $2::text[])
      )
    `,
    [
      targets.map((target) => target.type),
      targets.map((target) => target.key),
    ],
  );

  for (const row of rows) {
    result.add(lookupKey(row.targetType, row.targetKey));
  }

  return result;
}

export function lookupKey(type: CurationTargetType, key: string): string {
  return `${type}${KEY_SEPARATOR}${key}`;
}

function assertTarget(
  target: Pick<CurationTarget, "type" | "key">,
): void {
  if (!isCurationTargetType(target.type)) {
    throw new Error(`Unknown curation target type: ${target.type}`);
  }

  if (!target.key.trim()) {
    throw new Error("A curation target needs a key.");
  }
}

function asTextArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : [];
}

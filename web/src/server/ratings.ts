import type { SqlClient } from "./sql";

/**
 * Owner curation: what the site's one curator thinks of a post, a channel, an
 * account or a domain.
 *
 * Recording only. Nothing here hides anything from anyone; deciding to act on
 * a score is a separate, later, deliberately manual step.
 */

export type RatingTargetType = "post" | "channel" | "actor" | "domain";

export type RatingVerdict = "good" | "noise";

export type RatingTarget = {
  type: RatingTargetType;
  key: string;
  label: string;
};

export type TargetScore = {
  type: RatingTargetType;
  key: string;
  good: number;
  noise: number;
  /** 0-100, smoothed. See `scoreOf`. */
  score: number;
};

const TARGET_TYPES: readonly RatingTargetType[] = [
  "post",
  "channel",
  "actor",
  "domain",
];

const VERDICTS: readonly RatingVerdict[] = ["good", "noise"];

/**
 * Channels and actors are only identified by a source type together with a
 * key: `netsec` means one thing on Reddit and nothing at all on Bluesky. The
 * separator is a unit separator because it cannot occur in a URL, a subreddit
 * name, a DID or a domain, so no key can forge a different one by containing
 * it.
 */
export const KEY_SEPARATOR = "\u001f";

export function composeTargetKey(sourceType: string, key: string): string {
  return `${sourceType}${KEY_SEPARATOR}${key}`;
}

/**
 * The public quality number, 0-100:
 *
 *     100 * (good + 2) / (good + noise + 4)
 *
 * The two notional ratings on each side keep a single click from producing 0
 * or 100. One Noise rating on a fresh source reads as 40, not as a verdict.
 */
export function scoreOf(good: number, noise: number): number {
  return Math.round((100 * (good + 2)) / (good + noise + 4));
}

export function isRatingTargetType(value: unknown): value is RatingTargetType {
  return TARGET_TYPES.includes(value as RatingTargetType);
}

export function isRatingVerdict(value: unknown): value is RatingVerdict {
  return VERDICTS.includes(value as RatingVerdict);
}

/**
 * Record or change one verdict. Re-rating a target the same way is not an
 * error and not a second rating: there is one curator, so there is one opinion
 * per target.
 */
export async function rateTarget(
  sql: SqlClient,
  {
    target,
    verdict,
    postUniqueId = "",
  }: {
    target: RatingTarget;
    verdict: RatingVerdict;
    postUniqueId?: string;
  },
): Promise<void> {
  if (!isRatingTargetType(target.type)) {
    throw new Error(`Unknown rating target type: ${target.type}`);
  }

  if (!isRatingVerdict(verdict)) {
    throw new Error(`Unknown verdict: ${verdict}`);
  }

  if (!target.key.trim()) {
    throw new Error("A rating target needs a key.");
  }

  await sql.query(
    `
      INSERT INTO curation.ratings
        (target_type, target_key, target_label, verdict, post_unique_id)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT ON CONSTRAINT ratings_target_identity DO UPDATE SET
        verdict      = EXCLUDED.verdict,
        -- Refresh the label: a subreddit keeps its key but a feed's title
        -- changes, and the stale one would be what a review queue showed.
        target_label = EXCLUDED.target_label,
        updated_at   = now()
      RETURNING rating_id
    `,
    [target.type, target.key, target.label, verdict, postUniqueId],
  );
}

/** Return a target to neutral. Clearing something unrated is not an error. */
export async function clearRating(
  sql: SqlClient,
  target: Pick<RatingTarget, "type" | "key">,
): Promise<void> {
  if (!isRatingTargetType(target.type)) {
    throw new Error(`Unknown rating target type: ${target.type}`);
  }

  await sql.query(
    `
      DELETE FROM curation.ratings
      WHERE target_type = $1 AND target_key = $2
      RETURNING rating_id
    `,
    [target.type, target.key],
  );
}

type RatingRow = {
  targetType: RatingTargetType;
  targetKey: string;
  verdict: RatingVerdict;
};

/**
 * Every verdict the owner has already recorded for the given targets, so a
 * page of cards can show its own state without a query per card.
 */
export async function getRatingsFor(
  sql: SqlClient,
  targets: readonly Pick<RatingTarget, "type" | "key">[],
): Promise<Map<string, RatingVerdict>> {
  const verdicts = new Map<string, RatingVerdict>();

  if (targets.length === 0) {
    return verdicts;
  }

  const types = targets.map((target) => target.type);
  const keys = targets.map((target) => target.key);
  const rows = await sql.query<RatingRow>(
    `
      SELECT target_type AS "targetType", target_key AS "targetKey", verdict
      FROM curation.ratings
      WHERE (target_type, target_key) IN (
        SELECT * FROM unnest($1::text[], $2::text[])
      )
    `,
    [types, keys],
  );

  for (const row of rows) {
    verdicts.set(lookupKey(row.targetType, row.targetKey), row.verdict);
  }

  return verdicts;
}

/**
 * Counts and smoothed scores for the given targets.
 *
 * Every target carries exactly one verdict, so these counts are 0 or 1 today.
 * They are computed rather than assumed because the muting stage reads scores
 * across all of a target's evidence, and a score that is really a boolean in
 * disguise would have to be unpicked then.
 */
export async function getScoresFor(
  sql: SqlClient,
  targets: readonly Pick<RatingTarget, "type" | "key">[],
): Promise<Map<string, TargetScore>> {
  const scores = new Map<string, TargetScore>();

  if (targets.length === 0) {
    return scores;
  }

  const rows = await sql.query<{
    targetType: RatingTargetType;
    targetKey: string;
    good: number;
    noise: number;
  }>(
    `
      SELECT
        target_type AS "targetType",
        target_key  AS "targetKey",
        COUNT(*) FILTER (WHERE verdict = 'good')::int  AS good,
        COUNT(*) FILTER (WHERE verdict = 'noise')::int AS noise
      FROM curation.ratings
      WHERE (target_type, target_key) IN (
        SELECT * FROM unnest($1::text[], $2::text[])
      )
      GROUP BY target_type, target_key
    `,
    [targets.map((t) => t.type), targets.map((t) => t.key)],
  );

  for (const row of rows) {
    scores.set(lookupKey(row.targetType, row.targetKey), {
      type: row.targetType,
      key: row.targetKey,
      good: row.good,
      noise: row.noise,
      score: scoreOf(row.good, row.noise),
    });
  }

  return scores;
}

/** The key both maps above are read by. */
export function lookupKey(type: RatingTargetType, key: string): string {
  return `${type}${KEY_SEPARATOR}${key}`;
}

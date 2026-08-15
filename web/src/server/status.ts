import type {
  ContentFacts,
  DatabaseFacts,
  Health,
  HostFacts,
  JobName,
  JobStatus,
  QueueFacts,
  RunResult,
  RunSummary,
  SourceSummary,
  StripSlot,
  SubtaskSummary,
  SystemStatus,
} from "../models/system-status";
import { utcIso, type SqlClient } from "./sql";

/**
 * The status page's one read.
 *
 * Everything here is derived from `content.operation_runs`, which the Pi writes
 * and nothing else does. The page asks one question -- "is anything broken, and
 * if so what?" -- and the honest answer needs three separate clocks kept apart:
 * when a job ran, when its report reached the database, and now. Collapsing
 * them is how a page ends up claiming the Pi is offline when it is merely
 * between publishes.
 */

/**
 * Expected gap between runs, from the systemd timers in `deploy/systemd`.
 *
 * Grace covers each timer's `RandomizedDelaySec` plus the run's own duration.
 * These are duplicated from the unit files rather than read from them: the Pi
 * does not report its schedule, and a page that guessed the cadence from
 * observed history would call a genuinely stopped timer "normal" within a day.
 */
export const CADENCE: Record<
  JobName,
  { intervalSeconds: number; graceSeconds: number }
> = {
  collect: { intervalSeconds: 30 * 60, graceSeconds: 5 * 60 },
  publish: { intervalSeconds: 60 * 60, graceSeconds: 10 * 60 },
  "catalog-sync": { intervalSeconds: 60 * 60, graceSeconds: 10 * 60 },
  retention: { intervalSeconds: 7 * 24 * 60 * 60, graceSeconds: 60 * 60 },
};

const STRIP_HOURS = 24;
const RECENT_RUN_LIMIT = 25;

const RUN_COLUMNS = `
  job,
  ${utcIso("started_at")}  AS "startedAt",
  ${utcIso("finished_at")} AS "finishedAt",
  ${utcIso("reported_at")} AS "reportedAt",
  result,
  elapsed_ms::int          AS "elapsedMs",
  error_kind               AS "errorKind",
  error_message            AS "errorMessage"
`;

type RunRow = {
  job: string;
  startedAt: string;
  finishedAt: string | null;
  reportedAt: string;
  result: RunResult;
  elapsedMs: number | null;
  errorKind: string;
  errorMessage: string;
};

type LatestRunRow = RunRow & { details: unknown };

type MedianRow = { job: string; medianElapsedMs: number | null };

type ContentRow = {
  lastNewPostAt: string | null;
  postCount: number;
  failingFeedCount: number;
};

type DatabaseSizeRow = { newestBytes: string | null; oldestBytes: string | null };

export async function getSystemStatus(sql: SqlClient): Promise<SystemStatus> {
  const now = new Date();
  const [latestRows, windowRows, medianRows, recentRows, contentRow, sizeRow] =
    await Promise.all([
      selectLatestRuns(sql),
      selectRunWindow(sql),
      selectMedians(sql),
      selectRecentRuns(sql),
      selectContentFacts(sql),
      selectDatabaseSize(sql),
    ]);

  const latest = new Map(latestRows.map((row) => [row.job, row]));
  const medians = new Map(medianRows.map((row) => [row.job, row.medianElapsedMs]));
  const publishRow = latest.get("publish") ?? null;

  // Every job is judged against `now`, except collection: its facts only reach
  // the database when the Publisher next runs, so measuring it against `now`
  // would call it late for most of every hour. It is judged against the
  // Publisher's own newest heartbeat instead.
  const publish = buildJobStatus("publish", publishRow, windowRows, medians, now, now);
  const reportingClock = publishRow ? new Date(publishRow.startedAt) : null;
  const collect = buildCollectStatus(
    latest.get("collect") ?? null,
    windowRows,
    medians,
    now,
    publish,
    reportingClock,
  );

  const publishDetails = asObject(publishRow?.details);
  const collectDetails = asObject(latest.get("collect")?.details);

  return {
    observedAt: now.toISOString(),
    collect,
    publish,
    catalogSync: buildJobStatus(
      "catalog-sync",
      latest.get("catalog-sync") ?? null,
      windowRows,
      medians,
      now,
      now,
    ),
    retention: buildJobStatus(
      "retention",
      latest.get("retention") ?? null,
      windowRows,
      medians,
      now,
      now,
    ),
    sources: readSources(collectDetails),
    subtasks: readSubtasks(collectDetails),
    queue: readQueue(publishDetails),
    host: readHost(publishDetails),
    database: readDatabaseSize(sizeRow),
    content: readContent(contentRow, publishRow, now),
    recentRuns: recentRows.map(toRunSummary),
    failingFeedCount: contentRow?.failingFeedCount ?? 0,
  };
}

// --- queries ---------------------------------------------------------------

async function selectLatestRuns(sql: SqlClient): Promise<LatestRunRow[]> {
  return sql.query<LatestRunRow>(`
    SELECT DISTINCT ON (job) ${RUN_COLUMNS}, details
    FROM content.operation_runs
    ORDER BY job, started_at DESC, run_id DESC
  `);
}

/**
 * Runs inside the strip window. Fetched without `details`, which is by far the
 * largest column and is only needed for the newest run of each job.
 */
async function selectRunWindow(sql: SqlClient): Promise<RunRow[]> {
  return sql.query<RunRow>(
    `
      SELECT ${RUN_COLUMNS}
      FROM content.operation_runs
      WHERE started_at >= now() - make_interval(hours => $1)
      ORDER BY started_at ASC
    `,
    [STRIP_HOURS],
  );
}

/**
 * Seven-day median duration per job.
 *
 * A median rather than a mean because one 20-minute run behind a dead network
 * would drag an average for a week. Runs still in flight are excluded: their
 * duration is not yet a fact.
 */
async function selectMedians(sql: SqlClient): Promise<MedianRow[]> {
  return sql.query<MedianRow>(`
    SELECT job,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_ms)::int
             AS "medianElapsedMs"
    FROM content.operation_runs
    WHERE started_at >= now() - interval '7 days'
      AND elapsed_ms IS NOT NULL
    GROUP BY job
  `);
}

async function selectRecentRuns(sql: SqlClient): Promise<RunRow[]> {
  return sql.query<RunRow>(
    `
      SELECT ${RUN_COLUMNS}
      FROM content.operation_runs
      ORDER BY started_at DESC, run_id DESC
      LIMIT $1
    `,
    [RECENT_RUN_LIMIT],
  );
}

async function selectContentFacts(sql: SqlClient): Promise<ContentRow | null> {
  const rows = await sql.query<ContentRow>(`
    SELECT
      ${utcIso("(SELECT max(first_seen_at) FROM content.posts)")}
        AS "lastNewPostAt",
      (SELECT count(*)::int FROM content.posts) AS "postCount",
      (SELECT count(*)::int
         FROM content.rss_feed_health h
         JOIN catalog.rss_feeds f ON f.normalized_url = h.normalized_url
        WHERE h.consecutive_failures > 0
          AND f.enabled
          AND f.deleted_at IS NULL) AS "failingFeedCount"
  `);
  return rows[0] ?? null;
}

/**
 * The production database's logical size, as the Publisher measured it, newest
 * and seven days ago.
 *
 * Read from the run history rather than by calling `pg_database_size` here:
 * this query runs on whichever database serves the request, which for a preview
 * deployment is the development branch. The number that matters is the one the
 * Pi reports about production.
 */
async function selectDatabaseSize(sql: SqlClient): Promise<DatabaseSizeRow | null> {
  const rows = await sql.query<DatabaseSizeRow>(`
    WITH sized AS (
      SELECT started_at, details->>'database_bytes' AS bytes
      FROM content.operation_runs
      WHERE job = 'publish'
        AND started_at >= now() - interval '7 days'
        AND jsonb_typeof(details->'database_bytes') = 'number'
    )
    SELECT
      (SELECT bytes FROM sized ORDER BY started_at DESC LIMIT 1) AS "newestBytes",
      (SELECT bytes FROM sized ORDER BY started_at ASC  LIMIT 1) AS "oldestBytes"
  `);
  return rows[0] ?? null;
}

// --- derivation ------------------------------------------------------------

/**
 * Healthy until one run is missed, delayed after one, stopped after two.
 *
 * Two missed runs rather than one before saying "stopped" because a single
 * miss is what a reboot, a slow network or a long run looks like, and a page
 * that cries wolf on the first one stops being read.
 */
export function deriveHealth(
  ageSeconds: number | null,
  intervalSeconds: number,
  graceSeconds: number,
): Health {
  if (ageSeconds === null) return "unknown";
  if (ageSeconds <= intervalSeconds + graceSeconds) return "healthy";
  if (ageSeconds <= intervalSeconds * 2 + graceSeconds) return "delayed";
  return "stopped";
}

/**
 * One cell per expected run over the last 24 hours, oldest first.
 *
 * Built from expected slots rather than from the runs that exist, because the
 * cell that matters most is the one with no run in it. A list of what happened
 * cannot show what did not.
 */
export function buildStrip(
  runs: readonly RunSummary[],
  intervalSeconds: number,
  now: Date,
): StripSlot[] {
  const slotMs = intervalSeconds * 1000;
  const slotCount = Math.max(1, Math.round((STRIP_HOURS * 3600 * 1000) / slotMs));
  // Anchored to now and stepped backwards, so the newest slot is the one in
  // progress rather than an arbitrary wall-clock boundary.
  const end = now.getTime();
  const slots: StripSlot[] = [];

  for (let index = slotCount - 1; index >= 0; index -= 1) {
    const startsAt = end - (index + 1) * slotMs;
    const endsAt = startsAt + slotMs;
    const inSlot = runs.filter((run) => {
      const at = new Date(run.startedAt).getTime();
      return at >= startsAt && at < endsAt;
    });

    slots.push({
      startsAt: new Date(startsAt).toISOString(),
      state: inSlot.length === 0 ? "missing" : worstResult(inSlot),
    });
  }

  return slots;
}

const RESULT_SEVERITY: Record<RunResult, number> = {
  ok: 0,
  running: 1,
  partial: 2,
  failed: 3,
};

function worstResult(runs: readonly RunSummary[]): RunResult {
  return runs.reduce<RunResult>(
    (worst, run) =>
      RESULT_SEVERITY[run.result] > RESULT_SEVERITY[worst] ? run.result : worst,
    "ok",
  );
}

function buildJobStatus(
  job: JobName,
  row: RunRow | null,
  windowRows: readonly RunRow[],
  medians: ReadonlyMap<string, number | null>,
  now: Date,
  judgedAgainst: Date,
): JobStatus {
  const { intervalSeconds, graceSeconds } = CADENCE[job];
  const latest = row ? toRunSummary(row) : null;
  const ageSeconds = latest
    ? secondsBetween(new Date(latest.startedAt), judgedAgainst)
    : null;

  return {
    job,
    health: deriveHealth(ageSeconds, intervalSeconds, graceSeconds),
    latest,
    ageSeconds,
    intervalSeconds,
    medianElapsedMs: medians.get(job) ?? null,
    strip: buildStrip(
      windowRows.filter((run) => run.job === job).map(toRunSummary),
      intervalSeconds,
      now,
    ),
  };
}

/**
 * Collection, judged against the Publisher's clock rather than ours.
 *
 * With the reporting path stale we cannot tell a stopped collector from a
 * working one whose reports have not been carried over yet, so the honest
 * answer is `unknown`. Reporting "stopped" there would send the reader looking
 * at the collector when the problem is the publisher, which is exactly the
 * wasted evening this page exists to prevent.
 */
function buildCollectStatus(
  row: RunRow | null,
  windowRows: readonly RunRow[],
  medians: ReadonlyMap<string, number | null>,
  now: Date,
  publish: JobStatus,
  reportingClock: Date | null,
): JobStatus {
  const status = buildJobStatus(
    "collect",
    row,
    windowRows,
    medians,
    now,
    reportingClock ?? now,
  );

  if (publish.health !== "healthy") {
    return { ...status, health: "unknown" };
  }

  return status;
}

function secondsBetween(from: Date, to: Date): number {
  return Math.max(0, Math.round((to.getTime() - from.getTime()) / 1000));
}

function toRunSummary(row: RunRow): RunSummary {
  return {
    job: row.job,
    startedAt: row.startedAt,
    finishedAt: row.finishedAt,
    reportedAt: row.reportedAt,
    result: row.result,
    elapsedMs: row.elapsedMs,
    errorKind: row.errorKind,
    errorMessage: row.errorMessage,
  };
}

// --- details readers -------------------------------------------------------
//
// `details` is a JSON column, so nothing about its shape is guaranteed by the
// database. Each reader takes what it recognizes and defaults the rest, so a
// run written by an older Pi renders with gaps rather than crashing the page
// that would have explained the problem.

function asObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asCount(value: unknown): number {
  return asNumber(value) ?? 0;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function readSources(details: Record<string, unknown>): SourceSummary[] {
  const raw = Array.isArray(details.sources) ? details.sources : [];

  return raw.map((entry) => {
    const source = asObject(entry);
    return {
      sourceType: asText(source.source_type),
      result: asText(source.result) || "unknown",
      channelsAttempted: asCount(source.channels_attempted),
      channelsSucceeded: asCount(source.channels_succeeded),
      channelsFailed: asCount(source.channels_failed),
      itemsCollected: asCount(source.items_collected),
      elapsedMs: asCount(source.elapsed_ms),
      errorKind: asText(source.error_kind),
      errorMessage: asText(source.error_message),
    };
  });
}

export function readSubtasks(details: Record<string, unknown>): SubtaskSummary[] {
  const raw = Array.isArray(details.subtasks) ? details.subtasks : [];

  return raw.map((entry) => {
    const subtask = asObject(entry);
    return {
      name: asText(subtask.name),
      scope: asText(subtask.scope),
      result: asText(subtask.result) || "unknown",
      elapsedMs: asCount(subtask.elapsed_ms),
      errorKind: asText(subtask.error_kind),
      errorMessage: asText(subtask.error_message),
    };
  });
}

function readQueue(details: Record<string, unknown>): QueueFacts {
  // After the drain when we have it: a queue that emptied is the useful fact,
  // and the "before" figure is only interesting when the run never finished.
  const queue = asObject(details.queue_after ?? details.queue_before);
  const counts = asObject(queue.counts);

  return {
    ready: asCount(counts.ready),
    processing: asCount(counts.processing),
    published: asCount(counts.published),
    // The spool calls this stage `failed`; the page calls it quarantined,
    // which is what actually happened to the batch.
    quarantined: asCount(counts.failed),
    oldestOutstandingAgeSeconds: asNumber(queue.oldest_outstanding_age_seconds),
    spoolBytes: asNumber(queue.disk_bytes),
  };
}

function readHost(details: Record<string, unknown>): HostFacts {
  return {
    uptimeSeconds: asNumber(details.host_uptime_seconds),
    bootId: typeof details.host_boot_id === "string" ? details.host_boot_id : null,
    diskFreeBytes: asNumber(details.disk_free_bytes),
  };
}

function readDatabaseSize(row: DatabaseSizeRow | null): DatabaseFacts {
  const newest = row?.newestBytes ? Number(row.newestBytes) : null;
  const oldest = row?.oldestBytes ? Number(row.oldestBytes) : null;
  const usable = newest !== null && Number.isFinite(newest) ? newest : null;
  const baseline = oldest !== null && Number.isFinite(oldest) ? oldest : null;

  return {
    bytes: usable,
    // Null rather than zero when there is only one measurement: "no change" and
    // "nothing to compare with" are different answers.
    changeBytes:
      usable !== null && baseline !== null && usable !== baseline
        ? usable - baseline
        : null,
  };
}

function readContent(
  row: ContentRow | null,
  publishRow: RunRow | null,
  now: Date,
): ContentFacts {
  const lastNewPostAt = row?.lastNewPostAt ?? null;
  const quietAfter = CADENCE.publish.intervalSeconds * 2;
  const age = lastNewPostAt
    ? secondsBetween(new Date(lastNewPostAt), now)
    : null;

  return {
    lastNewPostAt,
    // Only meaningful once something has published: with no history at all,
    // "quiet" would just be describing an empty database.
    quiet: publishRow !== null && (age === null || age > quietAfter),
    postCount: row?.postCount ?? 0,
  };
}

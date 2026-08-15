/**
 * What the Flightdeck status page reads.
 *
 * Deliberately a narrow, already-decided shape rather than raw rows. The
 * database stores a `details` JSON object per run whose contents differ by job;
 * everything the page draws is pulled out and validated in `server/status.ts`,
 * so a page component never reaches into untyped JSON and never has to decide
 * what a missing field means.
 */

export type IsoDateString = string;

/** Every scheduled job the page knows how to describe. */
export type JobName = "collect" | "publish" | "catalog-sync" | "retention";

/** The four states a run can be in, as recorded by the job itself. */
export type RunResult = "running" | "ok" | "partial" | "failed";

/**
 * How a job is doing right now.
 *
 * `unknown` is not a failure. It is what the Collector reads as when the
 * Publisher is stale: collection facts travel through the Publisher, so with
 * the reporting path down we genuinely do not know, and saying so is more
 * useful than guessing.
 */
export type Health = "healthy" | "delayed" | "stopped" | "unknown";

export type RunSummary = {
  job: JobName | string;
  startedAt: IsoDateString;
  finishedAt: IsoDateString | null;
  reportedAt: IsoDateString;
  result: RunResult;
  elapsedMs: number | null;
  errorKind: string;
  errorMessage: string;
};

/** One source's contribution to the latest collection run. */
export type SourceSummary = {
  sourceType: string;
  result: string;
  channelsAttempted: number;
  channelsSucceeded: number;
  channelsFailed: number;
  itemsCollected: number;
  elapsedMs: number;
  errorKind: string;
  errorMessage: string;
};

/** Work done alongside collection that can fail on its own. */
export type SubtaskSummary = {
  name: string;
  scope: string;
  result: string;
  elapsedMs: number;
  errorKind: string;
  errorMessage: string;
};

/**
 * One slot of the 24-hour strip.
 *
 * `missing` means no run started in a window where one was expected. That is
 * the single most important cell on the page: it is what a stopped timer looks
 * like, and it is invisible in a list that only shows the runs that happened.
 */
export type StripSlot = {
  startsAt: IsoDateString;
  state: RunResult | "missing";
};

export type JobStatus = {
  job: JobName;
  health: Health;
  /** Newest run, or null when the job has never reported. */
  latest: RunSummary | null;
  /** Seconds between the newest run and the clock this job is judged against. */
  ageSeconds: number | null;
  /** Expected gap between runs, from the systemd timer. */
  intervalSeconds: number;
  medianElapsedMs: number | null;
  strip: StripSlot[];
};

export type QueueFacts = {
  ready: number;
  processing: number;
  published: number;
  quarantined: number;
  oldestOutstandingAgeSeconds: number | null;
  spoolBytes: number | null;
};

export type HostFacts = {
  uptimeSeconds: number | null;
  bootId: string | null;
  diskFreeBytes: number | null;
};

export type DatabaseFacts = {
  /** Logical size of the production database, as the Publisher last measured
   * it. Not a share of Neon's allowance: that is per project across branches
   * and only Neon's dashboard can see it. */
  bytes: number | null;
  /** Change over the last seven days, or null without enough history. */
  changeBytes: number | null;
};

export type ContentFacts = {
  /** `max(content.posts.first_seen_at)`: when a new link last arrived. */
  lastNewPostAt: IsoDateString | null;
  /** True once nothing new has arrived for two Publisher intervals. Quiet is
   * not a fault: a healthy drain adds nothing when every link was already
   * stored. */
  quiet: boolean;
  postCount: number;
};

export type SystemStatus = {
  /** When the page read the database. Every age on the page is measured from
   * here, so a cached render cannot silently age. */
  observedAt: IsoDateString;
  collect: JobStatus;
  publish: JobStatus;
  catalogSync: JobStatus;
  retention: JobStatus;
  sources: SourceSummary[];
  subtasks: SubtaskSummary[];
  queue: QueueFacts;
  host: HostFacts;
  database: DatabaseFacts;
  content: ContentFacts;
  recentRuns: RunSummary[];
  /** Feeds whose last fetch failed, for the "one link away" detail. */
  failingFeedCount: number;
};

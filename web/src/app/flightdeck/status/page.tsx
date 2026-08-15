import Link from "next/link";

import { formatRelative } from "../../../lib/format-relative";
import {
  formatAge,
  formatByteChange,
  formatBytes,
  formatDuration,
  MISSING,
} from "../../../lib/format-facts";
import type {
  Health,
  JobStatus,
  RunSummary,
  SourceSummary,
  StripSlot,
  SubtaskSummary,
  SystemStatus,
} from "../../../models/system-status";
import { getSystemStatus } from "../../../server/status";
import { getSqlClient } from "../../../server/sql";

export const dynamic = "force-dynamic";

type LoadResult =
  | { status: "ready"; data: SystemStatus }
  | { status: "error" };

export default async function StatusPage() {
  return <StatusView result={await loadStatus()} />;
}

async function loadStatus(): Promise<LoadResult> {
  try {
    return { status: "ready", data: await getSystemStatus(getSqlClient(process.env)) };
  } catch {
    // The guide below still renders. A failed read is often the very thing the
    // reader came here to diagnose, so losing the instructions with the data
    // would be the worst possible moment to lose them.
    return { status: "error" };
  }
}

export function StatusView({ result }: { result: LoadResult }) {
  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/flightdeck">&larr; Admin</Link>
          </p>
          <h1>System status</h1>
        </div>
        {result.status === "ready" ? (
          <p className="status-observed" title={result.data.observedAt}>
            Read {formatRelative(result.data.observedAt) ?? "just now"}
            <br />
            <Link href="/flightdeck/status/guide">How to read this page</Link>
          </p>
        ) : null}
      </header>

      {result.status === "error" ? (
        <section className="state state-error" role="alert">
          <h2>Status data unavailable</h2>
          <p>
            The database could not be read, so nothing below it is known. That
            failure is itself a finding: if the site is otherwise working, the
            web app has a database problem rather than the Pi. The exception is
            in Vercel&rsquo;s runtime logs.
          </p>
          <p>
            {/* The guide is a separate, static page precisely so it still
                answers when this read does not. */}
            <Link href="/flightdeck/status/guide">
              How to read this page, and what to do next
            </Link>
          </p>
        </section>
      ) : (
        <StatusBody data={result.data} />
      )}
    </main>
  );
}

function StatusBody({ data }: { data: SystemStatus }) {
  return (
    <>
      <section aria-label="Headline status" className="status-card-grid">
        <JobHeadlineCard
          status={data.collect}
          title="Collector"
          subtitle="Every 30 minutes, on the Pi"
        />
        <JobHeadlineCard
          status={data.publish}
          title="Publisher"
          subtitle="Hourly, the only job with the database"
        />
        <LastPostCard data={data} />
        <QueueCard data={data} />
      </section>

      <section aria-label="Recent activity" className="status-strips">
        <StripPanel status={data.collect} title="Collection, last 24 hours" />
        <StripPanel status={data.publish} title="Publishing, last 24 hours" />
      </section>

      <SourcesPanel
        sources={data.sources}
        subtasks={data.subtasks}
        failingFeedCount={data.failingFeedCount}
      />

      <FactsPanel data={data} />

      <SecondaryJobsPanel data={data} />

      <RecentRunsPanel runs={data.recentRuns} />
    </>
  );
}

// --- run outcomes ----------------------------------------------------------

/**
 * Plain-English gloss for the four words a run can end with.
 *
 * These appear as bare words in the table and on each source, where they read
 * as jargon: "partial" in particular sounds like a fault, when for a collection
 * across several hundred feeds it is the ordinary case. The full explanation
 * lives in the guide; this is the version that reaches someone who is not going
 * to open it.
 */
const RESULT_MEANING: Record<string, string> = {
  ok: "Every part of the run succeeded.",
  partial:
    "Some parts succeeded and some did not \u2014 usually a few feeds timing out while the rest were fine. Normal unless the failed count climbs.",
  failed: "Nothing succeeded.",
  running:
    "Still going, or stopped before it could write down how it ended.",
  skipped: "Switched off in the configuration; does not count either way.",
};

function ResultWord({ result }: { result: string }) {
  return (
    <span
      className={`status-result status-result-${result}`}
      title={RESULT_MEANING[result] ?? undefined}
    >
      {result}
    </span>
  );
}

// --- headline cards --------------------------------------------------------

const HEALTH_LABEL: Record<Health, string> = {
  healthy: "Healthy",
  delayed: "Delayed",
  stopped: "Stopped",
  unknown: "Unknown",
};

// A glyph as well as a colour, because a status page read at a glance must not
// depend on distinguishing green from amber.
const HEALTH_GLYPH: Record<Health, string> = {
  healthy: "\u25CF",
  delayed: "\u25D0",
  stopped: "\u25A0",
  unknown: "?",
};

function HealthBadge({ health }: { health: Health }) {
  return (
    <span className={`status-badge status-badge-${health}`}>
      <span aria-hidden="true">{HEALTH_GLYPH[health]}</span>
      {HEALTH_LABEL[health]}
    </span>
  );
}

function JobHeadlineCard({
  status,
  title,
  subtitle,
}: {
  status: JobStatus;
  title: string;
  subtitle: string;
}) {
  return (
    <article className={`status-card status-card-${status.health}`}>
      <header className="status-card-header">
        <h2>{title}</h2>
        <HealthBadge health={status.health} />
      </header>
      <p className="status-card-lead">{describeJob(status, title)}</p>
      <dl className="status-card-facts">
        <div>
          <dt>Last run</dt>
          <dd title={status.latest?.startedAt ?? undefined}>
            {status.latest
              ? (formatRelative(status.latest.startedAt) ?? MISSING)
              : "never"}
          </dd>
        </div>
        <div>
          <dt>Took</dt>
          <dd>
            {formatDuration(status.latest?.elapsedMs)}
            <span className="status-card-median">
              {" "}
              (median {formatDuration(status.medianElapsedMs)})
            </span>
          </dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>
            {status.latest ? <ResultWord result={status.latest.result} /> : MISSING}
          </dd>
        </div>
      </dl>
      {status.latest && status.latest.result !== "ok" ? (
        <p className="status-card-note">{RESULT_MEANING[status.latest.result]}</p>
      ) : null}
      <p className="status-card-note">{subtitle}</p>
      {status.latest?.errorMessage ? (
        <p className="status-card-error">
          <strong>{status.latest.errorKind || "error"}:</strong>{" "}
          {status.latest.errorMessage}
        </p>
      ) : null}
    </article>
  );
}

/**
 * One sentence that says what to do next, not just what happened.
 *
 * The phrasing matters more than it looks. "Has not reported" is the only
 * honest thing to say about a stale heartbeat, because the Pi being off, its
 * network failing and the database refusing connections are indistinguishable
 * from this side.
 */
function describeJob(status: JobStatus, title: string): string {
  if (!status.latest) {
    return `${title} has never reported. Either it has not run yet or its reports are not arriving.`;
  }

  const age = formatAge(status.ageSeconds);

  switch (status.health) {
    case "healthy":
      return `Reported ${age} ago, within the expected gap.`;
    case "delayed":
      return `Last reported ${age} ago: one run missed. Often a reboot or a slow network.`;
    case "stopped":
      return `Has not reported for ${age}, which is more than two expected runs.`;
    case "unknown":
      return `Last collection reported ${age} before the Publisher's own last report, but the Publisher is stale, so this cannot be trusted.`;
  }
}

function LastPostCard({ data }: { data: SystemStatus }) {
  const { lastNewPostAt, quiet, postCount } = data.content;

  return (
    <article className={`status-card${quiet ? " status-card-quiet" : ""}`}>
      <header className="status-card-header">
        <h2>Last new post</h2>
        {quiet ? <span className="status-badge status-badge-quiet">Quiet</span> : null}
      </header>
      <p className="status-card-lead" title={lastNewPostAt ?? undefined}>
        {lastNewPostAt
          ? (formatRelative(lastNewPostAt) ?? MISSING)
          : "Nothing stored yet"}
      </p>
      <p className="status-card-note">
        {quiet
          ? "Nothing new for two publishing rounds. Not a fault on its own: a healthy run adds nothing when every link was already stored."
          : "New links are arriving."}
      </p>
      <dl className="status-card-facts">
        <div>
          <dt>Posts stored</dt>
          <dd>{postCount.toLocaleString("en-US")}</dd>
        </div>
      </dl>
    </article>
  );
}

function QueueCard({ data }: { data: SystemStatus }) {
  const { ready, processing, quarantined, oldestOutstandingAgeSeconds } = data.queue;
  const waiting = ready + processing;

  return (
    <article
      className={`status-card${quarantined > 0 ? " status-card-delayed" : ""}`}
    >
      <header className="status-card-header">
        <h2>Queue on the Pi</h2>
        {quarantined > 0 ? (
          <span className="status-badge status-badge-delayed">
            {quarantined} quarantined
          </span>
        ) : null}
      </header>
      <p className="status-card-lead">
        {waiting === 0 ? "Empty" : `${waiting} batch${waiting === 1 ? "" : "es"} waiting`}
      </p>
      <dl className="status-card-facts">
        <div>
          <dt>Oldest waiting</dt>
          <dd>{formatAge(oldestOutstandingAgeSeconds)}</dd>
        </div>
        <div>
          <dt>Spool size</dt>
          <dd>{formatBytes(data.queue.spoolBytes)}</dd>
        </div>
      </dl>
      <p className="status-card-note">
        As the Publisher last saw it. Posts wait here safely while the database
        is unreachable.
      </p>
    </article>
  );
}

// --- 24-hour strip ---------------------------------------------------------

const SLOT_LABEL: Record<StripSlot["state"], string> = {
  ok: "succeeded",
  partial: "partly succeeded",
  failed: "failed",
  running: "still running",
  missing: "no run",
};

const SLOT_GLYPH: Record<StripSlot["state"], string> = {
  ok: "\u2713",
  partial: "~",
  failed: "\u2717",
  running: "\u00B7",
  missing: "\u2013",
};

function StripPanel({ status, title }: { status: JobStatus; title: string }) {
  const missing = status.strip.filter((slot) => slot.state === "missing").length;

  return (
    <section aria-label={title} className="status-strip-panel">
      <header className="status-strip-header">
        <h2>{title}</h2>
        <p className="status-strip-summary">
          {status.strip.length - missing} of {status.strip.length} expected runs
          reported
        </p>
      </header>
      <ol className="status-strip">
        {status.strip.map((slot) => (
          <li
            className={`status-slot status-slot-${slot.state}`}
            key={slot.startsAt}
            title={`${formatClock(slot.startsAt)}: ${SLOT_LABEL[slot.state]}`}
          >
            <span aria-hidden="true">{SLOT_GLYPH[slot.state]}</span>
            <span className="visually-hidden">
              {formatClock(slot.startsAt)}: {SLOT_LABEL[slot.state]}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function formatClock(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toISOString().slice(11, 16) + " UTC";
}

// --- sources ---------------------------------------------------------------

const SOURCE_LABEL: Record<string, string> = {
  rss: "RSS",
  reddit: "Reddit",
  bluesky: "Bluesky",
  mastodon: "Mastodon",
};

function SourcesPanel({
  sources,
  subtasks,
  failingFeedCount,
}: {
  sources: SourceSummary[];
  subtasks: SubtaskSummary[];
  failingFeedCount: number;
}) {
  return (
    <section aria-label="Sources" className="status-panel">
      <h2>Sources, from the latest collection</h2>
      {sources.length === 0 ? (
        <p className="status-empty">
          No collection run has reported yet, so there is nothing to break down.
        </p>
      ) : (
        <div className="status-source-grid">
          {sources.map((source) => (
            <SourceCard
              failingFeedCount={failingFeedCount}
              key={source.sourceType}
              source={source}
            />
          ))}
        </div>
      )}
      {subtasks.length > 0 ? (
        <ul className="status-subtasks">
          {subtasks.map((subtask) => (
            <li key={`${subtask.name}:${subtask.scope}`}>
              <strong>{subtask.name}</strong>
              {subtask.scope ? ` (${subtask.scope})` : ""} &mdash; {subtask.result}
              {subtask.errorMessage ? `: ${subtask.errorMessage}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function SourceCard({
  source,
  failingFeedCount,
}: {
  source: SourceSummary;
  failingFeedCount: number;
}) {
  const label = SOURCE_LABEL[source.sourceType] ?? source.sourceType;

  return (
    <article className={`status-source status-source-${source.result}`}>
      <header className="status-source-header">
        <h3>{label}</h3>
        <span className="status-source-result" title={RESULT_MEANING[source.result]}>
          {source.result}
        </span>
      </header>
      <p className="status-source-counts">
        {source.result === "skipped" ? (
          "Switched off in the configuration."
        ) : (
          <>
            {source.itemsCollected} item{source.itemsCollected === 1 ? "" : "s"} from{" "}
            {source.channelsSucceeded}/{source.channelsAttempted} channels in{" "}
            {formatDuration(source.elapsedMs)}
          </>
        )}
      </p>
      {source.channelsFailed > 0 ? (
        <p className="status-source-note">
          {source.channelsFailed} failed
          {source.errorKind ? ` (${source.errorKind})` : ""}
          {source.sourceType === "rss" && failingFeedCount > 0 ? (
            <>
              {" "}
              &mdash; <Link href="/flightdeck/feeds">see which feeds</Link>
            </>
          ) : null}
        </p>
      ) : null}
      {source.errorMessage ? (
        <p className="status-source-error">{source.errorMessage}</p>
      ) : null}
    </article>
  );
}

// --- facts -----------------------------------------------------------------

function FactsPanel({ data }: { data: SystemStatus }) {
  return (
    <section aria-label="Machine and database" className="status-panel">
      <h2>Machine and database</h2>
      <dl className="status-facts">
        <div>
          <dt>Free disk on the Pi</dt>
          <dd>{formatBytes(data.host.diskFreeBytes)}</dd>
        </div>
        <div>
          <dt>Pi uptime</dt>
          <dd>
            {data.host.uptimeSeconds === null
              ? MISSING
              : formatAge(data.host.uptimeSeconds)}
          </dd>
        </div>
        <div>
          <dt>Boot identity</dt>
          <dd className="status-fact-mono">
            {data.host.bootId ? data.host.bootId.slice(0, 8) : MISSING}
          </dd>
        </div>
        <div>
          <dt>Production database</dt>
          <dd>{formatBytes(data.database.bytes)}</dd>
        </div>
        <div>
          <dt>Change over 7 days</dt>
          <dd>{formatByteChange(data.database.changeBytes)}</dd>
        </div>
        <div>
          <dt>Quarantined batches</dt>
          <dd>{data.queue.quarantined}</dd>
        </div>
      </dl>
      <p className="status-panel-note">
        The database figure is production&rsquo;s own logical size, not a share
        of Neon&rsquo;s allowance: that is counted per project across branches
        and only Neon&rsquo;s dashboard can see it. A boot identity that changed
        since yesterday means the Pi restarted.
      </p>
    </section>
  );
}

// --- secondary jobs and history --------------------------------------------

function SecondaryJobsPanel({ data }: { data: SystemStatus }) {
  const jobs: Array<{ status: JobStatus; title: string; note: string }> = [
    {
      status: data.catalogSync,
      title: "Catalog sync",
      note: "Sends the feed list to the Pi after each publish. A failure leaves the Pi on its last known list, which is stale rather than stopped.",
    },
    {
      status: data.retention,
      title: "Retention",
      note: "Weekly. Deletes posts past the keep window and trims this page's own history.",
    },
  ];

  return (
    <section aria-label="Secondary jobs" className="status-panel">
      <h2>Secondary jobs</h2>
      <div className="status-secondary-grid">
        {jobs.map(({ status, title, note }) => (
          <article className="status-secondary" key={title}>
            <header className="status-card-header">
              <h3>{title}</h3>
              <HealthBadge health={status.health} />
            </header>
            <p className="status-card-lead" title={status.latest?.startedAt}>
              {status.latest
                ? `${status.latest.result}, ${formatRelative(status.latest.startedAt) ?? MISSING}`
                : "Never reported"}
            </p>
            {status.latest?.errorMessage ? (
              <p className="status-card-error">{status.latest.errorMessage}</p>
            ) : null}
            <p className="status-card-note">{note}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function RecentRunsPanel({ runs }: { runs: RunSummary[] }) {
  if (runs.length === 0) {
    return (
      <section aria-label="Recent runs" className="status-panel">
        <h2>Recent runs</h2>
        <p className="status-empty">
          No runs have been recorded. If the Pi has been updated recently, the
          first report arrives with the next publish.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Recent runs" className="status-panel">
      <h2>Recent runs</h2>
      <p className="status-panel-note status-legend">
        <strong>ok</strong> everything worked &middot; <strong>partial</strong>{" "}
        some parts worked and some did not &middot; <strong>failed</strong>{" "}
        nothing worked &middot; <strong>running</strong> still going, or stopped
        before it could say how it ended. <Link href="/flightdeck/status/guide">More</Link>
      </p>
      <div className="status-table-scroll">
        <table className="status-table">
          <thead>
            <tr>
              <th scope="col">Job</th>
              <th scope="col">Started</th>
              <th scope="col">Result</th>
              <th scope="col">Took</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={`${run.job}:${run.startedAt}:${run.reportedAt}`}>
                <td>{run.job}</td>
                <td title={run.startedAt}>
                  {formatRelative(run.startedAt) ?? MISSING}
                </td>
                <td>
                  <ResultWord result={run.result} />
                </td>
                <td>{formatDuration(run.elapsedMs)}</td>
                <td className="status-table-detail">
                  {run.errorMessage
                    ? `${run.errorKind || "error"}: ${run.errorMessage}`
                    : MISSING}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

import { describe, expect, it } from "vitest";

import type { RunSummary } from "../models/system-status";
import { buildStrip, deriveHealth, getSystemStatus, readSources } from "./status";
import { describePostgres, usePostgres } from "./test-support/postgres";

describe("deriveHealth", () => {
  const interval = 3600;
  const grace = 600;

  it("is healthy inside one expected gap", () => {
    expect(deriveHealth(0, interval, grace)).toBe("healthy");
    expect(deriveHealth(4200, interval, grace)).toBe("healthy");
  });

  // One miss is a reboot or a slow network. Calling that "stopped" is how a
  // status page trains its reader to ignore it.
  it("is delayed after one missed run", () => {
    expect(deriveHealth(4201, interval, grace)).toBe("delayed");
    expect(deriveHealth(7800, interval, grace)).toBe("delayed");
  });

  it("is stopped after two", () => {
    expect(deriveHealth(7801, interval, grace)).toBe("stopped");
  });

  it("is unknown with nothing to measure", () => {
    expect(deriveHealth(null, interval, grace)).toBe("unknown");
  });
});

describe("buildStrip", () => {
  const now = new Date("2026-01-02T12:00:00Z");

  function run(startedAt: string, result: RunSummary["result"]): RunSummary {
    return {
      job: "publish",
      startedAt,
      finishedAt: startedAt,
      reportedAt: startedAt,
      result,
      elapsedMs: 1000,
      errorKind: "",
      errorMessage: "",
    };
  }

  it("covers 24 hours at the job's cadence", () => {
    expect(buildStrip([], 3600, now)).toHaveLength(24);
    expect(buildStrip([], 1800, now)).toHaveLength(48);
  });

  // The whole reason the strip is built from expected slots rather than from
  // the runs that exist: a stopped timer leaves no rows at all.
  it("shows a slot with no run as missing", () => {
    const strip = buildStrip([], 3600, now);
    expect(strip.every((slot) => slot.state === "missing")).toBe(true);
  });

  it("places a run in the slot it started in", () => {
    const strip = buildStrip([run("2026-01-02T11:30:00Z", "ok")], 3600, now);
    expect(strip[strip.length - 1].state).toBe("ok");
    expect(strip[strip.length - 2].state).toBe("missing");
  });

  it("reports the worst outcome when a slot holds several runs", () => {
    const strip = buildStrip(
      [run("2026-01-02T11:10:00Z", "ok"), run("2026-01-02T11:40:00Z", "failed")],
      3600,
      now,
    );
    expect(strip[strip.length - 1].state).toBe("failed");
  });

  it("ignores runs older than the window", () => {
    const strip = buildStrip([run("2026-01-01T00:00:00Z", "ok")], 3600, now);
    expect(strip.every((slot) => slot.state === "missing")).toBe(true);
  });
});

describe("readSources", () => {
  // `details` is a JSON column, so an older Pi can send a shape this build has
  // never seen. Rendering with gaps beats crashing the page that explains the
  // problem.
  it("survives details it does not recognize", () => {
    expect(readSources({})).toEqual([]);
    expect(readSources({ sources: "nonsense" })).toEqual([]);
    expect(readSources({ sources: [{}] })).toEqual([
      {
        sourceType: "",
        result: "unknown",
        channelsAttempted: 0,
        channelsSucceeded: 0,
        channelsFailed: 0,
        itemsCollected: 0,
        elapsedMs: 0,
        errorKind: "",
        errorMessage: "",
      },
    ]);
  });
});

describePostgres("system status", () => {
  const pg = usePostgres();

  async function addRun(
    job: string,
    options: {
      minutesAgo: number;
      result?: string;
      elapsedMs?: number | null;
      details?: unknown;
      errorKind?: string;
      errorMessage?: string;
      runKey?: string;
    },
  ): Promise<void> {
    const finished = options.result === "running" ? null : "now()";
    await pg.exec(
      `INSERT INTO content.operation_runs
         (job, run_key, started_at, finished_at, result, elapsed_ms,
          error_kind, error_message, details)
       VALUES ($1, $2, now() - make_interval(mins => $3), ${finished ?? "NULL"},
               $4, $5, $6, $7, $8::jsonb)`,
      [
        job,
        options.runKey ?? "",
        options.minutesAgo,
        options.result ?? "ok",
        options.elapsedMs === undefined ? 1000 : options.elapsedMs,
        options.errorKind ?? "",
        options.errorMessage ?? "",
        JSON.stringify(options.details ?? {}),
      ],
    );
  }

  it("reads nothing at all without pretending otherwise", async () => {
    const status = await getSystemStatus(pg.sql);

    expect(status.collect.latest).toBeNull();
    expect(status.publish.latest).toBeNull();
    expect(status.publish.health).toBe("unknown");
    expect(status.recentRuns).toEqual([]);
    expect(status.content.lastNewPostAt).toBeNull();
    // Nothing has ever published, so "quiet" would be describing an empty
    // database rather than a symptom.
    expect(status.content.quiet).toBe(false);
  });

  it("reports a healthy pipeline", async () => {
    await addRun("publish", {
      minutesAgo: 10,
      details: {
        queue_after: { counts: { ready: 0, failed: 0 }, disk_bytes: 4096 },
        disk_free_bytes: 8_000_000_000,
        host_uptime_seconds: 3600,
        host_boot_id: "abcdef01-2345",
        database_bytes: 100_000_000,
      },
    });
    await addRun("collect", { minutesAgo: 25, runKey: "batch-a" });

    const status = await getSystemStatus(pg.sql);

    expect(status.publish.health).toBe("healthy");
    expect(status.collect.health).toBe("healthy");
    expect(status.host.diskFreeBytes).toBe(8_000_000_000);
    expect(status.host.bootId).toBe("abcdef01-2345");
    expect(status.database.bytes).toBe(100_000_000);
    expect(status.queue.spoolBytes).toBe(4096);
  });

  // The single most important behaviour on the page: collection facts travel
  // through the publisher, so a stale publisher makes the collector unknowable
  // rather than broken.
  it("calls the collector unknown behind a stale publisher", async () => {
    await addRun("publish", { minutesAgo: 60 * 6 });
    await addRun("collect", { minutesAgo: 60 * 6 + 5, runKey: "batch-a" });

    const status = await getSystemStatus(pg.sql);

    expect(status.publish.health).toBe("stopped");
    expect(status.collect.health).toBe("unknown");
  });

  // The other half of that: with the publisher current, a missing collector is
  // a real, reportable fault.
  it("calls the collector stopped behind a healthy publisher", async () => {
    await addRun("publish", { minutesAgo: 5 });
    await addRun("collect", { minutesAgo: 60 * 4, runKey: "batch-a" });

    const status = await getSystemStatus(pg.sql);

    expect(status.publish.health).toBe("healthy");
    expect(status.collect.health).toBe("stopped");
  });

  it("keeps a failed run's reason", async () => {
    await addRun("publish", {
      minutesAgo: 5,
      result: "failed",
      errorKind: "database",
      errorMessage: "OperationalError: connection lost",
    });

    const status = await getSystemStatus(pg.sql);

    expect(status.publish.latest?.result).toBe("failed");
    expect(status.publish.latest?.errorKind).toBe("database");
    expect(status.publish.latest?.errorMessage).toContain("connection lost");
  });

  it("shows a run that is still going as running", async () => {
    await addRun("publish", { minutesAgo: 1, result: "running", elapsedMs: null });

    const status = await getSystemStatus(pg.sql);

    expect(status.publish.latest?.result).toBe("running");
    expect(status.publish.latest?.finishedAt).toBeNull();
  });

  it("takes the median duration rather than the mean", async () => {
    // One slow run behind a dead network must not describe a week.
    await addRun("publish", { minutesAgo: 5, elapsedMs: 1000 });
    await addRun("publish", { minutesAgo: 65, elapsedMs: 2000 });
    await addRun("publish", { minutesAgo: 125, elapsedMs: 900_000 });

    const status = await getSystemStatus(pg.sql);

    expect(status.publish.medianElapsedMs).toBe(2000);
  });

  it("breaks the latest collection down by source", async () => {
    await addRun("publish", { minutesAgo: 5 });
    await addRun("collect", {
      minutesAgo: 20,
      runKey: "batch-a",
      result: "partial",
      details: {
        posts_collected: 12,
        sources: [
          {
            source_type: "rss",
            result: "partial",
            channels_attempted: 40,
            channels_succeeded: 38,
            channels_failed: 2,
            items_collected: 12,
            elapsed_ms: 8000,
            error_kind: "http",
            error_message: "",
          },
          {
            source_type: "bluesky",
            result: "skipped",
            channels_attempted: 0,
            channels_succeeded: 0,
            channels_failed: 0,
            items_collected: 0,
            elapsed_ms: 0,
            error_kind: "",
            error_message: "",
          },
        ],
        subtasks: [
          {
            name: "follows",
            scope: "bluesky",
            result: "failed",
            elapsed_ms: 500,
            error_kind: "network",
            error_message: "refused",
          },
        ],
      },
    });

    const status = await getSystemStatus(pg.sql);

    expect(status.sources).toHaveLength(2);
    expect(status.sources[0]).toMatchObject({
      sourceType: "rss",
      channelsFailed: 2,
      itemsCollected: 12,
      errorKind: "http",
    });
    expect(status.sources[1].result).toBe("skipped");
    expect(status.subtasks[0]).toMatchObject({ scope: "bluesky", result: "failed" });
  });

  it("counts the database change over the window", async () => {
    await addRun("publish", { minutesAgo: 60 * 24 * 6, details: { database_bytes: 90 } });
    await addRun("publish", { minutesAgo: 5, details: { database_bytes: 110 } });

    const status = await getSystemStatus(pg.sql);

    expect(status.database.bytes).toBe(110);
    expect(status.database.changeBytes).toBe(20);
  });

  it("does not report a change it cannot measure", async () => {
    await addRun("publish", { minutesAgo: 5, details: { database_bytes: 110 } });

    const status = await getSystemStatus(pg.sql);

    expect(status.database.changeBytes).toBeNull();
  });

  it("counts a growing queue and quarantined batches", async () => {
    await addRun("publish", {
      minutesAgo: 5,
      result: "partial",
      details: {
        queue_after: {
          counts: { ready: 4, processing: 1, published: 20, failed: 2 },
          oldest_outstanding_age_seconds: 9000,
          disk_bytes: 1024,
        },
      },
    });

    const status = await getSystemStatus(pg.sql);

    expect(status.queue.ready).toBe(4);
    expect(status.queue.quarantined).toBe(2);
    expect(status.queue.oldestOutstandingAgeSeconds).toBe(9000);
  });

  it("falls back to the pre-drain queue for a run that never finished", async () => {
    await addRun("publish", {
      minutesAgo: 2,
      result: "running",
      elapsedMs: null,
      details: { queue_before: { counts: { ready: 3 } } },
    });

    const status = await getSystemStatus(pg.sql);

    expect(status.queue.ready).toBe(3);
  });

  it("marks content quiet only after two publishing rounds", async () => {
    await addRun("publish", { minutesAgo: 5 });
    await pg.exec(
      `INSERT INTO content.posts (unique_id, source, source_type, posted_at, first_seen_at)
       VALUES ('a', 'src', 'rss', now(), now() - interval '30 minutes')`,
    );

    expect((await getSystemStatus(pg.sql)).content.quiet).toBe(false);

    await pg.exec(
      `UPDATE content.posts SET first_seen_at = now() - interval '5 hours'`,
    );

    const status = await getSystemStatus(pg.sql);
    expect(status.content.quiet).toBe(true);
    expect(status.content.postCount).toBe(1);
  });

  it("counts failing feeds for the one-link-away detail", async () => {
    await pg.exec(
      `INSERT INTO catalog.rss_feeds (feed_url, normalized_url)
       VALUES ('https://a.example/f', 'https://a.example/f'),
              ('https://b.example/f', 'https://b.example/f')`,
    );
    await pg.exec(
      `INSERT INTO content.rss_feed_health (normalized_url, consecutive_failures)
       VALUES ('https://a.example/f', 3), ('https://b.example/f', 0)`,
    );

    expect((await getSystemStatus(pg.sql)).failingFeedCount).toBe(1);
  });

  it("lists recent runs newest first across every job", async () => {
    await addRun("publish", { minutesAgo: 5 });
    await addRun("collect", { minutesAgo: 20, runKey: "batch-a" });
    await addRun("retention", { minutesAgo: 200 });

    const status = await getSystemStatus(pg.sql);

    expect(status.recentRuns.map((run) => run.job)).toEqual([
      "publish",
      "collect",
      "retention",
    ]);
  });

  // A job name this build has never heard of appears in the history without a
  // card, which is what the migration's unconstrained `job` column was for.
  it("shows an unrecognized job in the history without breaking", async () => {
    await addRun("something-new", { minutesAgo: 5 });

    const status = await getSystemStatus(pg.sql);

    expect(status.recentRuns[0].job).toBe("something-new");
    expect(status.publish.latest).toBeNull();
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { JobStatus, SystemStatus } from "../../../models/system-status";
import { StatusView } from "./page";

// No DOM library is installed and none is being added for this. Static markup
// is enough: every assertion here is about what the page says, and the page has
// no interactive behaviour to drive.
function render(node: Parameters<typeof renderToStaticMarkup>[0]): string {
  return renderToStaticMarkup(node);
}

function job(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    job: "publish",
    health: "healthy",
    latest: {
      job: "publish",
      startedAt: "2026-01-02T11:50:00.000Z",
      finishedAt: "2026-01-02T11:50:02.000Z",
      reportedAt: "2026-01-02T11:50:02.000Z",
      result: "ok",
      elapsedMs: 2000,
      errorKind: "",
      errorMessage: "",
    },
    ageSeconds: 600,
    intervalSeconds: 3600,
    medianElapsedMs: 1800,
    strip: [
      { startsAt: "2026-01-02T10:00:00.000Z", state: "ok" },
      { startsAt: "2026-01-02T11:00:00.000Z", state: "missing" },
    ],
    ...overrides,
  };
}

function status(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    observedAt: "2026-01-02T12:00:00.000Z",
    collect: job({ job: "collect", intervalSeconds: 1800 }),
    publish: job(),
    catalogSync: job({ job: "catalog-sync" }),
    retention: job({ job: "retention", health: "unknown", latest: null }),
    sources: [],
    subtasks: [],
    queue: {
      ready: 0,
      processing: 0,
      published: 12,
      quarantined: 0,
      oldestOutstandingAgeSeconds: null,
      spoolBytes: 4096,
    },
    host: { uptimeSeconds: 7200, bootId: "abcdef01-2345-6789", diskFreeBytes: 8e9 },
    database: { bytes: 100_000_000, changeBytes: 4_000_000 },
    content: {
      lastNewPostAt: "2026-01-02T11:30:00.000Z",
      quiet: false,
      postCount: 4210,
    },
    recentRuns: [],
    failingFeedCount: 0,
    ...overrides,
  };
}

describe("StatusView", () => {
  // The guide is now a page of its own, which is what makes it survive this:
  // the read failing is often the fault being diagnosed, and a static page
  // answers when a database-backed one cannot.
  it("still points at the guide when the database cannot be read", () => {
    const html = render(<StatusView result={{ status: "error" }} />);

    expect(html).toContain("Status data unavailable");
    expect(html).toContain("/flightdeck/status/guide");
    expect(html).not.toContain("Last new post");
  });

  it("links to the guide from the top of the page", () => {
    const html = render(<StatusView result={{ status: "ready", data: status() }} />);

    expect(html).toContain("How to read this page");
    expect(html).toContain("/flightdeck/status/guide");
  });

  // "partial" reads as a fault to anyone who has not been told otherwise, and
  // for a collection across several hundred feeds it is the ordinary case.
  it("explains an outcome word where the word appears", () => {
    const data = status({
      recentRuns: [
        {
          job: "collect",
          startedAt: "2026-01-02T11:30:00.000Z",
          finishedAt: "2026-01-02T11:30:42.000Z",
          reportedAt: "2026-01-02T11:40:00.000Z",
          result: "partial",
          elapsedMs: 42000,
          errorKind: "",
          errorMessage: "",
        },
      ],
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("some parts worked and some did not");
    expect(html).toContain("a few feeds timing out");
  });

  it("shows both headline jobs and their state", () => {
    const html = render(<StatusView result={{ status: "ready", data: status() }} />);

    expect(html).toContain("Collector");
    expect(html).toContain("Publisher");
    expect(html).toContain("Healthy");
    expect(html).toContain("4,210");
  });

  it("says a stopped job has not reported rather than guessing why", () => {
    const data = status({
      publish: job({ health: "stopped", ageSeconds: 60 * 60 * 9 }),
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("Stopped");
    expect(html).toContain("Has not reported");
  });

  it("explains a quiet site as normal rather than broken", () => {
    const data = status({
      content: {
        lastNewPostAt: "2026-01-02T02:00:00.000Z",
        quiet: true,
        postCount: 10,
      },
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("Quiet");
    expect(html).toContain("Not a fault on its own");
  });

  it("surfaces a run's error where it happened", () => {
    const data = status({
      publish: job({
        health: "delayed",
        latest: {
          job: "publish",
          startedAt: "2026-01-02T11:00:00.000Z",
          finishedAt: "2026-01-02T11:00:01.000Z",
          reportedAt: "2026-01-02T11:00:01.000Z",
          result: "failed",
          elapsedMs: 1000,
          errorKind: "database",
          errorMessage: "connection refused",
        },
      }),
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("connection refused");
    expect(html).toContain("database");
  });

  it("counts quarantined batches where they are actionable", () => {
    const data = status({
      queue: {
        ready: 2,
        processing: 0,
        published: 12,
        quarantined: 3,
        oldestOutstandingAgeSeconds: 7200,
        spoolBytes: 4096,
      },
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("3 quarantined");
    expect(html).toContain("2 batches waiting");
  });

  it("breaks the latest collection down by source", () => {
    const data = status({
      sources: [
        {
          sourceType: "rss",
          result: "partial",
          channelsAttempted: 40,
          channelsSucceeded: 38,
          channelsFailed: 2,
          itemsCollected: 12,
          elapsedMs: 8000,
          errorKind: "http",
          errorMessage: "",
        },
        {
          sourceType: "bluesky",
          result: "skipped",
          channelsAttempted: 0,
          channelsSucceeded: 0,
          channelsFailed: 0,
          itemsCollected: 0,
          elapsedMs: 0,
          errorKind: "",
          errorMessage: "",
        },
      ],
      failingFeedCount: 2,
      subtasks: [
        {
          name: "follows",
          scope: "mastodon",
          result: "failed",
          elapsedMs: 500,
          errorKind: "network",
          errorMessage: "refused",
        },
      ],
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("RSS");
    expect(html).toContain("38/40 channels");
    expect(html).toContain("Switched off in the configuration.");
    expect(html).toContain("/flightdeck/feeds");
    expect(html).toContain("follows");
  });

  // The strip is the page's only view of what did not happen, so a gap must be
  // legible without colour.
  it("labels a missing slot in text", () => {
    const html = render(<StatusView result={{ status: "ready", data: status() }} />);

    expect(html).toContain("no run");
    expect(html).toContain("status-slot-missing");
  });

  it("says plainly when nothing has ever run", () => {
    const data = status({
      collect: job({ job: "collect", health: "unknown", latest: null, strip: [] }),
      publish: job({ health: "unknown", latest: null, strip: [] }),
      content: { lastNewPostAt: null, quiet: false, postCount: 0 },
    });
    const html = render(<StatusView result={{ status: "ready", data }} />);

    expect(html).toContain("has never reported");
    expect(html).toContain("Nothing stored yet");
    expect(html).toContain("No runs have been recorded");
  });
});

import { describe, expect, it } from "vitest";

import {
  addRssFeed,
  countRssFeeds,
  countRssFeedsByStatus,
  listRssFeeds,
  listRssFeedsPage,
  normalizeFeedUrl,
  restoreRssFeed,
  softDeleteRssFeed,
} from "./feeds";
import { describePostgres, usePostgres } from "./test-support/postgres";

describe("normalizeFeedUrl", () => {
  it("lowercases the host", () => {
    expect(normalizeFeedUrl("https://EXAMPLE.com/Feed")).toBe(
      "https://example.com/Feed",
    );
  });

  // Two feeds on one host differ only by path, so the path must survive intact.
  it("preserves path and query case", () => {
    expect(normalizeFeedUrl("https://example.com/Feed?Tag=A")).toBe(
      "https://example.com/Feed?Tag=A",
    );
  });

  it("drops the fragment", () => {
    expect(normalizeFeedUrl("https://example.com/feed#recent")).toBe(
      "https://example.com/feed",
    );
  });

  it("rejects anything that is not absolute http(s)", () => {
    expect(normalizeFeedUrl("")).toBe("");
    expect(normalizeFeedUrl("   ")).toBe("");
    expect(normalizeFeedUrl("example.com/feed")).toBe("");
    expect(normalizeFeedUrl("ftp://example.com/feed")).toBe("");
    expect(normalizeFeedUrl("javascript:alert(1)")).toBe("");
  });
});

describePostgres("rss feed catalog", () => {
  const pg = usePostgres();

  async function addHealth(
    normalizedUrl: string,
    health: {
      lastFetchedAt?: string;
      lastSuccessAt?: string;
      lastStatus?: number;
      lastError?: string;
      consecutiveFailures?: number;
      etag?: string;
      lastModified?: string;
      latestEntryAt?: string;
      siteLink?: string;
    } = {},
  ): Promise<void> {
    await pg.exec(
      `INSERT INTO content.rss_feed_health
         (normalized_url, last_fetched_at, last_success_at, last_status,
          last_error, consecutive_failures, etag, last_modified,
          latest_entry_at, site_link)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
      [
        normalizedUrl,
        health.lastFetchedAt ?? null,
        health.lastSuccessAt ?? null,
        health.lastStatus ?? null,
        health.lastError ?? null,
        health.consecutiveFailures ?? 0,
        health.etag ?? null,
        health.lastModified ?? null,
        health.latestEntryAt ?? null,
        health.siteLink ?? null,
      ],
    );
  }

  it("adds a feed and reports it as active", async () => {
    const result = await addRssFeed(pg.sql, "https://example.com/feed");

    expect(result.status).toBe("added");
    if (result.status !== "added") return;
    expect(result.feed).toMatchObject({
      feedUrl: "https://example.com/feed",
      normalizedUrl: "https://example.com/feed",
      enabled: true,
      deletedAt: null,
      status: "active",
      consecutiveFailures: 0,
    });
  });

  it("stores the raw url alongside the normalized one", async () => {
    const result = await addRssFeed(pg.sql, "  https://EXAMPLE.com/Feed  ");

    expect(result.status).toBe("added");
    if (result.status !== "added") return;
    expect(result.feed.feedUrl).toBe("https://EXAMPLE.com/Feed");
    expect(result.feed.normalizedUrl).toBe("https://example.com/Feed");
  });

  it("reports an existing feed rather than inserting a duplicate", async () => {
    await addRssFeed(pg.sql, "https://example.com/feed");
    const again = await addRssFeed(pg.sql, "https://EXAMPLE.com/feed");

    expect(again.status).toBe("exists");
    await expect(countRssFeedsByStatus(pg.sql)).resolves.toMatchObject({
      total: 1,
    });
  });

  it("rejects an invalid url without touching the catalog", async () => {
    await expect(addRssFeed(pg.sql, "not a url")).resolves.toMatchObject({
      status: "invalid",
    });
    await expect(addRssFeed(pg.sql, "  ")).resolves.toMatchObject({
      status: "invalid",
      reason: "URL is required.",
    });
    await expect(countRssFeedsByStatus(pg.sql)).resolves.toMatchObject({
      total: 0,
    });
  });

  it("records the supplied timestamp as UTC", async () => {
    const result = await addRssFeed(
      pg.sql,
      "https://example.com/feed",
      new Date("2026-05-06T07:08:09Z"),
    );

    expect(result.status).toBe("added");
    if (result.status !== "added") return;
    expect(result.feed.addedAt).toBe("2026-05-06T07:08:09Z");
  });

  it("joins health on the normalized url", async () => {
    await addRssFeed(pg.sql, "https://example.com/feed");
    await addHealth("https://example.com/feed", {
      lastFetchedAt: "2026-02-01T10:00:00Z",
      lastSuccessAt: "2026-02-01T10:00:00Z",
      lastStatus: 200,
      consecutiveFailures: 0,
      etag: 'W/"abc"',
      lastModified: "Wed, 01 Feb 2026 10:00:00 GMT",
      latestEntryAt: "2026-01-31T23:00:00Z",
      siteLink: "https://example.com",
    });

    const [feed] = await listRssFeeds(pg.sql);

    expect(feed).toMatchObject({
      lastFetchedAt: "2026-02-01T10:00:00Z",
      lastStatus: 200,
      etag: 'W/"abc"',
      // An HTTP header echoed back verbatim, so it stays text and is not
      // reformatted as a timestamp.
      lastModified: "Wed, 01 Feb 2026 10:00:00 GMT",
      latestEntryAt: "2026-01-31T23:00:00Z",
      siteLink: "https://example.com",
    });
  });

  it("returns a feed with no health row yet", async () => {
    await addRssFeed(pg.sql, "https://example.com/feed");

    const [feed] = await listRssFeeds(pg.sql);

    expect(feed).toMatchObject({
      lastFetchedAt: null,
      lastStatus: null,
      lastError: null,
      consecutiveFailures: 0,
    });
  });

  // Health is keyed by url, not by the catalog's row id, precisely so it
  // outlives a remove/re-add cycle.
  it("keeps health across a remove and re-add", async () => {
    const added = await addRssFeed(pg.sql, "https://example.com/feed");
    if (added.status !== "added") throw new Error("expected added");
    await addHealth("https://example.com/feed", { consecutiveFailures: 3 });

    await softDeleteRssFeed(pg.sql, added.feed.id);
    await restoreRssFeed(pg.sql, added.feed.id);

    const [feed] = await listRssFeeds(pg.sql);
    expect(feed?.consecutiveFailures).toBe(3);
    expect(feed?.status).toBe("active");
  });

  it("soft deletes rather than removing the row", async () => {
    const added = await addRssFeed(pg.sql, "https://example.com/feed");
    if (added.status !== "added") throw new Error("expected added");

    await expect(softDeleteRssFeed(pg.sql, added.feed.id)).resolves.toBe(true);

    const [feed] = await listRssFeeds(pg.sql);
    expect(feed).toMatchObject({ status: "removed", enabled: false });
    expect(feed?.deletedAt).not.toBeNull();
  });

  it("does not soft delete an already removed feed twice", async () => {
    const added = await addRssFeed(pg.sql, "https://example.com/feed");
    if (added.status !== "added") throw new Error("expected added");

    await softDeleteRssFeed(pg.sql, added.feed.id);

    await expect(softDeleteRssFeed(pg.sql, added.feed.id)).resolves.toBe(false);
  });

  it("restores only a removed feed", async () => {
    const added = await addRssFeed(pg.sql, "https://example.com/feed");
    if (added.status !== "added") throw new Error("expected added");

    await expect(restoreRssFeed(pg.sql, added.feed.id)).resolves.toBe(false);

    await softDeleteRssFeed(pg.sql, added.feed.id);

    await expect(restoreRssFeed(pg.sql, added.feed.id)).resolves.toBe(true);
    const [feed] = await listRssFeeds(pg.sql);
    expect(feed).toMatchObject({ status: "active", enabled: true, deletedAt: null });
  });

  it("reports false for an unknown feed id", async () => {
    await expect(softDeleteRssFeed(pg.sql, 4242)).resolves.toBe(false);
    await expect(restoreRssFeed(pg.sql, 4242)).resolves.toBe(false);
  });

  it("filters by status", async () => {
    const active = await addRssFeed(pg.sql, "https://active.example/feed");
    const removed = await addRssFeed(pg.sql, "https://removed.example/feed");
    const disabled = await addRssFeed(pg.sql, "https://disabled.example/feed");
    if (removed.status !== "added" || disabled.status !== "added") {
      throw new Error("expected added");
    }
    await softDeleteRssFeed(pg.sql, removed.feed.id);
    await pg.exec("UPDATE catalog.rss_feeds SET enabled = false WHERE feed_id = $1", [
      disabled.feed.id,
    ]);

    await expect(
      listRssFeeds(pg.sql, { status: "active" }),
    ).resolves.toHaveLength(1);
    await expect(
      listRssFeeds(pg.sql, { status: "disabled" }),
    ).resolves.toHaveLength(1);
    await expect(
      listRssFeeds(pg.sql, { status: "removed" }),
    ).resolves.toHaveLength(1);
    await expect(listRssFeeds(pg.sql, { status: "all" })).resolves.toHaveLength(
      3,
    );
    expect(active.status).toBe("added");
  });

  it("filters to feeds that are failing", async () => {
    await addRssFeed(pg.sql, "https://healthy.example/feed");
    await addRssFeed(pg.sql, "https://failing.example/feed");
    await addRssFeed(pg.sql, "https://notfound.example/feed");
    await addHealth("https://healthy.example/feed", { lastStatus: 200 });
    await addHealth("https://failing.example/feed", { consecutiveFailures: 2 });
    await addHealth("https://notfound.example/feed", { lastStatus: 404 });

    const failing = await listRssFeeds(pg.sql, { errors: true });

    expect(failing.map((feed) => feed.normalizedUrl).sort()).toEqual([
      "https://failing.example/feed",
      "https://notfound.example/feed",
    ]);
  });

  it("excludes removed feeds from the error filter", async () => {
    const feed = await addRssFeed(pg.sql, "https://failing.example/feed");
    if (feed.status !== "added") throw new Error("expected added");
    await addHealth("https://failing.example/feed", { consecutiveFailures: 5 });
    await softDeleteRssFeed(pg.sql, feed.feed.id);

    await expect(listRssFeeds(pg.sql, { errors: true })).resolves.toEqual([]);
  });

  it("searches feed urls without regard to case", async () => {
    await addRssFeed(pg.sql, "https://example.com/Tech");
    await addRssFeed(pg.sql, "https://other.example/news");

    const results = await listRssFeeds(pg.sql, { q: "TECH" });

    expect(results.map((feed) => feed.normalizedUrl)).toEqual([
      "https://example.com/Tech",
    ]);
  });

  it("treats wildcards in a feed search as literal characters", async () => {
    await addRssFeed(pg.sql, "https://example.com/a_b");
    await addRssFeed(pg.sql, "https://example.com/axb");

    const results = await listRssFeeds(pg.sql, { q: "a_b" });

    expect(results.map((feed) => feed.normalizedUrl)).toEqual([
      "https://example.com/a_b",
    ]);
  });

  it("orders active feeds before disabled and removed ones", async () => {
    await addRssFeed(pg.sql, "https://zzz.example/feed");
    const removed = await addRssFeed(pg.sql, "https://aaa.example/feed");
    if (removed.status !== "added") throw new Error("expected added");
    await softDeleteRssFeed(pg.sql, removed.feed.id);

    const feeds = await listRssFeeds(pg.sql);

    expect(feeds.map((feed) => feed.status)).toEqual(["active", "removed"]);
  });

  it("counts each status independently", async () => {
    await addRssFeed(pg.sql, "https://active.example/feed");
    const removed = await addRssFeed(pg.sql, "https://removed.example/feed");
    const failing = await addRssFeed(pg.sql, "https://failing.example/feed");
    if (removed.status !== "added" || failing.status !== "added") {
      throw new Error("expected added");
    }
    await softDeleteRssFeed(pg.sql, removed.feed.id);
    await addHealth("https://failing.example/feed", { consecutiveFailures: 1 });

    await expect(countRssFeedsByStatus(pg.sql)).resolves.toEqual({
      active: 2,
      disabled: 0,
      removed: 1,
      errors: 1,
      total: 3,
    });
  });

  it("returns zeroes rather than nulls for an empty catalog", async () => {
    await expect(countRssFeedsByStatus(pg.sql)).resolves.toEqual({
      active: 0,
      disabled: 0,
      removed: 0,
      errors: 0,
      total: 0,
    });
  });

  async function addFeeds(count: number): Promise<void> {
    for (let index = 0; index < count; index += 1) {
      // Zero-padded so lexical order matches numeric order and the assertions
      // below can name an exact page of results.
      const label = String(index).padStart(2, "0");
      await addRssFeed(pg.sql, `https://feed${label}.example/feed`);
    }
  }

  it("splits the catalog into pages in the list order", async () => {
    await addFeeds(5);

    const page = await listRssFeedsPage(pg.sql, {}, 2, 2);

    expect(page.feeds.map((feed) => feed.normalizedUrl)).toEqual([
      "https://feed02.example/feed",
      "https://feed03.example/feed",
    ]);
    expect(page).toMatchObject({
      page: 2,
      pageSize: 2,
      totalFeeds: 5,
      totalPages: 3,
      hasPreviousPage: true,
      hasNextPage: true,
    });
  });

  it("clamps a page beyond the end back to the last page", async () => {
    await addFeeds(3);

    const page = await listRssFeedsPage(pg.sql, {}, 99, 2);

    expect(page.page).toBe(2);
    expect(page.hasNextPage).toBe(false);
    expect(page.feeds.map((feed) => feed.normalizedUrl)).toEqual([
      "https://feed02.example/feed",
    ]);
  });

  it("clamps a page below one back to the first page", async () => {
    await addFeeds(3);

    const page = await listRssFeedsPage(pg.sql, {}, 0, 2);

    expect(page.page).toBe(1);
    expect(page.hasPreviousPage).toBe(false);
  });

  // An empty catalog must still report one page, or the pager renders
  // "Page 1 of 0".
  it("reports a single empty page for an empty catalog", async () => {
    const page = await listRssFeedsPage(pg.sql, {}, 1, 2);

    expect(page).toMatchObject({
      feeds: [],
      page: 1,
      totalFeeds: 0,
      totalPages: 1,
      hasPreviousPage: false,
      hasNextPage: false,
    });
  });

  // The total is what the pager divides by, so a total that ignored the
  // filters would offer pages that render empty.
  it("counts and pages only the rows matching the filters", async () => {
    await addFeeds(4);
    const removed = await addRssFeed(pg.sql, "https://feed99.example/feed");
    if (removed.status !== "added") throw new Error("expected added");
    await softDeleteRssFeed(pg.sql, removed.feed.id);

    await expect(countRssFeeds(pg.sql, { status: "active" })).resolves.toBe(4);
    await expect(countRssFeeds(pg.sql, { status: "removed" })).resolves.toBe(1);
    await expect(countRssFeeds(pg.sql, { q: "feed0" })).resolves.toBe(4);

    const page = await listRssFeedsPage(pg.sql, { status: "removed" }, 1, 2);
    expect(page.totalFeeds).toBe(1);
    expect(page.totalPages).toBe(1);
    expect(page.feeds.map((feed) => feed.normalizedUrl)).toEqual([
      "https://feed99.example/feed",
    ]);
  });

  // The search pattern and the limit/offset share one placeholder sequence, so
  // a mis-ordered append would bind the wrong value to the wrong slot.
  it("keeps a search filter intact alongside limit and offset", async () => {
    await addFeeds(4);
    await addRssFeed(pg.sql, "https://other.example/feed");

    const page = await listRssFeedsPage(pg.sql, { q: "feed0" }, 2, 3);

    expect(page.totalFeeds).toBe(4);
    expect(page.feeds.map((feed) => feed.normalizedUrl)).toEqual([
      "https://feed03.example/feed",
    ]);
  });

  it("leaves the unpaginated list returning every row", async () => {
    await addFeeds(3);

    await expect(listRssFeeds(pg.sql)).resolves.toHaveLength(3);
  });
});

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

import {
  addRssFeed,
  countRssFeedsByStatus,
  listRssFeeds,
  normalizeFeedUrl,
  openWritableFetchlinksDatabase,
  restoreRssFeed,
  setRssFeedEnabled,
  softDeleteRssFeed,
  withWritableFetchlinksDatabase,
} from "./feeds";

type Fixture = {
  dbPath: string;
  cleanup: () => void;
};

function createFeedsFixture(): Fixture {
  const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-feeds-"));
  const dbPath = path.join(directory, "fetchlinks.db");
  const database = new DatabaseSync(dbPath);
  database.exec(`
    CREATE TABLE rss_feeds (
      feed_id              INTEGER PRIMARY KEY,
      feed_url             TEXT NOT NULL,
      normalized_url       TEXT NOT NULL UNIQUE,
      enabled              INTEGER NOT NULL DEFAULT 1,
      added_at             TEXT NOT NULL,
      deleted_at           TEXT,
      last_fetched_at      TEXT,
      last_success_at      TEXT,
      last_status          INTEGER,
      last_error           TEXT,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      etag                 TEXT,
      last_modified        TEXT,
      latest_entry_at      TEXT,
      site_link            TEXT
    );

    INSERT INTO rss_feeds
      (feed_id, feed_url, normalized_url, enabled, added_at, deleted_at,
       last_error, consecutive_failures, site_link)
    VALUES
      (1, 'https://a.example/feed', 'https://a.example/feed', 1, '2026-01-01 00:00:00', NULL, NULL, 0, 'https://a.example/'),
      (2, 'https://b.example/feed', 'https://b.example/feed', 0, '2026-01-02 00:00:00', NULL, 'HTTP 500', 10, NULL),
      (3, 'https://c.example/feed', 'https://c.example/feed', 0, '2026-01-03 00:00:00', '2026-02-01 00:00:00', NULL, 0, NULL);
  `);
  database.close();
  return {
    dbPath,
    cleanup: () => rmSync(directory, { force: true, recursive: true }),
  };
}

describe("listRssFeeds", () => {
  it("returns all feeds with computed status by default", () => {
    const fixture = createFeedsFixture();
    try {
      const feeds = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listRssFeeds(db),
      );
      const byUrl = Object.fromEntries(feeds.map((f) => [f.feedUrl, f.status]));
      expect(byUrl).toEqual({
        "https://a.example/feed": "active",
        "https://b.example/feed": "disabled",
        "https://c.example/feed": "removed",
      });
    } finally {
      fixture.cleanup();
    }
  });

  it("filters by status", () => {
    const fixture = createFeedsFixture();
    try {
      const active = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listRssFeeds(db, { status: "active" }),
      );
      expect(active.map((f) => f.feedUrl)).toEqual(["https://a.example/feed"]);

      const removed = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listRssFeeds(db, { status: "removed" }),
      );
      expect(removed.map((f) => f.feedUrl)).toEqual(["https://c.example/feed"]);
    } finally {
      fixture.cleanup();
    }
  });

  it("filters by search substring against feed_url and normalized_url", () => {
    const fixture = createFeedsFixture();
    try {
      const matches = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listRssFeeds(db, { q: "B.EXAMPLE" }),
      );
      expect(matches.map((f) => f.feedUrl)).toEqual(["https://b.example/feed"]);
    } finally {
      fixture.cleanup();
    }
  });

  it("surfaces siteLink (or null) from the rss_feeds row", () => {
    const fixture = createFeedsFixture();
    try {
      const feeds = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listRssFeeds(db),
      );
      const byUrl = Object.fromEntries(
        feeds.map((f) => [f.feedUrl, f.siteLink]),
      );
      expect(byUrl).toEqual({
        "https://a.example/feed": "https://a.example/",
        "https://b.example/feed": null,
        "https://c.example/feed": null,
      });
    } finally {
      fixture.cleanup();
    }
  });
});

describe("countRssFeedsByStatus", () => {
  it("returns counts across the three buckets", () => {
    const fixture = createFeedsFixture();
    try {
      const counts = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => countRssFeedsByStatus(db),
      );
      expect(counts).toEqual({
        active: 1,
        disabled: 1,
        removed: 1,
        errors: 0,
        total: 3,
      });
    } finally {
      fixture.cleanup();
    }
  });

  it("counts active feeds with failures or HTTP errors as errors", () => {
    const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-feeds-err-"));
    const dbPath = path.join(directory, "fetchlinks.db");
    const database = new DatabaseSync(dbPath);
    database.exec(`
      CREATE TABLE rss_feeds (
        feed_id              INTEGER PRIMARY KEY,
        feed_url             TEXT NOT NULL,
        normalized_url       TEXT NOT NULL UNIQUE,
        enabled              INTEGER NOT NULL DEFAULT 1,
        added_at             TEXT NOT NULL,
        deleted_at           TEXT,
        last_fetched_at      TEXT,
        last_success_at      TEXT,
        last_status          INTEGER,
        last_error           TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        etag                 TEXT,
        last_modified        TEXT,
        latest_entry_at      TEXT,
        site_link            TEXT
      );
      INSERT INTO rss_feeds
        (feed_id, feed_url, normalized_url, enabled, added_at,
         last_status, consecutive_failures)
      VALUES
        (1, 'https://ok.example/feed',     'https://ok.example/feed',     1, 'x', 200, 0),
        (2, 'https://fails.example/feed',  'https://fails.example/feed',  1, 'x', 200, 3),
        (3, 'https://http500.example/feed','https://http500.example/feed',1, 'x', 500, 0),
        (4, 'https://disabled.example/feed','https://disabled.example/feed',0,'x',500, 5);
    `);
    database.close();
    try {
      const counts = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: dbPath },
        (db) => countRssFeedsByStatus(db),
      );
      expect(counts).toEqual({
        active: 3,
        disabled: 1,
        removed: 0,
        errors: 2,
        total: 4,
      });

      const onlyErrors = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: dbPath },
        (db) => listRssFeeds(db, { errors: true }),
      );
      expect(onlyErrors.map((f) => f.feedUrl).sort()).toEqual([
        "https://fails.example/feed",
        "https://http500.example/feed",
      ]);
    } finally {
      rmSync(directory, { force: true, recursive: true });
    }
  });
});

describe("normalizeFeedUrl", () => {
  it("rejects non-http(s) and unparseable URLs", () => {
    expect(normalizeFeedUrl("")).toBe("");
    expect(normalizeFeedUrl("ftp://example/")).toBe("");
    expect(normalizeFeedUrl("not a url")).toBe("");
  });

  it("lowercases the hostname and drops the fragment", () => {
    expect(normalizeFeedUrl("HTTPS://Example.Com/Feed#anchor")).toBe(
      "https://example.com/Feed",
    );
  });
});

describe("addRssFeed", () => {
  it("inserts a new feed and returns the inserted row", () => {
    const fixture = createFeedsFixture();
    try {
      const result = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addRssFeed(db, "https://new.example/feed"),
      );
      expect(result.status).toBe("added");
      if (result.status === "added") {
        expect(result.feed.feedUrl).toBe("https://new.example/feed");
        expect(result.feed.status).toBe("active");
      }
    } finally {
      fixture.cleanup();
    }
  });

  it("returns 'exists' when the normalized URL is already present", () => {
    const fixture = createFeedsFixture();
    try {
      const result = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addRssFeed(db, "HTTPS://A.example/feed"),
      );
      expect(result.status).toBe("exists");
      if (result.status === "exists") {
        expect(result.feed.feedUrl).toBe("https://a.example/feed");
      }
    } finally {
      fixture.cleanup();
    }
  });

  it("returns 'invalid' for empty or non-URL input", () => {
    const fixture = createFeedsFixture();
    try {
      const empty = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addRssFeed(db, "   "),
      );
      expect(empty.status).toBe("invalid");

      const bad = withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addRssFeed(db, "ftp://example/"),
      );
      expect(bad.status).toBe("invalid");
    } finally {
      fixture.cleanup();
    }
  });
});

describe("setRssFeedEnabled", () => {
  it("enables a disabled feed and resets the failure counter", () => {
    const fixture = createFeedsFixture();
    try {
      withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => {
          expect(setRssFeedEnabled(db, 2, true)).toBe(true);
          const [feed] = listRssFeeds(db, { q: "b.example" });
          expect(feed.status).toBe("active");
          expect(feed.consecutiveFailures).toBe(0);
        },
      );
    } finally {
      fixture.cleanup();
    }
  });

  it("does not touch tombstoned rows", () => {
    const fixture = createFeedsFixture();
    try {
      withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => {
          expect(setRssFeedEnabled(db, 3, true)).toBe(false);
          const [feed] = listRssFeeds(db, { q: "c.example" });
          expect(feed.status).toBe("removed");
        },
      );
    } finally {
      fixture.cleanup();
    }
  });
});

describe("softDeleteRssFeed", () => {
  it("tombstones a live row", () => {
    const fixture = createFeedsFixture();
    try {
      withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => {
          expect(softDeleteRssFeed(db, 1)).toBe(true);
          const [feed] = listRssFeeds(db, { q: "a.example" });
          expect(feed.status).toBe("removed");
          expect(feed.deletedAt).toBeTruthy();
        },
      );
    } finally {
      fixture.cleanup();
    }
  });

  it("is a no-op when the row is already tombstoned", () => {
    const fixture = createFeedsFixture();
    try {
      withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => {
          expect(softDeleteRssFeed(db, 3)).toBe(false);
        },
      );
    } finally {
      fixture.cleanup();
    }
  });
});

describe("restoreRssFeed", () => {
  it("restores a tombstoned row and re-enables it", () => {
    const fixture = createFeedsFixture();
    try {
      withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => {
          expect(restoreRssFeed(db, 3)).toBe(true);
          const [feed] = listRssFeeds(db, { q: "c.example" });
          expect(feed.status).toBe("active");
          expect(feed.deletedAt).toBeNull();
        },
      );
    } finally {
      fixture.cleanup();
    }
  });

  it("does nothing for a live row", () => {
    const fixture = createFeedsFixture();
    try {
      withWritableFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => {
          expect(restoreRssFeed(db, 1)).toBe(false);
        },
      );
    } finally {
      fixture.cleanup();
    }
  });
});

describe("openWritableFetchlinksDatabase", () => {
  it("opens the DB read-write so admin actions can mutate", () => {
    const fixture = createFeedsFixture();
    try {
      const database = openWritableFetchlinksDatabase({
        fetchlinksDbPath: fixture.dbPath,
      });
      try {
        expect(database.isOpen).toBe(true);
        database.exec(
          `INSERT INTO rss_feeds (feed_url, normalized_url, enabled, added_at)
           VALUES ('https://d.example/feed', 'https://d.example/feed', 1, '2026-03-01 00:00:00')`,
        );
        expect(listRssFeeds(database).map((f) => f.feedUrl)).toContain(
          "https://d.example/feed",
        );
      } finally {
        database.close();
      }
    } finally {
      fixture.cleanup();
    }
  });
});

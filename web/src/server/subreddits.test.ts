import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

import {
  addSubreddit,
  cleanSubredditName,
  countSubredditsByStatus,
  listSubreddits,
  normalizeSubredditName,
  restoreSubreddit,
  softDeleteSubreddit,
  withWritableSubredditsDatabase,
} from "./subreddits";

type Fixture = {
  dbPath: string;
  cleanup: () => void;
};

function createSubredditsFixture(): Fixture {
  const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-subs-"));
  const dbPath = path.join(directory, "fetchlinks.db");
  const database = new DatabaseSync(dbPath);
  database.exec(`
    CREATE TABLE subreddits (
      subreddit_id    INTEGER PRIMARY KEY,
      name            TEXT NOT NULL,
      normalized_name TEXT NOT NULL UNIQUE,
      enabled         INTEGER NOT NULL DEFAULT 1,
      added_at        TEXT NOT NULL,
      deleted_at      TEXT
    );

    CREATE TABLE reddit_state (
      subreddit TEXT PRIMARY KEY,
      last_seen_fullname TEXT,
      time_created TEXT
    );

    CREATE TABLE posts (
      post_id INTEGER PRIMARY KEY,
      source TEXT,
      source_type TEXT,
      date_created TEXT
    );

    INSERT INTO subreddits
      (subreddit_id, name, normalized_name, enabled, added_at, deleted_at)
    VALUES
      (1, 'Netsec', 'netsec', 1, '2026-01-01 00:00:00', NULL),
      (2, 'Python', 'python', 0, '2026-01-02 00:00:00', NULL),
      (3, 'Golang', 'golang', 0, '2026-01-03 00:00:00', '2026-02-01 00:00:00');

    INSERT INTO reddit_state (subreddit, last_seen_fullname, time_created)
    VALUES ('netsec', 't3_abc', '2026-03-01 00:00:00');

    INSERT INTO posts (post_id, source, source_type, date_created)
    VALUES
      (1, 'https://www.reddit.com/r/Netsec', 'reddit', '2026-03-02 00:00:00'),
      (2, 'https://www.reddit.com/r/Netsec', 'reddit', '2026-03-05 00:00:00');
  `);
  database.close();
  return {
    dbPath,
    cleanup: () => rmSync(directory, { force: true, recursive: true }),
  };
}

describe("normalizeSubredditName / cleanSubredditName", () => {
  it("strips an r/ prefix and lowercases", () => {
    expect(normalizeSubredditName("r/NetSec")).toBe("netsec");
    expect(normalizeSubredditName("/r/NetSec/")).toBe("netsec");
    expect(normalizeSubredditName("  Python  ")).toBe("python");
  });

  it("preserves case when only cleaning", () => {
    expect(cleanSubredditName("r/NetSec")).toBe("NetSec");
    expect(cleanSubredditName("/r/Python/")).toBe("Python");
  });
});

describe("listSubreddits", () => {
  it("returns all subreddits with computed status by default", () => {
    const fixture = createSubredditsFixture();
    try {
      const subs = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db),
      );
      const byName = Object.fromEntries(
        subs.map((s) => [s.normalizedName, s.status]),
      );
      expect(byName).toEqual({
        netsec: "active",
        python: "disabled",
        golang: "removed",
      });
    } finally {
      fixture.cleanup();
    }
  });

  it("filters by status", () => {
    const fixture = createSubredditsFixture();
    try {
      const active = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db, { status: "active" }),
      );
      expect(active.map((s) => s.normalizedName)).toEqual(["netsec"]);

      const removed = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db, { status: "removed" }),
      );
      expect(removed.map((s) => s.normalizedName)).toEqual(["golang"]);
    } finally {
      fixture.cleanup();
    }
  });

  it("filters by search substring against name and normalized_name", () => {
    const fixture = createSubredditsFixture();
    try {
      const matches = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db, { q: "PYTH" }),
      );
      expect(matches.map((s) => s.normalizedName)).toEqual(["python"]);
    } finally {
      fixture.cleanup();
    }
  });

  it("joins fetch state and derives newest post + canonical source", () => {
    const fixture = createSubredditsFixture();
    try {
      const subs = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db, { status: "active" }),
      );
      const netsec = subs[0];
      expect(netsec.lastFetchedAt).toBe("2026-03-01 00:00:00");
      expect(netsec.latestPostAt).toBe("2026-03-05 00:00:00");
      expect(netsec.postSource).toBe("https://www.reddit.com/r/Netsec");
    } finally {
      fixture.cleanup();
    }
  });
});

describe("countSubredditsByStatus", () => {
  it("counts active, disabled, removed and total", () => {
    const fixture = createSubredditsFixture();
    try {
      const counts = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => countSubredditsByStatus(db),
      );
      expect(counts).toEqual({
        active: 1,
        disabled: 1,
        removed: 1,
        total: 3,
      });
    } finally {
      fixture.cleanup();
    }
  });
});

describe("addSubreddit", () => {
  it("adds a new subreddit and stores cleaned + normalized names", () => {
    const fixture = createSubredditsFixture();
    try {
      const result = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addSubreddit(db, "r/Rust"),
      );
      expect(result.status).toBe("added");
      if (result.status === "added") {
        expect(result.subreddit.name).toBe("Rust");
        expect(result.subreddit.normalizedName).toBe("rust");
        expect(result.subreddit.status).toBe("active");
      }
    } finally {
      fixture.cleanup();
    }
  });

  it("reports an existing subreddit without inserting a duplicate", () => {
    const fixture = createSubredditsFixture();
    try {
      const result = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addSubreddit(db, "NETSEC"),
      );
      expect(result.status).toBe("exists");
    } finally {
      fixture.cleanup();
    }
  });

  it("rejects names with invalid characters", () => {
    const fixture = createSubredditsFixture();
    try {
      const result = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addSubreddit(db, "not a sub!"),
      );
      expect(result.status).toBe("invalid");
    } finally {
      fixture.cleanup();
    }
  });

  it("rejects an empty name", () => {
    const fixture = createSubredditsFixture();
    try {
      const result = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => addSubreddit(db, "   "),
      );
      expect(result.status).toBe("invalid");
    } finally {
      fixture.cleanup();
    }
  });
});

describe("softDeleteSubreddit / restoreSubreddit", () => {
  it("tombstones an active subreddit then restores it", () => {
    const fixture = createSubredditsFixture();
    try {
      const removed = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => softDeleteSubreddit(db, 1),
      );
      expect(removed).toBe(true);

      const afterDelete = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db, { status: "removed" }),
      );
      expect(afterDelete.map((s) => s.normalizedName)).toContain("netsec");

      const restored = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => restoreSubreddit(db, 1),
      );
      expect(restored).toBe(true);

      const afterRestore = withWritableSubredditsDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => listSubreddits(db, { status: "active" }),
      );
      expect(afterRestore.map((s) => s.normalizedName)).toContain("netsec");
    } finally {
      fixture.cleanup();
    }
  });

  it("creates the subreddits table on demand for a fresh database", () => {
    const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-fresh-"));
    const dbPath = path.join(directory, "fetchlinks.db");
    try {
      const counts = withWritableSubredditsDatabase({ fetchlinksDbPath: dbPath }, (db) =>
        countSubredditsByStatus(db),
      );
      expect(counts.total).toBe(0);
    } finally {
      rmSync(directory, { force: true, recursive: true });
    }
  });
});

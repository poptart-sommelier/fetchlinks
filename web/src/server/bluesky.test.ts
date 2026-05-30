import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

import { getBlueskyFollows, withWritableBlueskyDatabase } from "./bluesky";

type Fixture = {
  dbPath: string;
  cleanup: () => void;
};

function createFixture(seed: (db: DatabaseSync) => void): Fixture {
  const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-bsky-"));
  const dbPath = path.join(directory, "fetchlinks.db");
  const database = new DatabaseSync(dbPath);
  database.exec(`
    CREATE TABLE posts (
      idx              INTEGER PRIMARY KEY,
      source           TEXT NOT NULL,
      source_type      TEXT,
      author           TEXT,
      description      TEXT,
      direct_link      TEXT,
      date_created     TEXT NOT NULL,
      unique_id_string TEXT NOT NULL UNIQUE
    );
  `);
  seed(database);
  database.close();
  return {
    dbPath,
    cleanup: () => rmSync(directory, { force: true, recursive: true }),
  };
}

describe("getBlueskyFollows", () => {
  it("creates the schema on a fresh DB and returns an empty snapshot", () => {
    const fixture = createFixture(() => {});
    try {
      const snapshot = withWritableBlueskyDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => getBlueskyFollows(db),
      );
      expect(snapshot.follows).toEqual([]);
      expect(snapshot.lastSyncedAt).toBeNull();
    } finally {
      fixture.cleanup();
    }
  });

  it("returns follows sorted by handle with derived latest post and source", () => {
    const fixture = createFixture((db) => {
      db.exec(`
        CREATE TABLE bluesky_follows (
          did          TEXT PRIMARY KEY,
          handle       TEXT NOT NULL,
          display_name TEXT,
          synced_at    TEXT NOT NULL
        );
        INSERT INTO bluesky_follows (did, handle, display_name, synced_at) VALUES
          ('did:b', 'beta.bsky.social', 'Beta', '2026-02-01 00:00:00'),
          ('did:a', 'alpha.bsky.social', NULL, '2026-02-02 00:00:00');
        INSERT INTO posts
          (idx, source, source_type, author, description, direct_link, date_created, unique_id_string)
        VALUES
          (1, 'https://bsky.app/profile/alpha.bsky.social', 'bluesky', 'Alpha', 'd', 'l', '2026-01-10 00:00:00', 'u1'),
          (2, 'https://bsky.app/profile/alpha.bsky.social', 'bluesky', 'Alpha', 'd', 'l', '2026-01-20 00:00:00', 'u2');
      `);
    });
    try {
      const snapshot = withWritableBlueskyDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => getBlueskyFollows(db),
      );
      expect(snapshot.follows.map((f) => f.handle)).toEqual([
        "alpha.bsky.social",
        "beta.bsky.social",
      ]);
      const alpha = snapshot.follows[0];
      expect(alpha.displayName).toBeNull();
      expect(alpha.latestPostAt).toBe("2026-01-20 00:00:00");
      expect(alpha.postSource).toBe(
        "https://bsky.app/profile/alpha.bsky.social",
      );
      const beta = snapshot.follows[1];
      expect(beta.latestPostAt).toBeNull();
      expect(beta.postSource).toBeNull();
      expect(snapshot.lastSyncedAt).toBe("2026-02-02 00:00:00");
    } finally {
      fixture.cleanup();
    }
  });
});

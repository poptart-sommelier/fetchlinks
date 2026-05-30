import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

import { getMastodonFollows, withWritableMastodonDatabase } from "./mastodon";

type Fixture = {
  dbPath: string;
  cleanup: () => void;
};

function createFixture(seed: (db: DatabaseSync) => void): Fixture {
  const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-masto-"));
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

describe("getMastodonFollows", () => {
  it("creates the schema on a fresh DB and returns an empty snapshot", () => {
    const fixture = createFixture(() => {});
    try {
      const snapshot = withWritableMastodonDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => getMastodonFollows(db),
      );
      expect(snapshot.follows).toEqual([]);
      expect(snapshot.lastSyncedAt).toBeNull();
    } finally {
      fixture.cleanup();
    }
  });

  it("returns follows ordered by instance then acct with derived posts", () => {
    const fixture = createFixture((db) => {
      db.exec(`
        CREATE TABLE mastodon_follows (
          instance_name TEXT NOT NULL,
          account_id    TEXT NOT NULL,
          acct          TEXT NOT NULL,
          display_name  TEXT,
          url           TEXT,
          synced_at     TEXT NOT NULL,
          PRIMARY KEY (instance_name, account_id)
        );
        INSERT INTO mastodon_follows
          (instance_name, account_id, acct, display_name, url, synced_at)
        VALUES
          ('infosec', '2', 'zed', 'Zed', 'https://infosec.exchange/@zed', '2026-02-01 00:00:00'),
          ('infosec', '1', 'abe', NULL, 'https://infosec.exchange/@abe', '2026-02-03 00:00:00'),
          ('hachyderm', '5', 'kit', 'Kit', 'https://hachyderm.io/@kit', '2026-02-02 00:00:00');
        INSERT INTO posts
          (idx, source, source_type, author, description, direct_link, date_created, unique_id_string)
        VALUES
          (1, 'https://infosec.exchange/@abe', 'mastodon', 'Abe', 'd', 'l', '2026-01-15 00:00:00', 'u1');
      `);
    });
    try {
      const snapshot = withWritableMastodonDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (db) => getMastodonFollows(db),
      );
      expect(
        snapshot.follows.map((f) => `${f.instanceName}/${f.acct}`),
      ).toEqual(["hachyderm/kit", "infosec/abe", "infosec/zed"]);
      const abe = snapshot.follows.find((f) => f.acct === "abe");
      expect(abe?.latestPostAt).toBe("2026-01-15 00:00:00");
      expect(abe?.postSource).toBe("https://infosec.exchange/@abe");
      const zed = snapshot.follows.find((f) => f.acct === "zed");
      expect(zed?.latestPostAt).toBeNull();
      expect(snapshot.lastSyncedAt).toBe("2026-02-03 00:00:00");
    } finally {
      fixture.cleanup();
    }
  });
});

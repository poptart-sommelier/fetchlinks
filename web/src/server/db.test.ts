import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";

import {
  getPosts,
  getPostCount,
  openConfiguredFetchlinksDatabase,
  openFetchlinksDatabase,
  withFetchlinksDatabase,
  type FetchlinksDatabase,
} from "./db";

type Fixture = {
  dbPath: string;
  cleanup: () => void;
};

describe("openFetchlinksDatabase", () => {
  it("opens the configured SQLite database in read-only mode", () => {
    const fixture = createFixtureDatabase();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      expect(database.isOpen).toBe(true);
      expect(getPostCount(database)).toBe(2);
      expect(() => insertPost(database)).toThrow();
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("fails when a read-only database path does not exist", () => {
    const fixture = createTempPath();

    try {
      expect(() =>
        openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath }),
      ).toThrow();
    } finally {
      fixture.cleanup();
    }
  });
});

describe("openConfiguredFetchlinksDatabase", () => {
  it("loads configuration from the provided environment", () => {
    const fixture = createFixtureDatabase();
    const database = openConfiguredFetchlinksDatabase({
      FETCHLINKS_DB: fixture.dbPath,
    });

    try {
      expect(getPostCount(database)).toBe(2);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });
});

describe("withFetchlinksDatabase", () => {
  it("closes the database after running the callback", () => {
    const fixture = createFixtureDatabase();
    let callbackDatabase: FetchlinksDatabase | undefined;

    try {
      const count = withFetchlinksDatabase(
        { fetchlinksDbPath: fixture.dbPath },
        (database) => {
          callbackDatabase = database;
          return getPostCount(database);
        },
      );

      expect(count).toBe(2);
      expect(callbackDatabase?.isOpen).toBe(false);
    } finally {
      fixture.cleanup();
    }
  });
});

describe("getPosts", () => {
  it("returns posts newest first with pagination metadata", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const page = getPosts(database, { page: 1, pageSize: 2 });

      expect(page).toMatchObject({
        page: 1,
        pageSize: 2,
        totalPosts: 4,
        totalPages: 2,
        hasPreviousPage: false,
        hasNextPage: true,
      });
      expect(page.posts.map((post) => post.uniqueId)).toEqual([
        "reddit-2",
        "rss-4",
      ]);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("returns later pages using the requested page size", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const page = getPosts(database, { page: 2, pageSize: 2 });

      expect(page).toMatchObject({
        page: 2,
        pageSize: 2,
        totalPosts: 4,
        totalPages: 2,
        hasPreviousPage: true,
        hasNextPage: false,
      });
      expect(page.posts.map((post) => post.uniqueId)).toEqual([
        "mastodon-3",
        "rss-1",
      ]);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("groups normalized URLs by post and prefers unshortened hrefs", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const page = getPosts(database, { page: 1, pageSize: 1 });
      const [post] = page.posts;

      expect(post?.uniqueId).toBe("reddit-2");
      expect(post?.urls).toEqual([
        {
          id: 3,
          postId: 2,
          position: 0,
          originalUrl: "https://example.com/direct-b",
          urlHash: "hash-b0",
          unshortenedUrl: null,
          href: "https://example.com/direct-b",
        },
        {
          id: 2,
          postId: 2,
          position: 1,
          originalUrl: "https://short.example/b",
          urlHash: "hash-b1",
          unshortenedUrl: "https://example.com/unshortened-b",
          href: "https://example.com/unshortened-b",
        },
      ]);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("filters posts by source", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const page = getPosts(database, { source: "rss", page: 1, pageSize: 10 });

      expect(page).toMatchObject({ totalPosts: 2, totalPages: 1 });
      expect(page.posts.map((post) => post.uniqueId)).toEqual(["rss-4", "rss-1"]);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("filters posts by extracted URL domain using normalized hrefs", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const byType = getPosts(database, {
        sourceType: "rss",
        page: 1,
        pageSize: 10,
      });
      const byAuthor = getPosts(database, {
        author: "Linus",
        page: 1,
        pageSize: 10,
      });

      expect(byType.posts.map((post) => post.uniqueId)).toEqual([
        "rss-4",
        "rss-1",
      ]);
      expect(byAuthor.posts.map((post) => post.uniqueId)).toEqual(["rss-4"]);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("searches post fields and extracted URLs", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const urlMatch = getPosts(database, { q: "unshortened-B" });
      const authorMatch = getPosts(database, { q: "linus" });

      expect(urlMatch.posts.map((post) => post.uniqueId)).toEqual(["reddit-2"]);
      expect(authorMatch.posts.map((post) => post.uniqueId)).toEqual(["rss-4"]);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("combines source, domain, and search filters", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      const page = getPosts(database, {
        sourceType: "rss",
        author: "Linus",
        q: "tie-break",
      });

      expect(page.posts.map((post) => post.uniqueId)).toEqual(["rss-4"]);
      expect(page.totalPosts).toBe(1);
    } finally {
      database.close();
      fixture.cleanup();
    }
  });

  it("rejects invalid pagination options", () => {
    const fixture = createPostsQueryFixture();
    const database = openFetchlinksDatabase({ fetchlinksDbPath: fixture.dbPath });

    try {
      expect(() => getPosts(database, { page: 0 })).toThrowError(
        /page must be a positive integer/,
      );
      expect(() => getPosts(database, { pageSize: 1.5 })).toThrowError(
        /pageSize must be a positive integer/,
      );
    } finally {
      database.close();
      fixture.cleanup();
    }
  });
});

function createFixtureDatabase(): Fixture {
  return createDatabaseWithSql(`
    CREATE TABLE posts (
      idx INTEGER PRIMARY KEY,
      source TEXT NOT NULL,
      source_type TEXT,
      author TEXT,
      description TEXT,
      direct_link TEXT,
      date_created TEXT NOT NULL,
      unique_id_string TEXT NOT NULL
    );

    CREATE TABLE post_urls (
      idx INTEGER PRIMARY KEY,
      post_id INTEGER NOT NULL,
      position INTEGER NOT NULL,
      url TEXT NOT NULL,
      url_hash TEXT NOT NULL,
      unshortened_url TEXT,
      FOREIGN KEY (post_id) REFERENCES posts(idx)
    );

    INSERT INTO posts (
      idx,
      source,
      source_type,
      author,
      description,
      direct_link,
      date_created,
      unique_id_string
    ) VALUES
      (1, 'rss', 'rss', 'Ada', 'First post', 'https://example.com/first', '2026-04-27T10:00:00Z', 'rss-1'),
      (2, 'reddit', 'reddit', 'Grace', 'Second post', 'https://example.com/second', '2026-04-28T10:00:00Z', 'reddit-2');

    INSERT INTO post_urls (
      idx,
      post_id,
      position,
      url,
      url_hash,
      unshortened_url
    ) VALUES
      (1, 1, 0, 'https://short.example/a', 'hash-a', 'https://example.com/a'),
      (2, 2, 0, 'https://example.com/b', 'hash-b', NULL);
  `);
}

function createPostsQueryFixture(): Fixture {
  return createDatabaseWithSql(`
    CREATE TABLE posts (
      idx INTEGER PRIMARY KEY,
      source TEXT NOT NULL,
      source_type TEXT,
      author TEXT,
      description TEXT,
      direct_link TEXT,
      date_created TEXT NOT NULL,
      unique_id_string TEXT NOT NULL
    );

    CREATE TABLE post_urls (
      idx INTEGER PRIMARY KEY,
      post_id INTEGER NOT NULL,
      position INTEGER NOT NULL,
      url TEXT NOT NULL,
      url_hash TEXT NOT NULL,
      unshortened_url TEXT,
      FOREIGN KEY (post_id) REFERENCES posts(idx)
    );

    INSERT INTO posts (
      idx,
      source,
      source_type,
      author,
      description,
      direct_link,
      date_created,
      unique_id_string
    ) VALUES
      (1, 'rss', 'rss', 'Ada', 'Oldest post', 'https://example.com/first', '2026-04-25T10:00:00Z', 'rss-1'),
      (2, 'reddit', 'reddit', 'Grace', 'Newest post', 'https://example.com/second', '2026-04-28T10:00:00Z', 'reddit-2'),
      (3, 'mastodon', 'mastodon', NULL, NULL, NULL, '2026-04-26T10:00:00Z', 'mastodon-3'),
      (4, 'rss', 'rss', 'Linus', 'Tie-break post', 'https://example.com/fourth', '2026-04-26T10:00:00Z', 'rss-4');

    INSERT INTO post_urls (
      idx,
      post_id,
      position,
      url,
      url_hash,
      unshortened_url
    ) VALUES
      (1, 1, 0, 'https://short.example/a', 'hash-a', 'https://example.com/a'),
      (2, 2, 1, 'https://short.example/b', 'hash-b1', 'https://example.com/unshortened-b'),
      (3, 2, 0, 'https://example.com/direct-b', 'hash-b0', NULL),
      (4, 4, 0, 'https://docs.example.org/d', 'hash-d', NULL);
  `);
}

function createDatabaseWithSql(sql: string): Fixture {
  const fixture = createTempPath();
  const database = new DatabaseSync(fixture.dbPath);

  database.exec(sql);

  database.close();

  return fixture;
}

function createTempPath(): Fixture {
  const directory = mkdtempSync(path.join(tmpdir(), "fetchlinks-web-"));

  return {
    dbPath: path.join(directory, "fetchlinks.db"),
    cleanup: () => rmSync(directory, { force: true, recursive: true }),
  };
}

function insertPost(database: FetchlinksDatabase): void {
  database.exec(`
    INSERT INTO posts (
      idx,
      source,
      source_type,
      author,
      description,
      direct_link,
      date_created,
      unique_id_string
    ) VALUES (
      3,
      'rss',
      'rss',
      'Read Only',
      'This should fail',
      'https://example.com/readonly',
      '2026-04-29T10:00:00Z',
      'rss-3'
    );
  `);
}
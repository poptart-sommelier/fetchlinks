import { expect, it } from "vitest";

import { getPostCount, getPosts } from "./db";
import { describePostgres, usePostgres } from "./test-support/postgres";

type SeedPost = {
  uniqueId: string;
  source: string;
  sourceType: string;
  author?: string;
  description?: string;
  directLink?: string;
  postedAt: string;
  urls?: string[];
};

describePostgres("posts read model", () => {
  const pg = usePostgres();

  async function seed(posts: SeedPost[]): Promise<void> {
    for (const post of posts) {
      const [row] = (await pg.exec(
        `INSERT INTO content.posts
           (unique_id, source, source_type, author, description, direct_link, posted_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         RETURNING post_id`,
        [
          post.uniqueId,
          post.source,
          post.sourceType,
          post.author ?? "",
          post.description ?? "",
          post.directLink ?? "",
          post.postedAt,
        ],
      )) as { post_id: string }[];

      const urls = post.urls ?? [];
      for (const [index, url] of urls.entries()) {
        await pg.exec(
          `INSERT INTO content.post_urls (post_id, position, url, url_hash)
           VALUES ($1, $2, $3, $4)`,
          [row.post_id, index, url, `${post.uniqueId}-${index}`],
        );
      }
    }
  }

  const FOUR_POSTS: SeedPost[] = [
    {
      uniqueId: "rss-1",
      source: "https://example.com/feed",
      sourceType: "rss",
      author: "Ada",
      description: "First article",
      postedAt: "2026-01-01T09:00:00Z",
      urls: ["https://example.com/one"],
    },
    {
      uniqueId: "reddit-2",
      source: "https://www.reddit.com/r/programming",
      sourceType: "reddit",
      author: "grace",
      description: "A discussion",
      postedAt: "2026-01-04T09:00:00Z",
    },
    {
      uniqueId: "bluesky-3",
      source: "https://bsky.app/profile/someone.bsky.social",
      sourceType: "bluesky",
      author: "someone",
      description: "A skeet",
      postedAt: "2026-01-02T09:00:00Z",
    },
    {
      uniqueId: "rss-4",
      source: "https://example.org/feed",
      sourceType: "rss",
      description: "Another article",
      postedAt: "2026-01-03T09:00:00Z",
    },
  ];

  it("counts every stored post", async () => {
    await seed(FOUR_POSTS);

    await expect(getPostCount(pg.sql)).resolves.toBe(4);
  });

  it("reports zero on an empty database", async () => {
    await expect(getPostCount(pg.sql)).resolves.toBe(0);
  });

  it("returns posts newest first with pagination metadata", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, { page: 1, pageSize: 2 });

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
  });

  it("returns later pages using the requested page size", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, { page: 2, pageSize: 2 });

    expect(page).toMatchObject({
      page: 2,
      totalPages: 2,
      hasPreviousPage: true,
      hasNextPage: false,
    });
    expect(page.posts.map((post) => post.uniqueId)).toEqual([
      "bluesky-3",
      "rss-1",
    ]);
  });

  it("returns an empty page past the end without reporting a next page", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, { page: 9, pageSize: 2 });

    expect(page.posts).toEqual([]);
    expect(page.hasNextPage).toBe(false);
    expect(page.hasPreviousPage).toBe(true);
  });

  // The old SQLite layer stored "YYYY-MM-DD HH:MM:SS" with no zone, which the
  // browser then read as local time. Timestamps must come back explicitly UTC.
  it("renders timestamps as ISO-8601 UTC", async () => {
    await seed([
      {
        uniqueId: "tz-1",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-03-04T05:06:07+02:00",
      },
    ]);

    const page = await getPosts(pg.sql);

    expect(page.posts[0]?.dateCreated).toBe("2026-03-04T03:06:07Z");
    expect(Number.isNaN(new Date(page.posts[0]!.dateCreated).valueOf())).toBe(
      false,
    );
  });

  it("attaches each post's urls in position order", async () => {
    await seed([
      {
        uniqueId: "many-urls",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-01T00:00:00Z",
        urls: ["https://a.example", "https://b.example", "https://c.example"],
      },
    ]);

    const page = await getPosts(pg.sql);

    expect(page.posts[0]?.urls.map((url) => url.originalUrl)).toEqual([
      "https://a.example",
      "https://b.example",
      "https://c.example",
    ]);
    expect(page.posts[0]?.urls.map((url) => url.position)).toEqual([0, 1, 2]);
  });

  it("prefers the unshortened url when one has been resolved", async () => {
    await seed([
      {
        uniqueId: "shortened",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-01T00:00:00Z",
        urls: ["https://t.co/abc"],
      },
    ]);
    await pg.exec(
      "UPDATE content.post_urls SET unshortened_url = $1 WHERE url = $2",
      ["https://example.com/full-article", "https://t.co/abc"],
    );

    const page = await getPosts(pg.sql);

    expect(page.posts[0]?.urls[0]?.href).toBe(
      "https://example.com/full-article",
    );
    expect(page.posts[0]?.urls[0]?.originalUrl).toBe("https://t.co/abc");
  });

  it("does not leak urls between posts", async () => {
    await seed([
      {
        uniqueId: "a",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-02T00:00:00Z",
        urls: ["https://a.example"],
      },
      {
        uniqueId: "b",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-01T00:00:00Z",
        urls: ["https://b.example"],
      },
    ]);

    const page = await getPosts(pg.sql);

    expect(page.posts.map((post) => post.urls.map((url) => url.href))).toEqual([
      ["https://a.example"],
      ["https://b.example"],
    ]);
  });

  it("filters by source type", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, { sourceType: "rss" });

    expect(page.totalPosts).toBe(2);
    expect(page.posts.map((post) => post.uniqueId)).toEqual(["rss-4", "rss-1"]);
  });

  it("ignores an unrecognised source type instead of returning nothing", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, { sourceType: "myspace" });

    expect(page.totalPosts).toBe(4);
  });

  it("filters by exact source and author", async () => {
    await seed(FOUR_POSTS);

    await expect(
      getPosts(pg.sql, { source: "https://example.org/feed" }),
    ).resolves.toMatchObject({ totalPosts: 1 });
    await expect(getPosts(pg.sql, { author: "grace" })).resolves.toMatchObject({
      totalPosts: 1,
    });
  });

  it("combines filters with AND", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, {
      sourceType: "rss",
      author: "Ada",
    });

    expect(page.posts.map((post) => post.uniqueId)).toEqual(["rss-1"]);
  });

  it("searches description, source, author, direct link and urls", async () => {
    await seed([
      {
        uniqueId: "in-description",
        source: "https://example.com/feed",
        sourceType: "rss",
        description: "About NEEDLE things",
        postedAt: "2026-01-05T00:00:00Z",
      },
      {
        uniqueId: "in-author",
        source: "https://example.com/feed",
        sourceType: "rss",
        author: "needle",
        postedAt: "2026-01-04T00:00:00Z",
      },
      {
        uniqueId: "in-direct-link",
        source: "https://example.com/feed",
        sourceType: "rss",
        directLink: "https://example.com/needle",
        postedAt: "2026-01-03T00:00:00Z",
      },
      {
        uniqueId: "in-url",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-02T00:00:00Z",
        urls: ["https://elsewhere.example/needle"],
      },
      {
        uniqueId: "unrelated",
        source: "https://example.com/feed",
        sourceType: "rss",
        description: "Nothing to see",
        postedAt: "2026-01-01T00:00:00Z",
      },
    ]);

    const page = await getPosts(pg.sql, { q: "needle" });

    expect(page.posts.map((post) => post.uniqueId).sort()).toEqual([
      "in-author",
      "in-description",
      "in-direct-link",
      "in-url",
    ]);
  });

  it("matches an unshortened url as well as the original", async () => {
    await seed([
      {
        uniqueId: "shortened",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-01T00:00:00Z",
        urls: ["https://t.co/xyz"],
      },
    ]);
    await pg.exec(
      "UPDATE content.post_urls SET unshortened_url = $1 WHERE url = $2",
      ["https://example.com/needle", "https://t.co/xyz"],
    );

    await expect(getPosts(pg.sql, { q: "needle" })).resolves.toMatchObject({
      totalPosts: 1,
    });
  });

  it("treats LIKE wildcards in the search term as literal characters", async () => {
    await seed([
      {
        uniqueId: "literal-percent",
        source: "https://example.com/feed",
        sourceType: "rss",
        description: "Now 100% faster",
        postedAt: "2026-01-02T00:00:00Z",
      },
      {
        uniqueId: "no-percent",
        source: "https://example.com/feed",
        sourceType: "rss",
        description: "Just as fast",
        postedAt: "2026-01-01T00:00:00Z",
      },
    ]);

    const page = await getPosts(pg.sql, { q: "100%" });

    expect(page.posts.map((post) => post.uniqueId)).toEqual([
      "literal-percent",
    ]);
  });

  it("searches without regard to case", async () => {
    await seed([
      {
        uniqueId: "mixed-case",
        source: "https://example.com/feed",
        sourceType: "rss",
        description: "A Story About Postgres",
        postedAt: "2026-01-01T00:00:00Z",
      },
    ]);

    await expect(getPosts(pg.sql, { q: "POSTGRES" })).resolves.toMatchObject({
      totalPosts: 1,
    });
  });

  it("counts only the filtered rows", async () => {
    await seed(FOUR_POSTS);

    const page = await getPosts(pg.sql, { sourceType: "rss", pageSize: 1 });

    expect(page.totalPosts).toBe(2);
    expect(page.totalPages).toBe(2);
  });

  it("rejects a non-positive page or page size", async () => {
    await expect(getPosts(pg.sql, { page: 0 })).rejects.toThrowError(RangeError);
    await expect(getPosts(pg.sql, { pageSize: -1 })).rejects.toThrowError(
      RangeError,
    );
    await expect(getPosts(pg.sql, { page: 1.5 })).rejects.toThrowError(
      RangeError,
    );
  });
});

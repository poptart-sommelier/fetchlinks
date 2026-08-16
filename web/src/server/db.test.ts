import { expect, it } from "vitest";

import { composeTargetKey } from "./curation";
import { getPostCount, getPosts } from "./db";
import { restoreSubreddit, softDeleteSubreddit } from "./subreddits";
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

  async function addOccurrence(
    uniqueId: string,
    {
      sourceType,
      channelKey,
      channelLabel,
      actorKey,
      actorLabel,
      source,
      directLink,
    }: {
      sourceType: string;
      channelKey: string;
      channelLabel: string;
      actorKey: string;
      actorLabel: string;
      source: string;
      directLink: string;
    },
  ): Promise<void> {
    await pg.exec(
      `INSERT INTO content.post_occurrences
         (post_id, source_type, channel_key, channel_label, actor_key,
          actor_label, source, direct_link, posted_at)
       SELECT post_id, $2, $3, $4, $5, $6, $7, $8, posted_at
       FROM content.posts
       WHERE unique_id = $1`,
      [
        uniqueId,
        sourceType,
        channelKey,
        channelLabel,
        actorKey,
        actorLabel,
        source,
        directLink,
      ],
    );
  }

  async function mute(
    type: "post" | "channel" | "actor" | "domain",
    key: string,
  ): Promise<void> {
    await pg.exec(
      `INSERT INTO curation.mutes (target_type, target_key, target_label)
       VALUES ($1, $2, $2)`,
      [type, key],
    );
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

  it("hides a muted post publicly but keeps it available to owner mode", async () => {
    await seed([FOUR_POSTS[0]!]);
    await mute("post", "rss-1");

    await expect(getPosts(pg.sql)).resolves.toMatchObject({
      totalPosts: 0,
      posts: [],
    });
    await expect(
      getPosts(pg.sql, { includeMuted: true }),
    ).resolves.toMatchObject({
      totalPosts: 1,
      posts: [{ uniqueId: "rss-1" }],
    });
  });

  it("keeps a post while one origin survives and names that visible origin", async () => {
    await seed([
      {
        uniqueId: "two-origins",
        source: "https://www.reddit.com/r/muted",
        sourceType: "reddit",
        author: "Muted author",
        description: "Shared article",
        directLink: "https://reddit.example/muted",
        postedAt: "2026-01-01T00:00:00Z",
        urls: ["https://article.example/story"],
      },
    ]);
    await addOccurrence("two-origins", {
      sourceType: "reddit",
      channelKey: "muted",
      channelLabel: "r/muted",
      actorKey: "muted-author",
      actorLabel: "Muted author",
      source: "https://www.reddit.com/r/muted",
      directLink: "https://reddit.example/muted",
    });
    await addOccurrence("two-origins", {
      sourceType: "mastodon",
      channelKey: "social.example",
      channelLabel: "social.example",
      actorKey: "visible-author",
      actorLabel: "Visible author",
      source: "https://social.example/@visible-author",
      directLink: "https://social.example/@visible-author/1",
    });
    await mute("channel", composeTargetKey("reddit", "muted"));

    const publicPage = await getPosts(pg.sql);

    expect(publicPage.totalPosts).toBe(1);
    expect(publicPage.posts[0]).toMatchObject({
      sourceType: "mastodon",
      source: "https://social.example/@visible-author",
      author: "Visible author",
      directLink: "https://social.example/@visible-author/1",
    });
    expect(publicPage.posts[0]?.occurrences).toHaveLength(1);
    await expect(
      getPosts(pg.sql, { source: "https://www.reddit.com/r/muted" }),
    ).resolves.toMatchObject({ totalPosts: 0 });
    await expect(
      getPosts(pg.sql, { author: "Muted author" }),
    ).resolves.toMatchObject({ totalPosts: 0 });
    await expect(
      getPosts(pg.sql, { q: "reddit.example/muted" }),
    ).resolves.toMatchObject({ totalPosts: 0 });
    await expect(
      getPosts(pg.sql, { sourceType: "mastodon" }),
    ).resolves.toMatchObject({ totalPosts: 1 });

    await mute("actor", composeTargetKey("mastodon", "visible-author"));

    await expect(getPosts(pg.sql)).resolves.toMatchObject({ totalPosts: 0 });
    await expect(
      getPosts(pg.sql, { includeMuted: true }),
    ).resolves.toMatchObject({
      totalPosts: 1,
      posts: [{ occurrences: [{}, {}] }],
    });
  });

  it("removes muted domains from a card and hides it when no link survives", async () => {
    await seed([
      {
        uniqueId: "two-links",
        source: "https://example.com/feed",
        sourceType: "rss",
        postedAt: "2026-01-01T00:00:00Z",
        urls: ["https://a.example/story", "https://b.example/story"],
      },
    ]);
    await mute("domain", "a.example");

    const publicPage = await getPosts(pg.sql);

    expect(publicPage.totalPosts).toBe(1);
    expect(publicPage.posts[0]?.urls.map((url) => url.urlHost)).toEqual([
      "b.example",
    ]);

    await mute("domain", "b.example");

    await expect(getPosts(pg.sql)).resolves.toMatchObject({ totalPosts: 0 });
    await expect(
      getPosts(pg.sql, { includeMuted: true }),
    ).resolves.toMatchObject({
      totalPosts: 1,
      posts: [{ urls: [{}, {}] }],
    });
  });

  it("filters mutes before counting and filling a page", async () => {
    await seed(FOUR_POSTS);
    await mute("post", "reddit-2");
    await mute("post", "rss-4");

    const page = await getPosts(pg.sql, { page: 1, pageSize: 2 });

    expect(page).toMatchObject({
      totalPosts: 2,
      totalPages: 1,
      hasNextPage: false,
    });
    expect(page.posts.map((post) => post.uniqueId)).toEqual([
      "bluesky-3",
      "rss-1",
    ]);
  });

  it("filters removed catalog origins before counting and pagination", async () => {
      await seed([
        {
          uniqueId: "newest-removed",
          source: "https://removed.example/feed",
          sourceType: "rss",
          description: "Removed newest",
          postedAt: "2026-01-03T00:00:00Z",
        },
        {
          uniqueId: "middle-visible",
          source: "https://middle.example/feed",
          sourceType: "rss",
          description: "Visible middle",
          postedAt: "2026-01-02T00:00:00Z",
        },
        {
          uniqueId: "old-visible",
          source: "https://old.example/feed",
          sourceType: "rss",
          description: "Visible old",
          postedAt: "2026-01-01T00:00:00Z",
        },
      ]);
      for (const [uniqueId, channelKey] of [
        ["newest-removed", "https://removed.example/feed"],
        ["middle-visible", "https://middle.example/feed"],
        ["old-visible", "https://old.example/feed"],
      ] as const) {
        await addOccurrence(uniqueId, {
          sourceType: "rss",
          channelKey,
          channelLabel: channelKey,
          actorKey: "",
          actorLabel: "",
          source: channelKey,
          directLink: `${channelKey}/post`,
        });
        await pg.exec(
          `INSERT INTO catalog.rss_feeds
             (feed_url, normalized_url, enabled, added_at)
           VALUES ($1, $1, true, now())`,
          [channelKey],
        );
      }
      await pg.exec(
        `UPDATE catalog.rss_feeds
         SET deleted_at = now(), enabled = false
         WHERE normalized_url = 'https://removed.example/feed'`,
      );

      await expect(getPosts(pg.sql, { pageSize: 1 })).resolves.toMatchObject({
        totalPosts: 2,
        totalPages: 2,
        posts: [{ uniqueId: "middle-visible" }],
      });
      await expect(
        getPosts(pg.sql, { includeMuted: true, pageSize: 5 }),
      ).resolves.toMatchObject({
        totalPosts: 3,
        posts: [
          {
            uniqueId: "newest-removed",
            occurrences: [{ channelKey: "https://removed.example/feed" }],
          },
          { uniqueId: "middle-visible" },
          { uniqueId: "old-visible" },
        ],
      });
  });

  it.each([
    {
      kind: "RSS",
      sourceType: "rss",
      channelKey: "https://disabled.example/feed",
      source: "https://disabled.example/feed",
      catalogSql: `INSERT INTO catalog.rss_feeds
        (feed_url, normalized_url, enabled, added_at)
        VALUES ('https://disabled.example/feed', 'https://disabled.example/feed',
          false, now())`,
    },
    {
      kind: "subreddit",
      sourceType: "reddit",
      channelKey: "disabled",
      source: "https://www.reddit.com/r/disabled",
      catalogSql: `INSERT INTO catalog.subreddits
        (name, normalized_name, enabled, added_at)
        VALUES ('Disabled', 'disabled', false, now())`,
    },
  ])(
    "filters a disabled $kind origin before counting and pagination",
    async ({ catalogSql, channelKey, source, sourceType }) => {
      await seed([
        {
          uniqueId: "newest-disabled",
          source,
          sourceType,
          description: "Disabled newest",
          postedAt: "2026-01-03T00:00:00Z",
        },
        {
          uniqueId: "middle-visible",
          source: "https://social.example/@middle",
          sourceType: "mastodon",
          description: "Visible middle",
          postedAt: "2026-01-02T00:00:00Z",
        },
        {
          uniqueId: "old-visible",
          source: "https://social.example/@old",
          sourceType: "mastodon",
          description: "Visible old",
          postedAt: "2026-01-01T00:00:00Z",
        },
      ]);
      await addOccurrence("newest-disabled", {
        sourceType,
        channelKey,
        channelLabel: channelKey,
        actorKey: "",
        actorLabel: "",
        source,
        directLink: `${source}/post`,
      });
      await pg.exec(catalogSql);

      await expect(getPosts(pg.sql, { pageSize: 1 })).resolves.toMatchObject({
        totalPosts: 2,
        totalPages: 2,
        posts: [{ uniqueId: "middle-visible" }],
      });
      await expect(
        getPosts(pg.sql, { includeMuted: true, pageSize: 5 }),
      ).resolves.toMatchObject({
        totalPosts: 3,
        posts: [{ uniqueId: "newest-disabled" }, {}, {}],
      });
    },
  );

  it("keeps an article public when another active unmuted catalog origin survives", async () => {
    await seed([
      {
        uniqueId: "disabled-and-live",
        source: "https://disabled.example/feed",
        sourceType: "rss",
        description: "Shared by active source",
        postedAt: "2026-01-01T00:00:00Z",
      },
    ]);
    await addOccurrence("disabled-and-live", {
      sourceType: "rss",
      channelKey: "https://disabled.example/feed",
      channelLabel: "Disabled Feed",
      actorKey: "",
      actorLabel: "",
      source: "https://disabled.example/feed",
      directLink: "https://disabled.example/post",
    });
    await addOccurrence("disabled-and-live", {
      sourceType: "reddit",
      channelKey: "active",
      channelLabel: "r/active",
      actorKey: "active-author",
      actorLabel: "Active Author",
      source: "https://www.reddit.com/r/active",
      directLink: "https://reddit.example/active",
    });
    await pg.exec(
      `INSERT INTO catalog.rss_feeds
         (feed_url, normalized_url, enabled, added_at)
       VALUES ('https://disabled.example/feed', 'https://disabled.example/feed',
         false, now())`,
    );
    await pg.exec(
      `INSERT INTO catalog.subreddits
         (name, normalized_name, enabled, added_at)
       VALUES ('Active', 'active', true, now())`,
    );

    await expect(getPosts(pg.sql)).resolves.toMatchObject({
      totalPosts: 1,
      posts: [
        {
          sourceType: "reddit",
          occurrences: [{ sourceType: "reddit", channelKey: "active" }],
        },
      ],
    });

    await mute("channel", composeTargetKey("reddit", "active"));

    await expect(getPosts(pg.sql)).resolves.toMatchObject({ totalPosts: 0 });
  });

  it("keeps an article public when another unmuted origin survives removal", async () => {
      await seed([
        {
          uniqueId: "removed-and-live",
          source: "https://removed.example/feed",
          sourceType: "rss",
          description: "Shared elsewhere",
          postedAt: "2026-01-01T00:00:00Z",
        },
      ]);
      await addOccurrence("removed-and-live", {
        sourceType: "rss",
        channelKey: "https://removed.example/feed",
        channelLabel: "Removed Feed",
        actorKey: "",
        actorLabel: "",
        source: "https://removed.example/feed",
        directLink: "https://removed.example/post",
      });
      await addOccurrence("removed-and-live", {
        sourceType: "mastodon",
        channelKey: "social.example",
        channelLabel: "social.example",
        actorKey: "live-account",
        actorLabel: "Live Account",
        source: "https://social.example",
        directLink: "https://social.example/post",
      });
      await pg.exec(
        `INSERT INTO catalog.rss_feeds
           (feed_url, normalized_url, enabled, added_at, deleted_at)
         VALUES
           ('https://removed.example/feed', 'https://removed.example/feed',
            false, now(), now())`,
      );

      await expect(getPosts(pg.sql)).resolves.toMatchObject({
        totalPosts: 1,
        posts: [
          {
            sourceType: "mastodon",
            occurrences: [{ sourceType: "mastodon" }],
          },
        ],
      });
  });

  it("restoring a source leaves its explicit mute in force", async () => {
      await seed([
        {
          uniqueId: "muted-restored",
          source: "https://www.reddit.com/r/test",
          sourceType: "reddit",
          description: "Still muted",
          postedAt: "2026-01-01T00:00:00Z",
        },
      ]);
      await addOccurrence("muted-restored", {
        sourceType: "reddit",
        channelKey: "test",
        channelLabel: "r/test",
        actorKey: "author",
        actorLabel: "Author",
        source: "https://www.reddit.com/r/test",
        directLink: "https://reddit.example/post",
      });
      const [subreddit] = (await pg.exec(
        `INSERT INTO catalog.subreddits
           (name, normalized_name, enabled, added_at)
         VALUES ('Test', 'test', true, now())
         RETURNING subreddit_id::int AS id`,
      )) as { id: number }[];
      if (!subreddit) throw new Error("expected a subreddit");
      await softDeleteSubreddit(pg.sql, subreddit.id);
      await mute("channel", composeTargetKey("reddit", "test"));
      await restoreSubreddit(pg.sql, subreddit.id);

      await expect(getPosts(pg.sql)).resolves.toMatchObject({ totalPosts: 0 });
      await expect(
        getPosts(pg.sql, { includeMuted: true }),
      ).resolves.toMatchObject({
        totalPosts: 1,
        posts: [{ uniqueId: "muted-restored", occurrences: [{}] }],
      });
  });

  it("does not search text that exists only in a muted link", async () => {
    await seed([
      {
        uniqueId: "search-links",
        source: "https://example.com/feed",
        sourceType: "rss",
        description: "Read muted.example/… and discuss the result",
        postedAt: "2026-01-01T00:00:00Z",
        urls: [
          "https://muted.example/secret-needle",
          "https://visible.example/story",
        ],
      },
    ]);
    await mute("domain", "muted.example");

    const publicPage = await getPosts(pg.sql);
    expect(publicPage.posts[0]?.description).toBe("Read and discuss the result");
    expect(publicPage.posts[0]?.description).not.toContain("muted.example");

    await expect(getPosts(pg.sql, { q: "muted.example" })).resolves.toMatchObject(
      { totalPosts: 0 },
    );
    await expect(
      getPosts(pg.sql, { includeMuted: true, q: "muted.example" }),
    ).resolves.toMatchObject({
      totalPosts: 1,
      posts: [{ description: expect.stringContaining("muted.example") }],
    });
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

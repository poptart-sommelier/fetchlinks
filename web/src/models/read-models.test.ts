import { describe, expect, expectTypeOf, it } from "vitest";

import type {
  PostPage,
  PostSummary,
  PostUrl,
  SourceType,
} from "./read-models";

describe("read models", () => {
  it("models a post with normalized URLs", () => {
    const url = {
      id: 10,
      postId: 1,
      position: 0,
      originalUrl: "https://short.example/a",
      urlHash: "hash-a",
      unshortenedUrl: "https://example.com/a",
      href: "https://example.com/a",
    } satisfies PostUrl;

    const post = {
      id: 1,
      source: "https://example.com/feed",
      sourceType: "rss",
      author: null,
      description: "A collected link",
      directLink: "https://example.com/post",
      dateCreated: "2026-04-28T12:00:00Z",
      uniqueId: "rss-1",
      urls: [url],
    } satisfies PostSummary;

    expect(post.urls[0]?.href).toBe("https://example.com/a");
    expectTypeOf<PostSummary["urls"][number]>().toEqualTypeOf<PostUrl>();
    expectTypeOf<PostUrl["unshortenedUrl"]>().toEqualTypeOf<string | null>();
    expectTypeOf<PostSummary["sourceType"]>().toEqualTypeOf<SourceType | null>();
  });

  it("models paginated post results", () => {
    const page = {
      posts: [],
      page: 1,
      pageSize: 50,
      totalPosts: 0,
      totalPages: 0,
      hasPreviousPage: false,
      hasNextPage: false,
    } satisfies PostPage;

    expect(page.hasNextPage).toBe(false);
    expectTypeOf<PostPage["posts"][number]>().toEqualTypeOf<PostSummary>();
  });

  it("constrains source type to the supported ingest sources", () => {
    const types: SourceType[] = ["rss", "reddit", "bluesky", "mastodon"];

    expect(types).toHaveLength(4);
    expectTypeOf<SourceType>().toEqualTypeOf<
      "rss" | "reddit" | "bluesky" | "mastodon"
    >();
  });
});
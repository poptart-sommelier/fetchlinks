import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PostPage } from "../models/read-models";
import { LatestPostsView, loadLatestPosts } from "./page";

describe("Home", () => {
  it("renders posts with the source label, extracted URL rows, and pagination links", () => {
    const markup = renderToStaticMarkup(<LatestPostsView result={createReadyResult()} />);

    expect(markup).toContain("Latest posts");
    expect(markup).toContain("Newest post");
    expect(markup).toContain("Grace");
    expect(markup).toContain("reddit/test");
    expect(markup).toContain('href="/?source_type=reddit&amp;author=Grace"');
    expect(markup).toContain("Apr 28, 2026, 10:00 AM");
    expect(markup).toContain('aria-label="Post links"');
    expect(markup).toContain('class="post-link-list"');
    expect(markup).toContain('class="post-link-row"');
    expect(markup).toContain('class="post-link-host"');
    expect(markup).toContain('class="post-link-path"');
    expect(markup).toContain('class="post-source-action"');
    expect(markup).toContain("source");
    expect(markup).toContain('href="https://example.com/unshortened-b"');
    expect(markup).toContain('href="https://example.com/direct-b"');
    expect(markup).not.toContain(">link 1<");
    expect(markup).toContain('href="/?page=2"');
  });

  it("renders a search-only filter bar and preserves filters in pagination links", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          filters: { sourceType: "reddit", author: "Grace", q: "AI" },
          page: createPostPage({ totalPosts: 75 }),
        })}
      />,
    );

    expect(markup).toContain('aria-label="75 matching posts"');
    expect(markup).toContain('name="q"');
    expect(markup).toContain('aria-label="Filter posts"');
    expect(markup).toContain('value="AI"');
    expect(markup).not.toContain('name="source"');
    expect(markup).not.toContain('name="domain"');
    expect(markup).toContain("Clear");
    expect(markup).toContain('href="/"');
    expect(markup).toContain(
      'href="/?source_type=reddit&amp;author=Grace&amp;q=AI&amp;page=2"',
    );
  });

  it("renders the RSS source label as a filter linking by source_type and source", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          page: createPostPage({
            posts: [createRssPost()],
          }),
        })}
      />,
    );

    expect(markup).toContain(">rss<");
    expect(markup).toContain("example.com/blog");
    expect(markup).toContain(
      'href="/?source_type=rss&amp;source=https%3A%2F%2Fexample.com%2Fblog"',
    );
  });

  it("renders an empty state when no posts exist", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          page: createPostPage({ posts: [], totalPosts: 0, totalPages: 0 }),
        })}
      />,
    );

    expect(markup).toContain("No posts");
    expect(markup).toContain("No posts have been collected yet.");
    expect(markup).toContain("Page 1 of 1");
  });

  it("renders a filtered empty state when filters have no matches", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          filters: { q: "missing" },
          page: createPostPage({ posts: [], totalPosts: 0, totalPages: 0 }),
        })}
      />,
    );

    expect(markup).toContain('aria-label="0 matching posts"');
    expect(markup).toContain("No posts match the current filters.");
  });

  it("renders a safe error state when the database cannot be configured", () => {
    const result = loadLatestPosts({ env: {} });
    const markup = renderToStaticMarkup(<LatestPostsView result={result} />);

    expect(result.status).toBe("error");
    expect(markup).toContain("Posts are unavailable");
    expect(markup).toContain("The database could not be opened.");
    expect(markup).not.toContain("FETCHLINKS_DB is required");
  });
});

function createReadyResult({
  filters = {},
  page = createPostPage(),
}: {
  filters?: {
    source?: string;
    sourceType?: "rss" | "reddit" | "bluesky" | "mastodon";
    author?: string;
    q?: string;
  };
  page?: PostPage;
} = {}) {
  return {
    status: "ready" as const,
    page,
    filters,
  };
}

function createPostPage(overrides: Partial<PostPage> = {}): PostPage {
  return {
    posts: [
      {
        id: 2,
        source: "https://www.reddit.com/r/test",
        sourceType: "reddit",
        author: "Grace",
        description: "Newest post",
        directLink: "https://example.com/source-post",
        dateCreated: "2026-04-28T10:00:00Z",
        uniqueId: "reddit-2",
        urls: [
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
        ],
      },
    ],
    page: 1,
    pageSize: 50,
    totalPosts: 51,
    totalPages: 2,
    hasPreviousPage: false,
    hasNextPage: true,
    ...overrides,
  };
}

function createRssPost(): PostPage["posts"][number] {
  return {
    id: 1,
    source: "https://example.com/blog",
    sourceType: "rss",
    author: "Ada",
    description: "First RSS post",
    directLink: "https://example.com/post-1",
    dateCreated: "2026-04-27T10:00:00Z",
    uniqueId: "rss-1",
    urls: [
      {
        id: 1,
        postId: 1,
        position: 0,
        originalUrl: "https://example.com/a",
        urlHash: "hash-a",
        unshortenedUrl: null,
        href: "https://example.com/a",
      },
    ],
  };
}

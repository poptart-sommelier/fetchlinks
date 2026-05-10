import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DomainSummary, PostPage, SourceSummary } from "../models/read-models";
import { LatestPostsView, loadLatestPosts } from "./page";

describe("Home", () => {
  it("renders posts with metadata, extracted URLs, and pagination links", () => {
    const markup = renderToStaticMarkup(<LatestPostsView result={createReadyResult()} />);

    expect(markup).toContain("Latest posts");
    expect(markup).toContain("Newest post");
    expect(markup).toContain("Grace");
    expect(markup).toContain('href="https://www.reddit.com/r/test"');
    expect(markup).toContain("Apr 28, 2026, 10:00 AM");
    expect(markup).toContain('aria-label="Post links"');
    expect(markup).toContain('class="post-url-actions"');
    expect(markup).toContain("link 1");
    expect(markup).toContain("link 2");
    expect(markup).toContain('class="post-source-action"');
    expect(markup).toContain("source");
    expect(markup).toContain('href="https://example.com/unshortened-b"');
    expect(markup).toContain('title="via short.example/b"');
    expect(markup).not.toContain("Source post");
    expect(markup).toContain('href="/?page=2"');
  });

  it("renders filters and preserves them in pagination links", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          filters: { source: "reddit", domain: "example.com", q: "AI" },
          page: createPostPage({ totalPosts: 75 }),
        })}
      />,
    );

    expect(markup).toContain('aria-label="75 matching posts"');
    expect(markup).toContain('name="q"');
    expect(markup).toContain('aria-label="Filter posts"');
    expect(markup).toContain('value="AI"');
    expect(markup).toContain("reddit (1)");
    expect(markup).toContain("example.com (1)");
    expect(markup).toContain('href="/"');
    expect(markup).toContain(
      'href="/?source=reddit&amp;domain=example.com&amp;q=AI&amp;page=2"',
    );
  });

  it("renders a single extracted URL as link 1", () => {
    const page = createPostPage();
    const [post] = page.posts;
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          page: createPostPage({
            posts: [{ ...post, urls: [post.urls[0]], directLink: null }],
          }),
        })}
      />,
    );

    expect(markup).toContain(">link 1</a>");
    expect(markup).not.toContain(">link</a>");
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
  domains = createDomainSummaries(),
  filters = {},
  page = createPostPage(),
  sources = createSourceSummaries(),
}: {
  domains?: DomainSummary[];
  filters?: { source?: string; domain?: string; q?: string };
  page?: PostPage;
  sources?: SourceSummary[];
} = {}) {
  return {
    status: "ready" as const,
    page,
    sources,
    domains,
    filters,
  };
}

function createPostPage(overrides: Partial<PostPage> = {}): PostPage {
  return {
    posts: [
      {
        id: 2,
        source: "https://www.reddit.com/r/test",
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

function createSourceSummaries(): SourceSummary[] {
  return [
    {
      source: "reddit",
      postCount: 1,
      latestPostDate: "2026-04-28T10:00:00Z",
    },
    {
      source: "rss",
      postCount: 50,
      latestPostDate: "2026-04-27T10:00:00Z",
    },
  ];
}

function createDomainSummaries(): DomainSummary[] {
  return [
    {
      domain: "example.com",
      postCount: 1,
      urlCount: 2,
      latestPostDate: "2026-04-28T10:00:00Z",
    },
    {
      domain: "docs.example.org",
      postCount: 12,
      urlCount: 12,
      latestPostDate: "2026-04-27T10:00:00Z",
    },
  ];
}
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PostPage } from "../models/read-models";
import { ratingTargetsFor } from "../server/rating-targets";
import { LatestPostsView, loadLatestPosts } from "./page";

describe("Home", () => {
  it("links the headline to the post's first URL and dates it relatively", () => {
    const markup = renderToStaticMarkup(<LatestPostsView result={createReadyResult()} />);

    expect(markup).toContain("Latest posts");
    expect(markup).toContain("Grace");
    expect(markup).toContain("reddit/test");
    expect(markup).toContain('href="/?source_type=reddit&amp;author=Grace"');

    // The headline itself is the link, rather than a URL row beneath it.
    expect(markup).toContain(
      '<h2 class="post-title"><a href="https://example.com/direct-b" rel="noreferrer" target="_blank" title="https://example.com/direct-b">Newest post</a></h2>',
    );

    // The absolute timestamp stays reachable as the tooltip.
    expect(markup).toContain('title="Apr 28, 2026, 10:00 AM"');
    expect(markup).toContain("2026-04-28T10:00:00Z");

    // The post links to example.com but came from reddit.com, so the target
    // host earns its place in the metadata line.
    expect(markup).toContain('class="post-target-host">example.com<');

    // The remaining URL is demoted; the one the headline uses is not repeated.
    expect(markup).toContain('aria-label="Other links in this post"');
    expect(markup).toContain('href="https://example.com/unshortened-b"');
    expect(markup).not.toContain('class="post-link-row"><a href="https://example.com/direct-b"');

    expect(markup).toContain('class="post-source-action"');
    expect(markup).toContain('href="https://example.com/source-post"');
    expect(markup).toContain('href="/?page=2"');
  });

  it("omits the target host when the post links back to its own source", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          page: createPostPage({ posts: [createRssPost()] }),
        })}
      />,
    );

    // Feed and article share example.com, so printing the domain again is noise.
    expect(markup).not.toContain("post-target-host");
  });

  it("drops the single link list when the headline already covers it", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          page: createPostPage({ posts: [createRssPost()] }),
        })}
      />,
    );

    expect(markup).toContain(
      '<h2 class="post-title"><a href="https://example.com/a" rel="noreferrer" target="_blank" title="https://example.com/a">First RSS post</a></h2>',
    );
    expect(markup).not.toContain("post-link-list");
    expect(markup).not.toContain("post-link-row");
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

  it("names the publication rather than repeating the feed URL and the type", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({
          page: createPostPage({
            posts: [createRssPost()],
          }),
        })}
      />,
    );

    // "rss · example.com/blog · Ada" collapses to the one part a reader needs,
    // rendered as a name rather than as a lowercase type token.
    expect(markup).toContain('<span class="post-source-mid">Ada</span>');
    expect(markup).not.toContain(">rss<");
    expect(markup).not.toContain("post-source-type");
    expect(markup).toContain('title="Ada — https://example.com/blog"');
    expect(markup).toContain(
      'href="/?source_type=rss&amp;source=https%3A%2F%2Fexample.com%2Fblog"',
    );
  });

  it("heads a filtered view with what is being filtered to", () => {
    const bySource = renderToStaticMarkup(
      <LatestPostsView
        result={createReadyResult({ filters: { source: "https://example.com/blog" } })}
      />,
    );
    const byQuery = renderToStaticMarkup(
      <LatestPostsView result={createReadyResult({ filters: { q: "AI" } })} />,
    );

    expect(bySource).toContain("<h1>example.com/blog</h1>");
    expect(bySource).not.toContain("<h1>Latest posts</h1>");
    expect(byQuery).toContain("Results for");
    expect(byQuery).toContain("AI");
  });

  it("says nothing about owner mode to a visitor who is not the owner", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: false, returnPath: "/?source_type=reddit&page=2" }}
        result={createReadyResult()}
      />,
    );

    // The entrance lives inside Flightdeck. The public page does not advertise
    // it, and nothing that belongs to the owner may render for a visitor.
    expect(markup).not.toContain("/flightdeck/owner");
    expect(markup).not.toContain("Owner mode");
    expect(markup).not.toContain("owner-banner");
    expect(markup).not.toContain("Exit owner mode");
  });

  it("shows the owner banner and a way back out once owner mode is on", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/?q=AI" }}
        result={createReadyResult()}
      />,
    );

    expect(markup).toContain('aria-label="Owner mode"');
    expect(markup).toContain("Exit owner mode");
    expect(markup).toContain('value="/?q=AI"');
    expect(markup).not.toContain("/flightdeck/owner?next=");
  });

  it("treats a view with no owner state as anonymous", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView result={createReadyResult()} />,
    );

    expect(markup).not.toContain("owner-banner");
  });

  it("shows rating controls only in owner mode, with what is already rated", () => {
    const post = createPostPage().posts[0];
    const targets = ratingTargetsFor(post);
    const rated = targets.find((target) => target.type === "actor");
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        ratingsByPostId={
          new Map([
            [
              post.id,
              {
                targets,
                verdicts: new Map([
                  [`${rated?.type}\u001f${rated?.key}`, "noise" as const],
                ]),
                scores: new Map(),
              },
            ],
          ])
        }
        result={createReadyResult()}
      />,
    );

    expect(markup).toContain("post-rating");
    expect(markup).toContain("r/test");
    expect(markup).toContain("example.com");
    // The recorded verdict has to be visible without relying on colour alone.
    expect(markup).toContain('aria-pressed="true" class="rating-noise"');
    // Clearing is only offered where there is something to clear.
    expect(markup).toContain("Clear");
    expect(markup).toContain('value="reddit-2"');
  });

  it("shows no rating controls to an anonymous visitor", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView result={createReadyResult()} />,
    );

    expect(markup).not.toContain("post-rating");
    expect(markup).not.toContain("Noise");
    // Nothing about the owner's judgments may reach a page they did not ask for.
    expect(markup).not.toContain("rating-target");
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

  it("renders a safe error state when the database cannot be configured", async () => {
    const result = await loadLatestPosts({ env: {} });
    const markup = renderToStaticMarkup(<LatestPostsView result={result} />);

    expect(result.status).toBe("error");
    expect(markup).toContain("Posts are unavailable");
    expect(markup).toContain("The database could not be opened.");
    expect(markup).not.toContain("DATABASE_URL is required");
  });

  it("renders the error state rather than surfacing a bad connection string", async () => {
    const result = await loadLatestPosts({ env: { DATABASE_URL: "nonsense" } });
    const markup = renderToStaticMarkup(<LatestPostsView result={result} />);

    expect(result.status).toBe("error");
    expect(markup).not.toContain("nonsense");
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
        occurrences: [
          {
            id: 1,
            postId: 2,
            sourceType: "reddit",
            channelKey: "test",
            channelLabel: "r/test",
            actorKey: "grace",
            actorLabel: "Grace",
            source: "https://www.reddit.com/r/test",
            directLink: "https://example.com/source-post",
          },
        ],
        urls: [
          {
            id: 3,
            postId: 2,
            position: 0,
            originalUrl: "https://example.com/direct-b",
            urlHash: "hash-b0",
            unshortenedUrl: null,
            urlHost: "example.com",
            href: "https://example.com/direct-b",
          },
          {
            id: 2,
            postId: 2,
            position: 1,
            originalUrl: "https://short.example/b",
            urlHash: "hash-b1",
            unshortenedUrl: "https://example.com/unshortened-b",
            urlHost: "example.com",
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
    occurrences: [
      {
        id: 2,
        postId: 1,
        sourceType: "rss",
        channelKey: "https://example.com/blog",
        channelLabel: "Ada",
        actorKey: "",
        actorLabel: "",
        source: "https://example.com/blog",
        directLink: "https://example.com/post-1",
      },
    ],
    urls: [
      {
        id: 1,
        postId: 1,
        position: 0,
        originalUrl: "https://example.com/a",
        urlHash: "hash-a",
        unshortenedUrl: null,
        urlHost: "example.com",
        href: "https://example.com/a",
      },
    ],
  };
}

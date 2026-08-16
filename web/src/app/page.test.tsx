import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PostPage } from "../models/read-models";
import type { CatalogSource } from "../server/catalog-sources";
import { lookupKey } from "../server/curation";
import { curationTargetsFor } from "../server/curation-targets";
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

  it("shows Manage only in owner mode, with cumulative feedback", () => {
    const post = createPostPage().posts[0];
    const targets = curationTargetsFor(post);
    const postTarget = targets.find((target) => target.type === "post");
    const marked = targets.find((target) => target.type === "actor");
    if (!postTarget) throw new Error("expected a post target");
    if (!marked) throw new Error("expected an actor target");
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map([
            [
              post.id,
              {
                mutes: new Set([
                  lookupKey(postTarget.type, postTarget.key),
                  lookupKey(marked.type, marked.key),
                ]),
                targets,
                thumbsDowns: new Map([
                  [
                    lookupKey(postTarget.type, postTarget.key),
                    {
                      count: 11,
                      activePostUniqueIds: [post.uniqueId],
                    },
                  ],
                  [
                    lookupKey(marked.type, marked.key),
                    {
                      count: 7,
                      activePostUniqueIds: [post.uniqueId],
                    },
                  ],
                ]),
              },
            ],
          ])
        }
        result={createReadyResult()}
      />,
    );

    expect(markup).toContain("post-manage");
    expect(markup).toContain("Manage");
    expect(markup).toContain("r/test");
    expect(markup).toContain("example.com");
    expect(markup).not.toContain('class="manage-target-type">this post</span>');
    expect(markup).toContain('title="Distinct articles marked down"');
    expect(markup).toContain(">👎</span> 7");
    expect(markup).not.toContain(">👎</span> 11");
    expect(markup).toContain(
      '<span class="post-manage-count">1 marked</span>',
    );
    expect(markup).toContain(
      '<span class="post-manage-count">1 muted</span>',
    );
    expect(markup).not.toContain(
      '<span class="post-manage-count">2 marked</span>',
    );
    expect(markup).not.toContain(
      '<span class="post-manage-count">2 muted</span>',
    );
    expect(markup).toContain(
      'aria-label="Remove thumbs down" aria-pressed="true" class="manage-icon-control manage-thumb" data-tooltip="Remove thumbs down"',
    );
    expect(markup).toContain('data-icon="thumbs-down"');
    expect(markup).toContain('aria-label="Thumbs down" aria-pressed="false"');
    expect(markup).toContain("Hidden from public.");
    expect(markup).toContain("account — Grace");
    expect(markup).toContain(
      'aria-label="Unmute" aria-pressed="true" class="manage-icon-control manage-mute" data-tooltip="Unmute"',
    );
    expect(markup).toContain('data-icon="mute"');
    expect(markup).not.toContain(">Unmute</button>");
    expect(markup).not.toContain(">Mute</button>");
    expect(markup).not.toContain(">Thumbs down</button>");
    expect(markup).not.toContain(">Remove thumbs down</button>");
    expect(markup).toContain('value="reddit-2"');
  });

  it("keeps long target labels and their actions in separate row columns", () => {
    const longDomain = `${"very-long-subdomain-".repeat(8)}example.com`;
    const post = createPostPage().posts[0]!;
    const withLongDomain = {
      ...post,
      urls: post.urls.map((url) => ({
        ...url,
        urlHost: longDomain,
      })),
    };
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map([
            [
              post.id,
              {
                mutes: new Set<string>(),
                targets: curationTargetsFor(withLongDomain),
                thumbsDowns: new Map(),
              },
            ],
          ])
        }
        result={createReadyResult({
          page: createPostPage({ posts: [withLongDomain] }),
        })}
      />,
    );
    const domainRow = markup.match(
      new RegExp(
        `<li class="manage-target"><div class="manage-target-name"><span class="manage-target-type">domain</span><span class="manage-target-label">${longDomain}</span>.*?</div><div class="manage-actions">(.*?)</div></li>`,
      ),
    );

    expect(domainRow).not.toBeNull();
    expect(domainRow?.[1]).toContain('data-icon="thumbs-down"');
    expect(domainRow?.[1]).toContain('data-icon="mute"');
  });

  it("keeps legacy post-mute recovery out of the ordinary Manage rows", () => {
    const post = createPostPage().posts[0]!;
    const targets = curationTargetsFor(post);
    const postTarget = targets.find((target) => target.type === "post");
    if (!postTarget) throw new Error("expected a post target");
    const managementByPostId = new Map([
      [
        post.id,
        {
          mutes: new Set([lookupKey(postTarget.type, postTarget.key)]),
          targets,
          thumbsDowns: new Map([
            [
              lookupKey(postTarget.type, postTarget.key),
              {
                count: 4,
                activePostUniqueIds: [post.uniqueId],
              },
            ],
          ]),
        },
      ],
    ]);
    const result = createReadyResult();
    const ownerMarkup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={managementByPostId}
        result={result}
      />,
    );
    const publicMarkup = renderToStaticMarkup(
      <LatestPostsView
        managementByPostId={managementByPostId}
        result={result}
      />,
    );

    expect(ownerMarkup).toContain("Hidden from public.");
    expect(ownerMarkup).toContain("this post — Newest post");
    expect(ownerMarkup).toContain(
      'aria-label="Unmute" aria-pressed="true" class="manage-icon-control manage-mute" data-tooltip="Unmute"',
    );
    expect(
      ownerMarkup.match(
        /<input(?=[^>]*name="target_type")(?=[^>]*value="post")[^>]*>/g,
      ),
    ).toHaveLength(1);
    expect(ownerMarkup).not.toContain(
      '<span class="post-manage-count">1 marked</span>',
    );
    expect(ownerMarkup).not.toContain(
      '<span class="post-manage-count">1 muted</span>',
    );
    expect(ownerMarkup).not.toContain(
      'class="manage-target-type">this post</span>',
    );
    expect(publicMarkup).not.toContain("Hidden from public.");
    expect(publicMarkup).not.toContain("this post — Newest post");
    expect(publicMarkup).not.toContain('value="post"');
    expect(publicMarkup).not.toContain("Unmute");
  });

  it("gives every card an anchor so Manage can come back to it", () => {
    const post = createPostPage().posts[0];
    const markup = renderToStaticMarkup(
      <LatestPostsView result={createReadyResult()} />,
    );

    expect(markup).toContain(`id="post-${post.id}"`);
  });

  it("reopens Manage on the card just changed, and only that one", () => {
    const page = createPostPage();
    const [first, second] = page.posts;
    const managementByPostId = new Map(
      page.posts.map((post) => [
        post.id,
        {
          mutes: new Set<string>(),
          targets: curationTargetsFor(post),
          thumbsDowns: new Map(),
        },
      ]),
    );
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/", managedPostId: first.id }}
        managementByPostId={managementByPostId}
        result={createReadyResult({ page })}
      />,
    );

    expect(markup).toContain('<details class="post-manage" open="">');
    // A second card must not be dragged open with it.
    if (second) {
      expect(markup).toContain('<details class="post-manage">');
    }
  });

  it("leaves every panel shut when no Manage action just happened", () => {
    const page = createPostPage();
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map(
            page.posts.map((post) => [
              post.id,
              {
                mutes: new Set<string>(),
                targets: curationTargetsFor(post),
                thumbsDowns: new Map(),
              },
            ]),
          )
        }
        result={createReadyResult({ page })}
      />,
    );

    expect(markup).not.toContain("open=");
  });

  it("shows no management controls to an anonymous visitor", () => {
    const markup = renderToStaticMarkup(
      <LatestPostsView result={createReadyResult()} />,
    );

    expect(markup).not.toContain("post-manage");
    expect(markup).not.toContain("Thumbs down");
    // Nothing about the owner's judgments may reach a page they did not ask for.
    expect(markup).not.toContain("manage-target");
    expect(markup).not.toContain("Hidden from public");
    expect(markup).not.toContain("Unmute");
    expect(markup).not.toContain("Remove from collection");
    expect(markup).not.toContain("removed from collection");
    expect(markup).not.toContain("Restore");
  });

  it("offers confirmed removal only for an active real channel source", () => {
    const post = createRssPost();
    const targets = curationTargetsFor(post);
    const channel = targets.find((target) => target.type === "channel");
    if (!channel) throw new Error("expected an RSS channel");
    const catalogSources = new Map<string, CatalogSource>([
      [
        lookupKey(channel.type, channel.key),
        {
          id: 12,
          kind: "rss",
          status: "active",
          targetKey: channel.key,
        },
      ],
    ]);
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map([
            [
              post.id,
              {
                catalogSources,
                mutes: new Set(),
                targets,
                thumbsDowns: new Map(),
              },
            ],
          ])
        }
        result={createReadyResult({
          page: createPostPage({ posts: [post] }),
        })}
      />,
    );

    expect(markup).toContain(
      '<details class="manage-remove-confirm"><summary aria-label="Remove this feed from collection" class="manage-icon-control" data-tooltip="Remove this feed from collection">',
    );
    expect(markup).toContain('data-icon="trash"');
    expect(markup).toContain("Remove from collection</button>");
    expect(markup).not.toContain(">Remove from collection</summary>");
  });

  it("names a subreddit in its collection removal control", () => {
    const post = createPostPage().posts[0]!;
    const targets = curationTargetsFor(post);
    const channel = targets.find((target) => target.type === "channel");
    if (!channel) throw new Error("expected a subreddit channel");
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map([
            [
              post.id,
              {
                catalogSources: new Map([
                  [
                    lookupKey(channel.type, channel.key),
                    {
                      id: 13,
                      kind: "subreddit" as const,
                      status: "active" as const,
                      targetKey: channel.key,
                    },
                  ],
                ]),
                mutes: new Set<string>(),
                targets,
                thumbsDowns: new Map(),
              },
            ],
          ])
        }
        result={createReadyResult()}
      />,
    );

    expect(markup).toContain(
      'aria-label="Remove this subreddit from collection" class="manage-icon-control" data-tooltip="Remove this subreddit from collection"',
    );
  });

  it("shows a removed-source reason and direct restore without clearing mutes", () => {
    const post = createRssPost();
    const targets = curationTargetsFor(post);
    const channel = targets.find((target) => target.type === "channel");
    if (!channel) throw new Error("expected an RSS channel");
    const identity = lookupKey(channel.type, channel.key);
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map([
            [
              post.id,
              {
                catalogSources: new Map([
                  [
                    identity,
                    {
                      id: 12,
                      kind: "rss" as const,
                      status: "removed" as const,
                      targetKey: channel.key,
                    },
                  ],
                ]),
                mutes: new Set([identity]),
                targets,
                thumbsDowns: new Map(),
              },
            ],
          ])
        }
        result={createReadyResult({
          page: createPostPage({ posts: [post] }),
        })}
      />,
    );

    expect(markup).toContain("Hidden from public.");
    expect(markup).toContain("removed from collection — Ada");
    expect(markup).toContain(
      'aria-label="Restore this feed to collection" class="manage-icon-control" data-tooltip="Restore this feed to collection"',
    );
    expect(markup).toContain('data-icon="restore"');
    expect(markup).toContain(
      'aria-label="Unmute" aria-pressed="true" class="manage-icon-control manage-mute" data-tooltip="Unmute"',
    );
    expect(markup).not.toContain(">Restore</button>");
    expect(markup).not.toContain(">Unmute</button>");
    expect(markup).not.toContain("manage-remove-confirm");
  });

  it.each([
    {
      kind: "RSS",
      post: createRssPost(),
      source: {
        id: 12,
        kind: "rss" as const,
        status: "disabled" as const,
      },
      reason: "disabled in collection — Ada",
    },
    {
      kind: "subreddit",
      post: createPostPage().posts[0]!,
      source: {
        id: 13,
        kind: "subreddit" as const,
        status: "disabled" as const,
      },
      reason: "disabled in collection — r/test",
    },
  ])(
    "explains a disabled $kind origin to the owner without collection controls",
    ({ post, reason, source }) => {
      const targets = curationTargetsFor(post);
      const channel = targets.find((target) => target.type === "channel");
      if (!channel) throw new Error("expected a channel target");
      const catalogSources = new Map<string, CatalogSource>([
        [
          lookupKey(channel.type, channel.key),
          { ...source, targetKey: channel.key },
        ],
      ]);
      const managementByPostId = new Map([
        [
          post.id,
          {
            catalogSources,
            mutes: new Set<string>(),
            targets,
            thumbsDowns: new Map(),
          },
        ],
      ]);
      const result = createReadyResult({
        page: createPostPage({ posts: [post] }),
      });
      const ownerMarkup = renderToStaticMarkup(
        <LatestPostsView
          owner={{ isOwner: true, returnPath: "/" }}
          managementByPostId={managementByPostId}
          result={result}
        />,
      );
      const publicMarkup = renderToStaticMarkup(
        <LatestPostsView
          managementByPostId={managementByPostId}
          result={result}
        />,
      );

      expect(ownerMarkup).toContain("Hidden from public.");
      expect(ownerMarkup).toContain(reason);
      expect(ownerMarkup).not.toContain("manage-remove-confirm");
      expect(ownerMarkup).not.toContain('data-icon="restore"');
      expect(publicMarkup).not.toContain("Hidden from public.");
      expect(publicMarkup).not.toContain("disabled in collection");
      expect(publicMarkup).not.toContain("Remove from collection");
      expect(publicMarkup).not.toContain("Restore");
    },
  );

  it("calls a card partly hidden when another origin remains available", () => {
    const post = createPostPage().posts[0]!;
    const targets = curationTargetsFor(post);
    const redditChannel = targets.find((target) => target.type === "channel");
    if (!redditChannel) throw new Error("expected a Reddit channel");
    const mastodonOccurrence = {
      ...post.occurrences[0]!,
      id: 99,
      sourceType: "mastodon" as const,
      channelKey: "social.example",
      channelLabel: "social.example",
      actorKey: "other",
      actorLabel: "Other",
    };
    const withSurvivor = {
      ...post,
      occurrences: [...post.occurrences, mastodonOccurrence],
    };
    const allTargets = curationTargetsFor(withSurvivor);
    const markup = renderToStaticMarkup(
      <LatestPostsView
        owner={{ isOwner: true, returnPath: "/" }}
        managementByPostId={
          new Map([
            [
              post.id,
              {
                catalogSources: new Map([
                  [
                    lookupKey(redditChannel.type, redditChannel.key),
                    {
                      id: 3,
                      kind: "subreddit" as const,
                      status: "removed" as const,
                      targetKey: redditChannel.key,
                    },
                  ],
                ]),
                mutes: new Set(),
                targets: allTargets,
                thumbsDowns: new Map(),
              },
            ],
          ])
        }
        result={createReadyResult({
          page: createPostPage({ posts: [withSurvivor] }),
        })}
      />,
    );

    expect(markup).toContain("Partly hidden from public.");
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

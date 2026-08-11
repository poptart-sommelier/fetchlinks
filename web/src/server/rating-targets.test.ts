import { describe, expect, it } from "vitest";

import type { PostSummary } from "../models/read-models";
import { ratingTargetsFor } from "./rating-targets";
import { composeTargetKey } from "./ratings";

describe("rating targets", () => {
  it("offers the post, its channel, its account and its linked domain", () => {
    const targets = ratingTargetsFor(createPost());

    expect(targets).toEqual([
      { type: "post", key: "post-1", label: "A collected link" },
      {
        type: "channel",
        key: composeTargetKey("reddit", "netsec"),
        label: "r/netsec",
      },
      {
        type: "actor",
        key: composeTargetKey("reddit", "grace"),
        label: "Grace",
      },
      { type: "domain", key: "example.com", label: "example.com" },
    ]);
  });

  it("offers both origins when one link arrived from two places", () => {
    const targets = ratingTargetsFor(
      createPost({
        occurrences: [
          createOccurrence(),
          createOccurrence({
            id: 2,
            sourceType: "rss",
            channelKey: "https://example.com/feed",
            channelLabel: "Example Weekly",
            actorKey: "",
            actorLabel: "",
          }),
        ],
      }),
    );

    expect(targets.map((target) => target.label)).toContain("r/netsec");
    expect(targets.map((target) => target.label)).toContain("Example Weekly");
  });

  it("skips dimensions a source does not have", () => {
    // An RSS feed has no author, so an actor row would be an empty control.
    const targets = ratingTargetsFor(
      createPost({
        occurrences: [
          createOccurrence({
            sourceType: "rss",
            channelKey: "https://example.com/feed",
            channelLabel: "Example Weekly",
            actorKey: "",
            actorLabel: "",
          }),
        ],
      }),
    );

    expect(targets.some((target) => target.type === "actor")).toBe(false);
  });

  it("offers one control per target when a post repeats a domain", () => {
    const targets = ratingTargetsFor(
      createPost({
        urls: [createUrl(), createUrl({ id: 2, urlHost: "example.com" })],
      }),
    );
    const domains = targets.filter((target) => target.type === "domain");

    expect(domains).toHaveLength(1);
  });

  it("falls back to a key when a label was never captured", () => {
    const targets = ratingTargetsFor(
      createPost({
        occurrences: [createOccurrence({ channelLabel: "", actorLabel: "" })],
      }),
    );

    expect(targets[1]?.label).toBe("netsec");
    expect(targets[2]?.label).toBe("grace");
  });

  it("keeps one source's key from colliding with another's", () => {
    const reddit = composeTargetKey("reddit", "netsec");
    const mastodon = composeTargetKey("mastodon", "netsec");

    expect(reddit).not.toBe(mastodon);
  });
});

function createPost(overrides: Partial<PostSummary> = {}): PostSummary {
  return {
    id: 1,
    source: "https://www.reddit.com/r/netsec",
    sourceType: "reddit",
    author: "Grace",
    description: "A collected link",
    directLink: "https://www.reddit.com/r/netsec/comments/1",
    dateCreated: "2026-08-11T10:00:00Z",
    uniqueId: "post-1",
    urls: [createUrl()],
    occurrences: [createOccurrence()],
    ...overrides,
  };
}

function createUrl(
  overrides: Partial<PostSummary["urls"][number]> = {},
): PostSummary["urls"][number] {
  return {
    id: 1,
    postId: 1,
    position: 0,
    originalUrl: "https://example.com/a",
    urlHash: "hash-a",
    unshortenedUrl: null,
    urlHost: "example.com",
    href: "https://example.com/a",
    ...overrides,
  };
}

function createOccurrence(
  overrides: Partial<PostSummary["occurrences"][number]> = {},
): PostSummary["occurrences"][number] {
  return {
    id: 1,
    postId: 1,
    sourceType: "reddit",
    channelKey: "netsec",
    channelLabel: "r/netsec",
    actorKey: "grace",
    actorLabel: "Grace",
    source: "https://www.reddit.com/r/netsec",
    directLink: "https://www.reddit.com/r/netsec/comments/1",
    ...overrides,
  };
}

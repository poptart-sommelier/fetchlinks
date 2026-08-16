import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PostSummary } from "../models/read-models";

const cookieStore = new Map<string, string>();
const getPosts = vi.fn();
const setMute = vi.fn();
const clearMute = vi.fn();
const setThumbsDown = vi.fn();
const clearThumbsDown = vi.fn();
const resolveCatalogSource = vi.fn();
const softDeleteRssFeed = vi.fn();
const restoreRssFeed = vi.fn();
const softDeleteSubreddit = vi.fn();
const restoreSubreddit = vi.fn();
const redirect = vi.fn();

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = cookieStore.get(name);
      return value === undefined ? undefined : { name, value };
    },
    delete: (name: string) => cookieStore.delete(name),
  }),
}));

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => redirect(path),
}));
vi.mock("../server/sql", () => ({ getSqlClient: () => ({}) }));
vi.mock("../server/db", () => ({
  getPosts: (...args: unknown[]) => getPosts(...args),
}));
vi.mock("../server/catalog-sources", () => ({
  resolveCatalogSource: (...args: unknown[]) => resolveCatalogSource(...args),
}));
vi.mock("../server/feeds", () => ({
  softDeleteRssFeed: (...args: unknown[]) => softDeleteRssFeed(...args),
  restoreRssFeed: (...args: unknown[]) => restoreRssFeed(...args),
}));
vi.mock("../server/subreddits", () => ({
  softDeleteSubreddit: (...args: unknown[]) => softDeleteSubreddit(...args),
  restoreSubreddit: (...args: unknown[]) => restoreSubreddit(...args),
}));
vi.mock("../server/curation", async () => {
  const actual =
    await vi.importActual<typeof import("../server/curation")>(
      "../server/curation",
    );
  return {
    ...actual,
    setMute: (...args: unknown[]) => setMute(...args),
    clearMute: (...args: unknown[]) => clearMute(...args),
    setThumbsDown: (...args: unknown[]) => setThumbsDown(...args),
    clearThumbsDown: (...args: unknown[]) => clearThumbsDown(...args),
  };
});

const { muteAction, sourceCollectionAction, thumbsDownAction } =
  await import("./owner-actions");
const { OWNER_COOKIE_NAME, createOwnerToken } = await import("../server/owner");

const ENV = {
  FETCHLINKS_ADMIN_USER: "owner",
  FETCHLINKS_ADMIN_PASS: "a-long-random-password",
};

const POST: PostSummary = {
  id: 1,
  source: "https://www.reddit.com/r/test",
  sourceType: "reddit",
  author: "Grace",
  description: "A post",
  directLink: "https://example.com/direct",
  dateCreated: "2026-04-28T10:00:00Z",
  uniqueId: "reddit-2",
  urls: [
    {
      id: 1,
      postId: 1,
      position: 0,
      originalUrl: "https://example.com/one",
      urlHash: "h1",
      unshortenedUrl: null,
      href: "https://example.com/one",
      urlHost: "example.com",
    },
  ],
  occurrences: [],
};

const RSS_POST: PostSummary = {
  ...POST,
  source: "https://feed.example/rss",
  sourceType: "rss",
  uniqueId: "rss-1",
  occurrences: [
    {
      id: 2,
      postId: 1,
      sourceType: "rss",
      channelKey: "https://feed.example/rss",
      channelLabel: "Feed Example",
      actorKey: "",
      actorLabel: "",
      source: "https://feed.example/rss",
      directLink: "https://feed.example/post",
    },
  ],
};

const REDDIT_POST: PostSummary = {
  ...POST,
  occurrences: [
    {
      id: 3,
      postId: 1,
      sourceType: "reddit",
      channelKey: "test",
      channelLabel: "r/test",
      actorKey: "grace",
      actorLabel: "Grace",
      source: "https://www.reddit.com/r/test",
      directLink: "https://reddit.example/post",
    },
  ],
};

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    data.set(key, value);
  }
  return data;
}

const VALID_TARGET = {
  post_unique_id: "reddit-2",
  target_type: "domain",
  target_key: "example.com",
  intent: "set",
  next: "/",
};

describe("owner Manage actions", () => {
  beforeEach(async () => {
    cookieStore.clear();
    vi.clearAllMocks();
    Object.assign(process.env, ENV);
    getPosts.mockResolvedValue({ posts: [POST] });
    softDeleteRssFeed.mockResolvedValue(true);
    restoreRssFeed.mockResolvedValue(true);
    softDeleteSubreddit.mockResolvedValue(true);
    restoreSubreddit.mockResolvedValue(true);
  });

  async function signIn(): Promise<void> {
    const token = await createOwnerToken(process.env);

    if (!token) {
      throw new Error("Owner credentials are not configured for this test.");
    }

    cookieStore.set(OWNER_COOKIE_NAME, token);
  }

  it("refuses a caller with no owner cookie", async () => {
    await expect(thumbsDownAction(form(VALID_TARGET))).rejects.toThrow(
      /owner mode/i,
    );
    expect(setThumbsDown).not.toHaveBeenCalled();
  });

  it("refuses a forged owner cookie", async () => {
    cookieStore.set(OWNER_COOKIE_NAME, "9999999999.deadbeef");

    await expect(thumbsDownAction(form(VALID_TARGET))).rejects.toThrow();
    expect(setThumbsDown).not.toHaveBeenCalled();
  });

  it("records feedback for a target the article really has", async () => {
    await signIn();
    await thumbsDownAction(form(VALID_TARGET));

    expect(setThumbsDown).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        postUniqueId: "reddit-2",
        target: expect.objectContaining({
          type: "domain",
          key: "example.com",
        }),
      }),
    );
    expect(redirect).toHaveBeenCalledWith("/?managed=1#post-1");
  });

  it("refuses a target the article does not have", async () => {
    await signIn();

    await expect(
      thumbsDownAction(
        form({ ...VALID_TARGET, target_key: "evil.example" }),
      ),
    ).rejects.toThrow(/belong/i);
    expect(setThumbsDown).not.toHaveBeenCalled();
  });

  it("ignores a submitted label and derives the stored one", async () => {
    await signIn();
    await thumbsDownAction(
      form({ ...VALID_TARGET, target_label: "<script>alert(1)</script>" }),
    );

    const [, call] = setThumbsDown.mock.calls[0] as [
      unknown,
      { target: { label?: string } },
    ];
    expect(call.target.label).toBe("example.com");
  });

  it("refuses an action it does not recognise", async () => {
    await signIn();

    await expect(
      thumbsDownAction(form({ ...VALID_TARGET, intent: "erase-everything" })),
    ).rejects.toThrow(/action/i);
  });

  it("clears only this article's feedback when asked", async () => {
    await signIn();
    await thumbsDownAction(form({ ...VALID_TARGET, intent: "clear" }));

    expect(clearThumbsDown).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ postUniqueId: "reddit-2" }),
    );
    expect(setThumbsDown).not.toHaveBeenCalled();
  });

  it("refuses feedback for an article that no longer exists", async () => {
    await signIn();
    getPosts.mockResolvedValue({ posts: [] });

    await expect(thumbsDownAction(form(VALID_TARGET))).rejects.toThrow(
      /exists/i,
    );
  });

  it("returns only to a safe local path", async () => {
    await signIn();
    await thumbsDownAction(
      form({ ...VALID_TARGET, next: "https://evil.example/" }),
    );

    expect(redirect).toHaveBeenCalledWith("/?managed=1#post-1");
  });

  it("returns to the card with filters and pagination intact", async () => {
    await signIn();
    await thumbsDownAction(
      form({ ...VALID_TARGET, next: "/?q=AI&page=3" }),
    );

    expect(redirect).toHaveBeenCalledWith(
      "/?q=AI&page=3&managed=1#post-1",
    );
  });

  it("replaces an old marker instead of stacking them", async () => {
    await signIn();
    await thumbsDownAction(
      form({ ...VALID_TARGET, next: "/?managed=9#post-9" }),
    );

    expect(redirect).toHaveBeenCalledWith("/?managed=1#post-1");
  });

  it("returns to the card after clearing too", async () => {
    await signIn();
    await thumbsDownAction(form({ ...VALID_TARGET, intent: "clear" }));

    expect(redirect).toHaveBeenCalledWith("/?managed=1#post-1");
  });

  it("refuses to mute without owner mode", async () => {
    await expect(
      muteAction(form({ ...VALID_TARGET, intent: "mute" })),
    ).rejects.toThrow(/owner mode/i);
    expect(setMute).not.toHaveBeenCalled();
  });

  it("mutes a real target and keeps the hidden article resolvable", async () => {
    await signIn();
    await muteAction(form({ ...VALID_TARGET, intent: "mute" }));

    expect(getPosts).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ includeMuted: true, uniqueId: "reddit-2" }),
    );
    expect(setMute).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        type: "domain",
        key: "example.com",
        label: "example.com",
      }),
    );
    expect(redirect).toHaveBeenCalledWith("/?managed=1#post-1");
  });

  it("unmutes only the target requested", async () => {
    await signIn();
    await muteAction(form({ ...VALID_TARGET, intent: "unmute" }));

    expect(clearMute).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ type: "domain", key: "example.com" }),
    );
    expect(setMute).not.toHaveBeenCalled();
  });

  it("refuses to mute a forged target or unknown intent", async () => {
    await signIn();

    await expect(
      muteAction(
        form({
          ...VALID_TARGET,
          intent: "mute",
          target_key: "forged.example",
        }),
      ),
    ).rejects.toThrow(/belong/i);
    await expect(
      muteAction(form({ ...VALID_TARGET, intent: "toggle" })),
    ).rejects.toThrow(/action/i);
    expect(setMute).not.toHaveBeenCalled();
    expect(clearMute).not.toHaveBeenCalled();
  });

  it("removes a real RSS feed after reconstructing its target", async () => {
    await signIn();
    getPosts.mockResolvedValue({ posts: [RSS_POST] });
    resolveCatalogSource.mockResolvedValue({
      id: 41,
      kind: "rss",
      status: "active",
      targetKey: "rss\u001fhttps://feed.example/rss",
    });

    await sourceCollectionAction(
      form({
        post_unique_id: "rss-1",
        target_type: "channel",
        target_key: "rss\u001fhttps://feed.example/rss",
        intent: "remove",
        next: "/?page=2",
      }),
    );

    expect(getPosts).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ includeMuted: true, uniqueId: "rss-1" }),
    );
    expect(resolveCatalogSource).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        type: "channel",
        key: "rss\u001fhttps://feed.example/rss",
        label: "Feed Example",
      }),
    );
    expect(softDeleteRssFeed).toHaveBeenCalledWith(expect.anything(), 41);
    expect(redirect).toHaveBeenCalledWith("/?page=2&managed=1#post-1");
  });

  it("removes and restores a real subreddit", async () => {
    await signIn();
    getPosts.mockResolvedValue({ posts: [REDDIT_POST] });
    resolveCatalogSource
      .mockResolvedValueOnce({
        id: 7,
        kind: "subreddit",
        status: "active",
        targetKey: "reddit\u001ftest",
      })
      .mockResolvedValueOnce({
        id: 7,
        kind: "subreddit",
        status: "removed",
        targetKey: "reddit\u001ftest",
      });
    const target = {
      post_unique_id: "reddit-2",
      target_type: "channel",
      target_key: "reddit\u001ftest",
      next: "/",
    };

    await sourceCollectionAction(form({ ...target, intent: "remove" }));
    await sourceCollectionAction(form({ ...target, intent: "restore" }));

    expect(softDeleteSubreddit).toHaveBeenCalledWith(expect.anything(), 7);
    expect(restoreSubreddit).toHaveBeenCalledWith(expect.anything(), 7);
    expect(clearMute).not.toHaveBeenCalled();
  });

  it("refuses source changes without owner mode or with a forged target", async () => {
    getPosts.mockResolvedValue({ posts: [REDDIT_POST] });
    const target = {
      post_unique_id: "reddit-2",
      target_type: "channel",
      target_key: "reddit\u001ftest",
      intent: "remove",
      next: "/",
    };

    await expect(sourceCollectionAction(form(target))).rejects.toThrow(
      /owner mode/i,
    );
    await signIn();
    await expect(
      sourceCollectionAction(
        form({ ...target, target_key: "reddit\u001fforged" }),
      ),
    ).rejects.toThrow(/belong/i);
    expect(resolveCatalogSource).not.toHaveBeenCalled();
    expect(softDeleteSubreddit).not.toHaveBeenCalled();
  });

  it("enforces active removal and removed restoration states", async () => {
    await signIn();
    getPosts.mockResolvedValue({ posts: [REDDIT_POST] });
    resolveCatalogSource.mockResolvedValue({
      id: 7,
      kind: "subreddit",
      status: "disabled",
      targetKey: "reddit\u001ftest",
    });
    const target = {
      post_unique_id: "reddit-2",
      target_type: "channel",
      target_key: "reddit\u001ftest",
      next: "/",
    };

    await expect(
      sourceCollectionAction(form({ ...target, intent: "remove" })),
    ).rejects.toThrow(/active/i);
    await expect(
      sourceCollectionAction(form({ ...target, intent: "restore" })),
    ).rejects.toThrow(/removed/i);
    expect(softDeleteSubreddit).not.toHaveBeenCalled();
    expect(restoreSubreddit).not.toHaveBeenCalled();
  });
});

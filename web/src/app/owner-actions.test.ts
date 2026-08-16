import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PostSummary } from "../models/read-models";

const cookieStore = new Map<string, string>();
const getPosts = vi.fn();
const setThumbsDown = vi.fn();
const clearThumbsDown = vi.fn();
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
vi.mock("../server/curation", async () => {
  const actual =
    await vi.importActual<typeof import("../server/curation")>(
      "../server/curation",
    );
  return {
    ...actual,
    setThumbsDown: (...args: unknown[]) => setThumbsDown(...args),
    clearThumbsDown: (...args: unknown[]) => clearThumbsDown(...args),
  };
});

const { thumbsDownAction } = await import("./owner-actions");
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

describe("thumbsDownAction", () => {
  beforeEach(async () => {
    cookieStore.clear();
    vi.clearAllMocks();
    Object.assign(process.env, ENV);
    getPosts.mockResolvedValue({ posts: [POST] });
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
});

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PostSummary } from "../models/read-models";

const cookieStore = new Map<string, string>();
const getPosts = vi.fn();
const rateTarget = vi.fn();
const clearRating = vi.fn();
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
vi.mock("../server/db", () => ({ getPosts: (...args: unknown[]) => getPosts(...args) }));
vi.mock("../server/ratings", async () => {
  const actual =
    await vi.importActual<typeof import("../server/ratings")>(
      "../server/ratings",
    );
  return {
    ...actual,
    rateTarget: (...args: unknown[]) => rateTarget(...args),
    clearRating: (...args: unknown[]) => clearRating(...args),
  };
});

const { rateAction } = await import("./owner-actions");
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
  verdict: "good",
  next: "/",
};

describe("rateAction", () => {
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
    await expect(rateAction(form(VALID_TARGET))).rejects.toThrow(
      /owner mode/i,
    );
    expect(rateTarget).not.toHaveBeenCalled();
  });

  it("refuses a forged owner cookie", async () => {
    cookieStore.set(OWNER_COOKIE_NAME, "9999999999.deadbeef");

    await expect(rateAction(form(VALID_TARGET))).rejects.toThrow();
    expect(rateTarget).not.toHaveBeenCalled();
  });

  it("records a verdict for a target the post really has", async () => {
    await signIn();
    await rateAction(form(VALID_TARGET));

    expect(rateTarget).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        verdict: "good",
        postUniqueId: "reddit-2",
        target: expect.objectContaining({
          type: "domain",
          key: "example.com",
        }),
      }),
    );
    expect(redirect).toHaveBeenCalledWith("/?rated=1#post-1");
  });

  it("refuses a target the post does not have", async () => {
    await signIn();

    await expect(
      rateAction(form({ ...VALID_TARGET, target_key: "evil.example" })),
    ).rejects.toThrow(/rated by/i);
    expect(rateTarget).not.toHaveBeenCalled();
  });

  it("ignores a submitted label and uses the one derived from the post", async () => {
    await signIn();
    await rateAction(
      form({ ...VALID_TARGET, target_label: "<script>alert(1)</script>" }),
    );

    const [, call] = rateTarget.mock.calls[0] as [
      unknown,
      { target: { label?: string } },
    ];

    expect(call.target.label).toBe("example.com");
  });

  it("refuses a verdict it does not recognise", async () => {
    await signIn();

    await expect(
      rateAction(form({ ...VALID_TARGET, verdict: "excellent" })),
    ).rejects.toThrow(/verdict/i);
  });

  it("clears a rating when asked to", async () => {
    await signIn();
    await rateAction(form({ ...VALID_TARGET, verdict: "clear" }));

    expect(clearRating).toHaveBeenCalled();
    expect(rateTarget).not.toHaveBeenCalled();
  });

  it("refuses a rating for a post that no longer exists", async () => {
    await signIn();
    getPosts.mockResolvedValue({ posts: [] });

    await expect(rateAction(form(VALID_TARGET))).rejects.toThrow(/exists/i);
  });

  it("sends the visitor to a safe path, never to another site", async () => {
    await signIn();
    await rateAction(form({ ...VALID_TARGET, next: "https://evil.example/" }));

    expect(redirect).toHaveBeenCalledWith("/?rated=1#post-1");
  });

  it("returns to the rated card, keeping the filters and page it came from", async () => {
    await signIn();
    await rateAction(form({ ...VALID_TARGET, next: "/?q=AI&page=3" }));

    expect(redirect).toHaveBeenCalledWith("/?q=AI&page=3&rated=1#post-1");
  });

  it("does not stack up a rated marker over repeated ratings", async () => {
    await signIn();
    await rateAction(form({ ...VALID_TARGET, next: "/?rated=9#post-9" }));

    expect(redirect).toHaveBeenCalledWith("/?rated=1#post-1");
  });

  it("returns to the card after clearing, not just after rating", async () => {
    await signIn();
    await rateAction(form({ ...VALID_TARGET, verdict: "clear" }));

    expect(redirect).toHaveBeenCalledWith("/?rated=1#post-1");
  });
});

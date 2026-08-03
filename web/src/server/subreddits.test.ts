import { describe, expect, it } from "vitest";

import {
  addSubreddit,
  cleanSubredditName,
  countSubredditsByStatus,
  listSubreddits,
  normalizeSubredditName,
  restoreSubreddit,
  softDeleteSubreddit,
} from "./subreddits";
import { describePostgres, usePostgres } from "./test-support/postgres";

describe("cleanSubredditName", () => {
  it("strips an r/ or /r/ prefix", () => {
    expect(cleanSubredditName("r/programming")).toBe("programming");
    expect(cleanSubredditName("/r/programming")).toBe("programming");
    expect(cleanSubredditName("R/Programming")).toBe("Programming");
  });

  it("strips surrounding whitespace and slashes", () => {
    expect(cleanSubredditName("  /programming/  ")).toBe("programming");
  });

  it("preserves case so the display name matches Reddit", () => {
    expect(cleanSubredditName("r/AskHistorians")).toBe("AskHistorians");
  });
});

describe("normalizeSubredditName", () => {
  it("lowercases the cleaned name", () => {
    expect(normalizeSubredditName("r/AskHistorians")).toBe("askhistorians");
  });
});

describePostgres("subreddit catalog", () => {
  const pg = usePostgres();

  it("adds a subreddit and reports it as active", async () => {
    const result = await addSubreddit(pg.sql, "r/Programming");

    expect(result.status).toBe("added");
    if (result.status !== "added") return;
    expect(result.subreddit).toMatchObject({
      name: "Programming",
      normalizedName: "programming",
      enabled: true,
      deletedAt: null,
      status: "active",
    });
  });

  it("reports an existing subreddit rather than inserting a duplicate", async () => {
    await addSubreddit(pg.sql, "programming");
    const again = await addSubreddit(pg.sql, "r/PROGRAMMING");

    expect(again.status).toBe("exists");
    await expect(countSubredditsByStatus(pg.sql)).resolves.toMatchObject({
      total: 1,
    });
  });

  it("rejects a name Reddit would not accept", async () => {
    await expect(addSubreddit(pg.sql, "")).resolves.toMatchObject({
      status: "invalid",
      reason: "Subreddit name is required.",
    });
    await expect(addSubreddit(pg.sql, "a")).resolves.toMatchObject({
      status: "invalid",
    });
    await expect(addSubreddit(pg.sql, "has spaces")).resolves.toMatchObject({
      status: "invalid",
    });
    await expect(addSubreddit(pg.sql, "a".repeat(22))).resolves.toMatchObject({
      status: "invalid",
    });
    await expect(countSubredditsByStatus(pg.sql)).resolves.toMatchObject({
      total: 0,
    });
  });

  it("records the supplied timestamp as UTC", async () => {
    const result = await addSubreddit(
      pg.sql,
      "programming",
      new Date("2026-05-06T07:08:09Z"),
    );

    expect(result.status).toBe("added");
    if (result.status !== "added") return;
    expect(result.subreddit.addedAt).toBe("2026-05-06T07:08:09Z");
  });

  it("reports the last fetch from the published checkpoint", async () => {
    await addSubreddit(pg.sql, "programming");
    await pg.exec(
      `INSERT INTO content.reddit_state (subreddit, last_seen_fullname, observed_at)
       VALUES ($1, $2, $3)`,
      ["programming", "t3_abc", "2026-02-03T04:05:06Z"],
    );

    const [subreddit] = await listSubreddits(pg.sql);

    expect(subreddit?.lastFetchedAt).toBe("2026-02-03T04:05:06Z");
  });

  it("reports the latest matching post and its source url", async () => {
    await addSubreddit(pg.sql, "Programming");
    for (const [uniqueId, postedAt] of [
      ["p1", "2026-01-01T00:00:00Z"],
      ["p2", "2026-03-01T00:00:00Z"],
    ] as const) {
      await pg.exec(
        `INSERT INTO content.posts (unique_id, source, source_type, posted_at)
         VALUES ($1, $2, 'reddit', $3)`,
        [uniqueId, "https://www.reddit.com/r/programming", postedAt],
      );
    }

    const [subreddit] = await listSubreddits(pg.sql);

    expect(subreddit?.latestPostAt).toBe("2026-03-01T00:00:00Z");
    expect(subreddit?.postSource).toBe("https://www.reddit.com/r/programming");
  });

  it("does not attribute another subreddit's posts", async () => {
    await addSubreddit(pg.sql, "programming");
    await pg.exec(
      `INSERT INTO content.posts (unique_id, source, source_type, posted_at)
       VALUES ('other', 'https://www.reddit.com/r/rust', 'reddit', '2026-01-01T00:00:00Z')`,
    );

    const [subreddit] = await listSubreddits(pg.sql);

    expect(subreddit?.latestPostAt).toBeNull();
    expect(subreddit?.postSource).toBeNull();
  });

  it("soft deletes rather than removing the row", async () => {
    const added = await addSubreddit(pg.sql, "programming");
    if (added.status !== "added") throw new Error("expected added");

    await expect(
      softDeleteSubreddit(pg.sql, added.subreddit.id),
    ).resolves.toBe(true);
    await expect(
      softDeleteSubreddit(pg.sql, added.subreddit.id),
    ).resolves.toBe(false);

    const [subreddit] = await listSubreddits(pg.sql);
    expect(subreddit).toMatchObject({ status: "removed", enabled: false });
  });

  it("restores only a removed subreddit", async () => {
    const added = await addSubreddit(pg.sql, "programming");
    if (added.status !== "added") throw new Error("expected added");

    await expect(restoreSubreddit(pg.sql, added.subreddit.id)).resolves.toBe(
      false,
    );
    await softDeleteSubreddit(pg.sql, added.subreddit.id);
    await expect(restoreSubreddit(pg.sql, added.subreddit.id)).resolves.toBe(
      true,
    );

    const [subreddit] = await listSubreddits(pg.sql);
    expect(subreddit).toMatchObject({ status: "active", deletedAt: null });
  });

  it("reports false for an unknown subreddit id", async () => {
    await expect(softDeleteSubreddit(pg.sql, 4242)).resolves.toBe(false);
    await expect(restoreSubreddit(pg.sql, 4242)).resolves.toBe(false);
  });

  it("filters by status", async () => {
    await addSubreddit(pg.sql, "active_sub");
    const removed = await addSubreddit(pg.sql, "removed_sub");
    const disabled = await addSubreddit(pg.sql, "disabled_sub");
    if (removed.status !== "added" || disabled.status !== "added") {
      throw new Error("expected added");
    }
    await softDeleteSubreddit(pg.sql, removed.subreddit.id);
    await pg.exec(
      "UPDATE catalog.subreddits SET enabled = false WHERE subreddit_id = $1",
      [disabled.subreddit.id],
    );

    await expect(
      listSubreddits(pg.sql, { status: "active" }),
    ).resolves.toHaveLength(1);
    await expect(
      listSubreddits(pg.sql, { status: "disabled" }),
    ).resolves.toHaveLength(1);
    await expect(
      listSubreddits(pg.sql, { status: "removed" }),
    ).resolves.toHaveLength(1);
    await expect(listSubreddits(pg.sql)).resolves.toHaveLength(3);
  });

  it("searches names without regard to case", async () => {
    await addSubreddit(pg.sql, "AskHistorians");
    await addSubreddit(pg.sql, "programming");

    const results = await listSubreddits(pg.sql, { q: "HISTOR" });

    expect(results.map((subreddit) => subreddit.name)).toEqual([
      "AskHistorians",
    ]);
  });

  it("treats wildcards in a search as literal characters", async () => {
    await addSubreddit(pg.sql, "a_b");
    await addSubreddit(pg.sql, "axb");

    const results = await listSubreddits(pg.sql, { q: "a_b" });

    expect(results.map((subreddit) => subreddit.name)).toEqual(["a_b"]);
  });

  it("orders active subreddits before removed ones", async () => {
    await addSubreddit(pg.sql, "zzz");
    const removed = await addSubreddit(pg.sql, "aaa");
    if (removed.status !== "added") throw new Error("expected added");
    await softDeleteSubreddit(pg.sql, removed.subreddit.id);

    const subreddits = await listSubreddits(pg.sql);

    expect(subreddits.map((subreddit) => subreddit.status)).toEqual([
      "active",
      "removed",
    ]);
  });

  it("counts each status independently", async () => {
    await addSubreddit(pg.sql, "active_sub");
    const removed = await addSubreddit(pg.sql, "removed_sub");
    if (removed.status !== "added") throw new Error("expected added");
    await softDeleteSubreddit(pg.sql, removed.subreddit.id);

    await expect(countSubredditsByStatus(pg.sql)).resolves.toEqual({
      active: 1,
      disabled: 0,
      removed: 1,
      total: 2,
    });
  });

  it("returns zeroes rather than nulls for an empty catalog", async () => {
    await expect(countSubredditsByStatus(pg.sql)).resolves.toEqual({
      active: 0,
      disabled: 0,
      removed: 0,
      total: 0,
    });
  });
});

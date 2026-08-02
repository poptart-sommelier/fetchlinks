import { expect, it } from "vitest";

import { getBlueskyFollows } from "./bluesky";
import { describePostgres, usePostgres } from "./test-support/postgres";

describePostgres("bluesky follows", () => {
  const pg = usePostgres();

  async function addFollow(
    did: string,
    handle: string,
    syncedAt: string,
    displayName: string | null = null,
  ): Promise<void> {
    await pg.exec(
      `INSERT INTO content.bluesky_follows (did, handle, display_name, synced_at)
       VALUES ($1, $2, $3, $4)`,
      [did, handle, displayName, syncedAt],
    );
  }

  it("returns an empty snapshot when nothing has been synced", async () => {
    await expect(getBlueskyFollows(pg.sql)).resolves.toEqual({
      follows: [],
      lastSyncedAt: null,
    });
  });

  it("orders follows by handle without regard to case", async () => {
    await addFollow("did:1", "Zoe.bsky.social", "2026-01-01T00:00:00Z");
    await addFollow("did:2", "adam.bsky.social", "2026-01-01T00:00:00Z");

    const snapshot = await getBlueskyFollows(pg.sql);

    expect(snapshot.follows.map((follow) => follow.handle)).toEqual([
      "adam.bsky.social",
      "Zoe.bsky.social",
    ]);
  });

  it("reports the most recent sync time across all follows", async () => {
    await addFollow("did:1", "a.bsky.social", "2026-01-01T00:00:00Z");
    await addFollow("did:2", "b.bsky.social", "2026-03-01T12:00:00Z");

    const snapshot = await getBlueskyFollows(pg.sql);

    expect(snapshot.lastSyncedAt).toBe("2026-03-01T12:00:00Z");
  });

  it("matches posts to a follow by profile url, ignoring handle case", async () => {
    await addFollow("did:1", "Someone.bsky.social", "2026-01-01T00:00:00Z");
    for (const [uniqueId, postedAt] of [
      ["p1", "2026-01-05T00:00:00Z"],
      ["p2", "2026-02-05T00:00:00Z"],
    ] as const) {
      await pg.exec(
        `INSERT INTO content.posts (unique_id, source, source_type, posted_at)
         VALUES ($1, $2, 'bluesky', $3)`,
        [
          uniqueId,
          "https://bsky.app/profile/someone.bsky.social",
          postedAt,
        ],
      );
    }

    const snapshot = await getBlueskyFollows(pg.sql);

    expect(snapshot.follows[0]?.latestPostAt).toBe("2026-02-05T00:00:00Z");
    expect(snapshot.follows[0]?.postSource).toBe(
      "https://bsky.app/profile/someone.bsky.social",
    );
  });

  it("reports no posts for a follow that has not posted", async () => {
    await addFollow("did:1", "quiet.bsky.social", "2026-01-01T00:00:00Z");
    await pg.exec(
      `INSERT INTO content.posts (unique_id, source, source_type, posted_at)
       VALUES ('other', 'https://bsky.app/profile/loud.bsky.social', 'bluesky', '2026-01-01T00:00:00Z')`,
    );

    const snapshot = await getBlueskyFollows(pg.sql);

    expect(snapshot.follows[0]?.latestPostAt).toBeNull();
    expect(snapshot.follows[0]?.postSource).toBeNull();
  });

  it("carries the display name through", async () => {
    await addFollow("did:1", "a.bsky.social", "2026-01-01T00:00:00Z", "Ada L.");

    const snapshot = await getBlueskyFollows(pg.sql);

    expect(snapshot.follows[0]?.displayName).toBe("Ada L.");
  });
});

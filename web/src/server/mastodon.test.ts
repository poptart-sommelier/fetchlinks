import { expect, it } from "vitest";

import { getMastodonFollows } from "./mastodon";
import { describePostgres, usePostgres } from "./test-support/postgres";

describePostgres("mastodon follows", () => {
  const pg = usePostgres();

  async function addFollow(
    instanceName: string,
    accountId: string,
    acct: string,
    syncedAt: string,
    url: string | null = null,
  ): Promise<void> {
    await pg.exec(
      `INSERT INTO content.mastodon_follows
         (instance_name, account_id, acct, display_name, url, synced_at)
       VALUES ($1, $2, $3, NULL, $4, $5)`,
      [instanceName, accountId, acct, url, syncedAt],
    );
  }

  it("returns an empty snapshot when nothing has been synced", async () => {
    await expect(getMastodonFollows(pg.sql)).resolves.toEqual({
      follows: [],
      lastSyncedAt: null,
    });
  });

  it("groups by instance, then orders by account without regard to case", async () => {
    await addFollow("mastodon.social", "2", "Zoe", "2026-01-01T00:00:00Z");
    await addFollow("mastodon.social", "1", "adam", "2026-01-01T00:00:00Z");
    await addFollow("fosstodon.org", "3", "kim", "2026-01-01T00:00:00Z");

    const snapshot = await getMastodonFollows(pg.sql);

    expect(
      snapshot.follows.map((follow) => [follow.instanceName, follow.acct]),
    ).toEqual([
      ["fosstodon.org", "kim"],
      ["mastodon.social", "adam"],
      ["mastodon.social", "Zoe"],
    ]);
  });

  it("reports the most recent sync time across all instances", async () => {
    await addFollow("mastodon.social", "1", "a", "2026-01-01T00:00:00Z");
    await addFollow("fosstodon.org", "2", "b", "2026-04-01T09:00:00Z");

    const snapshot = await getMastodonFollows(pg.sql);

    expect(snapshot.lastSyncedAt).toBe("2026-04-01T09:00:00Z");
  });

  // Mastodon accounts are matched on the profile url exactly, since the same
  // acct can exist on more than one instance.
  it("matches posts to a follow by profile url", async () => {
    await addFollow(
      "mastodon.social",
      "1",
      "someone",
      "2026-01-01T00:00:00Z",
      "https://mastodon.social/@someone",
    );
    for (const [uniqueId, postedAt] of [
      ["p1", "2026-01-05T00:00:00Z"],
      ["p2", "2026-02-05T00:00:00Z"],
    ] as const) {
      await pg.exec(
        `INSERT INTO content.posts (unique_id, source, source_type, posted_at)
         VALUES ($1, 'https://mastodon.social/@someone', 'mastodon', $2)`,
        [uniqueId, postedAt],
      );
    }

    const snapshot = await getMastodonFollows(pg.sql);

    expect(snapshot.follows[0]?.latestPostAt).toBe("2026-02-05T00:00:00Z");
    expect(snapshot.follows[0]?.postSource).toBe(
      "https://mastodon.social/@someone",
    );
  });

  it("reports no posts for a follow with no url recorded", async () => {
    await addFollow("mastodon.social", "1", "someone", "2026-01-01T00:00:00Z");
    await pg.exec(
      `INSERT INTO content.posts (unique_id, source, source_type, posted_at)
       VALUES ('p1', 'https://mastodon.social/@someone', 'mastodon', '2026-01-01T00:00:00Z')`,
    );

    const snapshot = await getMastodonFollows(pg.sql);

    expect(snapshot.follows[0]?.latestPostAt).toBeNull();
  });
});

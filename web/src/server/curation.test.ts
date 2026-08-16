import { expect, it } from "vitest";

import {
  clearMute,
  clearThumbsDown,
  composeTargetKey,
  getMutesFor,
  getThumbsDownsFor,
  lookupKey,
  setMute,
  setThumbsDown,
} from "./curation";
import { describePostgres, usePostgres } from "./test-support/postgres";

const CHANNEL = {
  type: "channel" as const,
  key: composeTargetKey("reddit", "netsec"),
  label: "r/netsec",
};

const ACTOR = {
  type: "actor" as const,
  key: composeTargetKey("reddit", "grace"),
  label: "Grace",
};

describePostgres("owner thumbs down", () => {
  const pg = usePostgres();

  it("records one article's feedback and reads it back", async () => {
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });

    const states = await getThumbsDownsFor(
      pg.sql,
      [CHANNEL],
      ["post-1"],
    );

    expect(states.get(lookupKey(CHANNEL.type, CHANNEL.key))).toEqual({
      count: 1,
      activePostUniqueIds: ["post-1"],
    });
  });

  it("does not count repeated clicks on one article twice", async () => {
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: { ...CHANNEL, label: "r/netsec (renamed)" },
    });

    const [row] = (await pg.exec(
      `SELECT count(*)::int AS count, target_label AS label
       FROM curation.thumbs_downs
       WHERE target_type = $1 AND target_key = $2
       GROUP BY target_label`,
      [CHANNEL.type, CHANNEL.key],
    )) as { count: number; label: string }[];

    expect(row).toEqual({ count: 1, label: "r/netsec (renamed)" });
  });

  it("accumulates the same target across distinct articles", async () => {
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-2",
      target: CHANNEL,
    });

    const states = await getThumbsDownsFor(
      pg.sql,
      [CHANNEL],
      ["post-2"],
    );

    expect(states.get(lookupKey(CHANNEL.type, CHANNEL.key))).toEqual({
      count: 2,
      // The aggregate returns pressed state only for articles on this page.
      activePostUniqueIds: ["post-2"],
    });
  });

  it("keeps separate targets independent", async () => {
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: ACTOR,
    });

    const states = await getThumbsDownsFor(
      pg.sql,
      [CHANNEL, ACTOR],
      ["post-1"],
    );

    expect(states.get(lookupKey(CHANNEL.type, CHANNEL.key))?.count).toBe(1);
    expect(states.get(lookupKey(ACTOR.type, ACTOR.key))?.count).toBe(1);
  });

  it("does not confuse one source's key with another's", async () => {
    const mastodon = {
      type: "channel" as const,
      key: composeTargetKey("mastodon", "netsec"),
      label: "infosec.exchange",
    };

    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });
    await setThumbsDown(pg.sql, {
      postUniqueId: "post-2",
      target: mastodon,
    });

    const states = await getThumbsDownsFor(
      pg.sql,
      [CHANNEL, mastodon],
      ["post-1", "post-2"],
    );

    expect(states.size).toBe(2);
    expect(states.get(lookupKey(CHANNEL.type, CHANNEL.key))?.count).toBe(1);
    expect(states.get(lookupKey(mastodon.type, mastodon.key))?.count).toBe(1);
  });

  it("clears only this article's contribution to the count", async () => {
    for (const postUniqueId of ["post-1", "post-2"]) {
      await setThumbsDown(pg.sql, { postUniqueId, target: CHANNEL });
    }

    await clearThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });

    const states = await getThumbsDownsFor(
      pg.sql,
      [CHANNEL],
      ["post-1", "post-2"],
    );
    expect(states.get(lookupKey(CHANNEL.type, CHANNEL.key))).toEqual({
      count: 1,
      activePostUniqueIds: ["post-2"],
    });
  });

  it("treats clearing absent evidence as a no-op", async () => {
    await expect(
      clearThumbsDown(pg.sql, {
        postUniqueId: "post-1",
        target: CHANNEL,
      }),
    ).resolves.toBeUndefined();
  });

  it("survives retention deleting the post that prompted it", async () => {
    const [post] = (await pg.exec(
      `INSERT INTO content.posts
         (unique_id, source, source_type, author, description, direct_link, posted_at)
       VALUES ('post-1', 'https://example.com/feed', 'rss', '', '', '', now())
       RETURNING post_id`,
    )) as { post_id: string }[];

    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });
    await pg.exec("DELETE FROM content.posts WHERE post_id = $1", [
      post.post_id,
    ]);

    const states = await getThumbsDownsFor(pg.sql, [CHANNEL], []);
    expect(states.get(lookupKey(CHANNEL.type, CHANNEL.key))?.count).toBe(1);
  });

  it("turns matching legacy evidence into a deliberate Manage action", async () => {
    await pg.exec(
      `INSERT INTO curation.ratings
         (target_type, target_key, target_label, verdict, post_unique_id)
       VALUES ($1, $2, $3, 'noise', 'post-1')`,
      [CHANNEL.type, CHANNEL.key, CHANNEL.label],
    );

    await setThumbsDown(pg.sql, {
      postUniqueId: "post-1",
      target: CHANNEL,
    });

    const [row] = (await pg.exec(
      `SELECT legacy_rating_id AS "legacyRatingId"
       FROM curation.thumbs_downs`,
    )) as { legacyRatingId: string | null }[];
    expect(row.legacyRatingId).toBeNull();
  });

  it("refuses an unknown target or missing identity", async () => {
    await expect(
      setThumbsDown(pg.sql, {
        postUniqueId: "post-1",
        target: { ...CHANNEL, type: "everything" as never },
      }),
    ).rejects.toThrow(/target type/i);
    await expect(
      setThumbsDown(pg.sql, {
        postUniqueId: "post-1",
        target: { ...CHANNEL, key: "" },
      }),
    ).rejects.toThrow(/key/i);
    await expect(
      setThumbsDown(pg.sql, {
        postUniqueId: "",
        target: CHANNEL,
      }),
    ).rejects.toThrow(/article/i);
  });

  it("asks for no rows when given no targets", async () => {
    await expect(getThumbsDownsFor(pg.sql, [], [])).resolves.toEqual(new Map());
  });

  it("sets one explicit mute idempotently and reads it back", async () => {
    await setMute(pg.sql, CHANNEL);
    await setMute(pg.sql, { ...CHANNEL, label: "r/netsec renamed" });

    await expect(getMutesFor(pg.sql, [CHANNEL, ACTOR])).resolves.toEqual(
      new Set([lookupKey(CHANNEL.type, CHANNEL.key)]),
    );
    const rows = (await pg.exec(
      `SELECT count(*)::int AS count, target_label AS label
       FROM curation.mutes
       GROUP BY target_label`,
    )) as { count: number; label: string }[];
    expect(rows).toEqual([{ count: 1, label: "r/netsec renamed" }]);
  });

  it("unmutes only the requested target and treats absence as a no-op", async () => {
    await setMute(pg.sql, CHANNEL);
    await setMute(pg.sql, ACTOR);

    await clearMute(pg.sql, CHANNEL);
    await clearMute(pg.sql, CHANNEL);

    await expect(getMutesFor(pg.sql, [CHANNEL, ACTOR])).resolves.toEqual(
      new Set([lookupKey(ACTOR.type, ACTOR.key)]),
    );
  });

  it("validates mute targets and skips an empty lookup", async () => {
    await expect(
      setMute(pg.sql, { ...CHANNEL, type: "everything" as never }),
    ).rejects.toThrow(/target type/i);
    await expect(clearMute(pg.sql, { ...CHANNEL, key: "" })).rejects.toThrow(
      /key/i,
    );
    await expect(getMutesFor(pg.sql, [])).resolves.toEqual(new Set());
  });
});

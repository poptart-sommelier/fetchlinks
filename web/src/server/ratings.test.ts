import { expect, it } from "vitest";

import {
  clearRating,
  composeTargetKey,
  getRatingsFor,
  getScoresFor,
  rateTarget,
  scoreOf,
} from "./ratings";
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

describePostgres("owner ratings", () => {
  const pg = usePostgres();

  it("records a verdict and reads it back", async () => {
    await rateTarget(pg.sql, { target: CHANNEL, verdict: "noise" });

    const verdicts = await getRatingsFor(pg.sql, [CHANNEL]);

    expect(verdicts.get(`channel\u001f${CHANNEL.key}`)).toBe("noise");
  });

  it("replaces a verdict rather than accumulating opinions", async () => {
    await rateTarget(pg.sql, { target: CHANNEL, verdict: "noise" });
    await rateTarget(pg.sql, { target: CHANNEL, verdict: "good" });

    const scores = await getScoresFor(pg.sql, [CHANNEL]);
    const score = scores.get(`channel\u001f${CHANNEL.key}`);

    expect(score?.good).toBe(1);
    expect(score?.noise).toBe(0);
  });

  it("refreshes the stored label when a target is renamed", async () => {
    await rateTarget(pg.sql, { target: CHANNEL, verdict: "good" });
    await rateTarget(pg.sql, {
      target: { ...CHANNEL, label: "r/netsec (renamed)" },
      verdict: "good",
    });

    const [row] = (await pg.exec(
      "SELECT target_label FROM curation.ratings WHERE target_key = $1",
      [CHANNEL.key],
    )) as { target_label: string }[];

    expect(row.target_label).toBe("r/netsec (renamed)");
  });

  it("keeps separate targets independent", async () => {
    await rateTarget(pg.sql, { target: ACTOR, verdict: "good" });
    await rateTarget(pg.sql, { target: CHANNEL, verdict: "noise" });

    const verdicts = await getRatingsFor(pg.sql, [ACTOR, CHANNEL]);

    expect(verdicts.get(`actor\u001f${ACTOR.key}`)).toBe("good");
    expect(verdicts.get(`channel\u001f${CHANNEL.key}`)).toBe("noise");
  });

  it("does not confuse one source's key with another's", async () => {
    const mastodon = {
      type: "channel" as const,
      key: composeTargetKey("mastodon", "netsec"),
      label: "infosec.exchange",
    };

    await rateTarget(pg.sql, { target: CHANNEL, verdict: "noise" });
    await rateTarget(pg.sql, { target: mastodon, verdict: "good" });

    const verdicts = await getRatingsFor(pg.sql, [CHANNEL, mastodon]);

    expect(verdicts.get(`channel\u001f${CHANNEL.key}`)).toBe("noise");
    expect(verdicts.get(`channel\u001f${mastodon.key}`)).toBe("good");
  });

  it("returns a target to neutral when cleared", async () => {
    await rateTarget(pg.sql, { target: CHANNEL, verdict: "noise" });
    await clearRating(pg.sql, CHANNEL);

    const verdicts = await getRatingsFor(pg.sql, [CHANNEL]);

    expect(verdicts.size).toBe(0);
  });

  it("treats clearing an unrated target as a no-op", async () => {
    await expect(clearRating(pg.sql, CHANNEL)).resolves.toBeUndefined();
  });

  it("survives the retention sweep that deletes the post it came from", async () => {
    const [post] = (await pg.exec(
      `INSERT INTO content.posts
         (unique_id, source, source_type, author, description, direct_link, posted_at)
       VALUES ('post-1', 'https://example.com/feed', 'rss', '', '', '', now())
       RETURNING post_id`,
    )) as { post_id: string }[];

    await rateTarget(pg.sql, {
      target: { type: "post", key: "post-1", label: "An article" },
      verdict: "good",
      postUniqueId: "post-1",
    });

    await pg.exec("DELETE FROM content.posts WHERE post_id = $1", [
      post.post_id,
    ]);

    // The evidence is the point. Losing it every month would make long-run
    // source judgments impossible, which is the whole feature.
    const verdicts = await getRatingsFor(pg.sql, [
      { type: "post", key: "post-1" },
    ]);

    expect(verdicts.get("post\u001fpost-1")).toBe("good");
  });

  it("refuses a verdict or target type it does not know", async () => {
    await expect(
      rateTarget(pg.sql, {
        target: CHANNEL,
        verdict: "excellent" as never,
      }),
    ).rejects.toThrow();

    await expect(
      rateTarget(pg.sql, {
        target: { ...CHANNEL, type: "everything" as never },
        verdict: "good",
      }),
    ).rejects.toThrow();
  });

  it("asks for no rows when given no targets", async () => {
    await expect(getRatingsFor(pg.sql, [])).resolves.toEqual(new Map());
    await expect(getScoresFor(pg.sql, [])).resolves.toEqual(new Map());
  });
});

it("keeps a single click away from an extreme score", () => {
  expect(scoreOf(0, 0)).toBe(50);
  expect(scoreOf(1, 0)).toBe(60);
  expect(scoreOf(0, 1)).toBe(40);
  // Sustained evidence does move it decisively.
  expect(scoreOf(0, 20)).toBe(8);
  expect(scoreOf(20, 0)).toBe(92);
});

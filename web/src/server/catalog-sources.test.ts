import { expect, it } from "vitest";

import { composeTargetKey, type CurationTarget } from "./curation";
import {
  getCatalogSourcesFor,
  resolveCatalogSource,
} from "./catalog-sources";
import { describePostgres, usePostgres } from "./test-support/postgres";

describePostgres("Manage catalog source resolution", () => {
  const pg = usePostgres();

  it("resolves real RSS feeds and subreddits with explicit states", async () => {
    await pg.exec(
      `INSERT INTO catalog.rss_feeds
         (feed_url, normalized_url, enabled, added_at)
       VALUES
         ('https://active.example/feed', 'https://active.example/feed', true, now()),
         ('https://removed.example/feed', 'https://removed.example/feed', false, now())`,
    );
    await pg.exec(
      `UPDATE catalog.rss_feeds
       SET deleted_at = now()
       WHERE normalized_url = 'https://removed.example/feed'`,
    );
    await pg.exec(
      `INSERT INTO catalog.subreddits
         (name, normalized_name, enabled, added_at)
       VALUES
         ('ActiveSub', 'activesub', true, now()),
         ('DisabledSub', 'disabledsub', false, now())`,
    );
    const targets: CurationTarget[] = [
      channel("rss", "https://active.example/feed"),
      channel("rss", "https://removed.example/feed"),
      channel("reddit", "activesub"),
      channel("reddit", "disabledsub"),
    ];

    const sources = await getCatalogSourcesFor(pg.sql, targets);

    expect(sources.get(identity(targets[0]!))).toMatchObject({
      kind: "rss",
      status: "active",
    });
    expect(sources.get(identity(targets[1]!))).toMatchObject({
      kind: "rss",
      status: "removed",
    });
    expect(sources.get(identity(targets[2]!))).toMatchObject({
      kind: "subreddit",
      status: "active",
    });
    expect(sources.get(identity(targets[3]!))).toMatchObject({
      kind: "subreddit",
      status: "disabled",
    });
  });

  it("rejects posts, actors, domains, other networks, and unknown channels", async () => {
    await pg.exec(
      `INSERT INTO catalog.rss_feeds
         (feed_url, normalized_url, enabled, added_at)
       VALUES ('https://real.example/feed', 'https://real.example/feed', true, now())`,
    );
    const ineligible: CurationTarget[] = [
      { type: "post", key: "post-1", label: "post" },
      {
        type: "actor",
        key: composeTargetKey("reddit", "person"),
        label: "person",
      },
      { type: "domain", key: "example.com", label: "example.com" },
      channel("bluesky", "did:plc:abc"),
      channel("mastodon", "social.example"),
      channel("rss", "https://forged.example/feed"),
    ];

    for (const target of ineligible) {
      await expect(resolveCatalogSource(pg.sql, target)).rejects.toThrow(
        /not an RSS feed or subreddit/i,
      );
    }
  });
});

function channel(sourceType: string, key: string): CurationTarget {
  return {
    type: "channel",
    key: composeTargetKey(sourceType, key),
    label: key,
  };
}

function identity(target: CurationTarget): string {
  return `${target.type}\u001f${target.key}`;
}

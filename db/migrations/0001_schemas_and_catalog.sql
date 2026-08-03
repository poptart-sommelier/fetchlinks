-- 0001: schemas and the admin-owned catalog.
--
-- `catalog` holds subscription *identity and on/off state*. It is owned by the
-- web admin UI: the admin adds, disables, soft-deletes and restores feeds and
-- subreddits here, and the Publisher only ever reads it in order to export a
-- catalog snapshot for the Collector.
--
-- Health, cache validators and counters deliberately do NOT live here. They are
-- collection outcomes, they belong to `content`, and keeping them out of the
-- catalog is what lets the Collector consume a catalog snapshot that can never
-- roll back a resume position.

CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS content;


CREATE TABLE IF NOT EXISTS catalog.rss_feeds (
    feed_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_url       text        NOT NULL,
    normalized_url text        NOT NULL UNIQUE,
    enabled        boolean     NOT NULL DEFAULT true,
    added_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    CONSTRAINT rss_feeds_feed_url_not_blank CHECK (feed_url <> ''),
    CONSTRAINT rss_feeds_normalized_url_not_blank CHECK (normalized_url <> '')
);

-- The catalog export and the admin list both ask the same question: which feeds
-- are live? A partial index keeps that lookup proportional to the live set
-- rather than to everything ever subscribed to.
CREATE INDEX IF NOT EXISTS idx_rss_feeds_live
    ON catalog.rss_feeds (normalized_url)
    WHERE enabled AND deleted_at IS NULL;


CREATE TABLE IF NOT EXISTS catalog.subreddits (
    subreddit_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text        NOT NULL,
    normalized_name text        NOT NULL UNIQUE,
    enabled         boolean     NOT NULL DEFAULT true,
    added_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    CONSTRAINT subreddits_name_not_blank CHECK (name <> ''),
    CONSTRAINT subreddits_normalized_name_not_blank CHECK (normalized_name <> '')
);

CREATE INDEX IF NOT EXISTS idx_subreddits_live
    ON catalog.subreddits (normalized_name)
    WHERE enabled AND deleted_at IS NULL;

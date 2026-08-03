-- 0002: the Publisher-owned content schema.
--
-- Everything here is a collection outcome. The Publisher is the only writer;
-- the web application reads it. Column names follow the batch contract
-- (`unique_id`, `posted_at`, `observed_at`) rather than the old SQLite names
-- (`unique_id_string`, `date_created`, `time_created`) so that a reader can
-- check the Publisher against the contract without a translation table.


-- --------------------------------------------------------------------------
-- Posts and their URLs
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS content.posts (
    post_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The Collector's natural identity for a post: a digest of its sorted URL
    -- set. This, not the surrogate key, is what makes republishing idempotent.
    unique_id     text        NOT NULL UNIQUE,
    source        text        NOT NULL DEFAULT '',
    source_type   text        NOT NULL,
    author        text        NOT NULL DEFAULT '',
    description   text        NOT NULL DEFAULT '',
    direct_link   text        NOT NULL DEFAULT '',
    posted_at     timestamptz NOT NULL,
    -- When this row first reached the database. Distinct from `posted_at`,
    -- which is when the source published it, and useful for answering "what
    -- did the last publish actually add?" without consulting the spool.
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    -- Deliberately a shape constraint, not a value whitelist. The batch
    -- contract does not enumerate source types, and the reason for choosing a
    -- CHECK over an enum was so that adding a source needs no migration; a
    -- whitelist would throw that away.
    CONSTRAINT posts_source_type_shape CHECK (
        source_type <> ''
        AND source_type = lower(source_type)
        AND length(source_type) <= 32
    ),
    CONSTRAINT posts_unique_id_not_blank CHECK (unique_id <> '')
);

CREATE INDEX IF NOT EXISTS idx_posts_posted_at   ON content.posts (posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_source_type ON content.posts (source_type);
CREATE INDEX IF NOT EXISTS idx_posts_source      ON content.posts (source);
CREATE INDEX IF NOT EXISTS idx_posts_author      ON content.posts (author);


CREATE TABLE IF NOT EXISTS content.post_urls (
    post_url_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id         bigint  NOT NULL
                        REFERENCES content.posts (post_id) ON DELETE CASCADE,
    position        integer NOT NULL,
    url             text    NOT NULL,
    -- Derived by the Publisher from `url`, never carried in a batch, so an old
    -- Collector cannot pin a stale hashing scheme into the database.
    url_hash        text    NOT NULL,
    -- Reserved for the unshortener, which currently only reads. Nullable so an
    -- unresolved short link is distinguishable from one resolved to itself.
    unshortened_url text,
    CONSTRAINT post_urls_position_non_negative CHECK (position >= 0),
    CONSTRAINT post_urls_url_not_blank CHECK (url <> ''),
    CONSTRAINT post_urls_position_unique UNIQUE (post_id, position),
    CONSTRAINT post_urls_hash_unique     UNIQUE (post_id, url_hash)
);

CREATE INDEX IF NOT EXISTS idx_post_urls_post     ON content.post_urls (post_id);
CREATE INDEX IF NOT EXISTS idx_post_urls_url_hash ON content.post_urls (url_hash);


-- --------------------------------------------------------------------------
-- Per-feed health
-- --------------------------------------------------------------------------
--
-- Joined to catalog.rss_feeds on `normalized_url` rather than on the catalog's
-- surrogate key: health survives a feed being removed and re-added, and the two
-- tables are owned by different roles.

CREATE TABLE IF NOT EXISTS content.rss_feed_health (
    normalized_url       text PRIMARY KEY,
    last_fetched_at      timestamptz,
    last_success_at      timestamptz,
    last_status          integer,
    last_error           text,
    consecutive_failures integer NOT NULL DEFAULT 0,
    etag                 text,
    -- An HTTP header value echoed back verbatim in If-Modified-Since. Parsing
    -- it into a timestamptz would risk not reproducing it byte for byte.
    last_modified        text,
    latest_entry_at      timestamptz,
    site_link            text,
    CONSTRAINT rss_feed_health_failures_non_negative
        CHECK (consecutive_failures >= 0)
);

CREATE INDEX IF NOT EXISTS idx_rss_feed_health_failing
    ON content.rss_feed_health (consecutive_failures DESC)
    WHERE consecutive_failures > 0;


-- --------------------------------------------------------------------------
-- Source checkpoints
-- --------------------------------------------------------------------------
--
-- The Collector keeps its own authoritative resume state on disk; these tables
-- are the published view of it, for operator visibility and for rebuilding a
-- Collector that has lost its state file. Every one carries `observed_at` so
-- that replaying an older batch cannot move a cursor backwards.

CREATE TABLE IF NOT EXISTS content.reddit_state (
    subreddit          text PRIMARY KEY,
    last_seen_fullname text,
    source_url         text,
    observed_at        timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS content.bluesky_state (
    -- 'timeline' today; a key rather than a single-row table so a second
    -- stream does not need a schema change.
    source_key  text PRIMARY KEY,
    cursor      text,
    source_url  text,
    observed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS content.mastodon_state (
    source_name  text PRIMARY KEY,
    instance_url text,
    last_seen_id text,
    observed_at  timestamptz NOT NULL
);


-- --------------------------------------------------------------------------
-- Follows snapshots
-- --------------------------------------------------------------------------
--
-- Follows arrive as complete snapshots, so publishing one is a replacement, not
-- a merge. `follows_snapshots` records when each scope was last observed; the
-- Publisher checks it before replacing, which is what stops a delayed batch
-- from reinstating a follow list the user has since changed.

CREATE TABLE IF NOT EXISTS content.follows_snapshots (
    source_type text        NOT NULL,
    scope       text        NOT NULL,
    observed_at timestamptz NOT NULL,
    record_count integer    NOT NULL DEFAULT 0,
    PRIMARY KEY (source_type, scope),
    CONSTRAINT follows_snapshots_count_non_negative CHECK (record_count >= 0)
);

CREATE TABLE IF NOT EXISTS content.bluesky_follows (
    did          text PRIMARY KEY,
    handle       text        NOT NULL,
    display_name text,
    synced_at    timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bluesky_follows_handle
    ON content.bluesky_follows (handle);

CREATE TABLE IF NOT EXISTS content.mastodon_follows (
    instance_name text        NOT NULL,
    account_id    text        NOT NULL,
    acct          text        NOT NULL,
    display_name  text,
    url           text,
    synced_at     timestamptz NOT NULL,
    PRIMARY KEY (instance_name, account_id)
);

CREATE INDEX IF NOT EXISTS idx_mastodon_follows_instance
    ON content.mastodon_follows (instance_name);


-- --------------------------------------------------------------------------
-- Batch ledger
-- --------------------------------------------------------------------------
--
-- The Publisher inserts here first, inside the same transaction as the batch's
-- content. A second attempt at the same batch id conflicts and applies nothing,
-- which is what makes a crash between "database committed" and "spool directory
-- moved" recoverable rather than a source of double-counted health and
-- resurrected follows.

CREATE TABLE IF NOT EXISTS content.published_batches (
    batch_id          text PRIMARY KEY,
    contract_version  integer     NOT NULL,
    batch_created_at  timestamptz NOT NULL,
    published_at      timestamptz NOT NULL DEFAULT now(),
    collector_version text,
    collector_commit  text,
    catalog_revision  text,
    record_count      integer     NOT NULL DEFAULT 0,
    posts_inserted    integer     NOT NULL DEFAULT 0,
    urls_inserted     integer     NOT NULL DEFAULT 0,
    CONSTRAINT published_batches_counts_non_negative CHECK (
        record_count >= 0 AND posts_inserted >= 0 AND urls_inserted >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_published_batches_published_at
    ON content.published_batches (published_at DESC);

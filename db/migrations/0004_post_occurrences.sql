-- 0004: every origin of a deduplicated link, and the host each link points at.
--
-- A post's identity is a digest of its sorted URL set, so the same article
-- arriving from an RSS feed and then from Reddit is one row in content.posts
-- and the second arrival used to be discarded outright. That was fine while the
-- only question was "show me the link", and wrong the moment the question
-- became "which sources are worth keeping": the discarded arrival is exactly
-- the evidence that a source found something.
--
-- So the origin moves out of content.posts and into its own table. content.posts
-- keeps its `source`/`author` columns as the display attribution of whichever
-- arrival was seen first; content.post_occurrences records all of them,
-- including that first one.


-- --------------------------------------------------------------------------
-- Where a link points
-- --------------------------------------------------------------------------
--
-- Rating "this domain is noise" needs the domain as a value that can be
-- grouped and indexed, not one parsed out of a URL by every query that cares.
--
-- Generated rather than written by the Publisher, for the same reason
-- `url_hash` is derived rather than carried in a batch: the rule lives in one
-- place and cannot be pinned to whatever an old Collector believed. It also
-- means the column follows `unshortened_url` automatically if the unshortener
-- is ever wired up to write, instead of needing a second update path that
-- could be forgotten.
--
-- Normalization matches ingest/rss_feed_import.py's canonical_hostname: lower
-- case, strip a leading `www.`, and nothing else. Other subdomains stay
-- distinct on purpose -- collapsing them would merge every github.io page into
-- a single ratable target.

ALTER TABLE content.post_urls
    ADD COLUMN IF NOT EXISTS url_host text
    GENERATED ALWAYS AS (
        substring(
            lower(coalesce(nullif(unshortened_url, ''), url))
            from '^[a-z][a-z0-9+.-]*://(?:www[.])?([^/?#:]+)'
        )
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_post_urls_host ON content.post_urls (url_host);


-- --------------------------------------------------------------------------
-- Who published a post, and where
-- --------------------------------------------------------------------------
--
-- The key/label split is the important part. A key is what a rating is
-- attached to and must survive the account being renamed, so it is the most
-- stable identifier the source offers: a Bluesky DID rather than a handle, a
-- Mastodon account URI rather than an `acct`, the feed's own URL rather than
-- the website it advertises. A label is only ever displayed.
--
-- Both are NOT NULL DEFAULT '' rather than nullable because they are part of
-- the uniqueness key below, and NULL does not compare equal to NULL. An empty
-- key means "this source has no such dimension" -- RSS feeds have no author,
-- the Bluesky timeline has no channel -- and empty is also what a batch written
-- by the older contract version carries, which is the honest answer for those:
-- the origin was recorded, its identity was not.

CREATE TABLE IF NOT EXISTS content.post_occurrences (
    occurrence_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id       bigint      NOT NULL
                      REFERENCES content.posts (post_id) ON DELETE CASCADE,
    source_type   text        NOT NULL,
    -- The place it appeared: feed URL, subreddit, Mastodon instance.
    channel_key   text        NOT NULL DEFAULT '',
    channel_label text        NOT NULL DEFAULT '',
    -- The account that posted it: Reddit username, Bluesky DID, Mastodon URI.
    actor_key     text        NOT NULL DEFAULT '',
    actor_label   text        NOT NULL DEFAULT '',
    -- Display origin and permalink for this arrival specifically. A Reddit
    -- occurrence of an article links to the Reddit thread, not to the article.
    source        text        NOT NULL DEFAULT '',
    direct_link   text        NOT NULL DEFAULT '',
    posted_at     timestamptz NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT post_occurrences_source_type_shape CHECK (
        source_type <> ''
        AND source_type = lower(source_type)
        AND length(source_type) <= 32
    ),
    -- What makes republishing safe: a second arrival from the same account in
    -- the same place is the same occurrence, not a new one.
    CONSTRAINT post_occurrences_identity
        UNIQUE (post_id, source_type, channel_key, actor_key)
);

CREATE INDEX IF NOT EXISTS idx_post_occurrences_post
    ON content.post_occurrences (post_id);
-- Scoring reads by target, so index the two key columns the ratings hang off.
-- Partial, because the empty key is not a target anyone can rate and indexing
-- it would just be a large entry for rows nobody looks up this way.
CREATE INDEX IF NOT EXISTS idx_post_occurrences_channel
    ON content.post_occurrences (source_type, channel_key)
    WHERE channel_key <> '';
CREATE INDEX IF NOT EXISTS idx_post_occurrences_actor
    ON content.post_occurrences (source_type, actor_key)
    WHERE actor_key <> '';


-- --------------------------------------------------------------------------
-- Backfill
-- --------------------------------------------------------------------------
--
-- One occurrence per existing post, so nothing downstream has to special-case
-- a post with no origin at all.
--
-- Keys are left empty for every source except Reddit, and that restraint is
-- deliberate. Reddit's stored `author` really is the username and its `source`
-- really does contain the subreddit, so those keys will match what the
-- Collector writes from now on. For RSS the feed URL was never stored on the
-- post; for Bluesky and Mastodon the stored author is a display name, which is
-- not identity. Inventing keys from those would create targets that can be
-- rated once and never matched again, quietly splitting a source's score in
-- two. Empty is worth more than wrong, and the one-month retention window
-- replaces all of it with fully attributed rows anyway.

INSERT INTO content.post_occurrences
    (post_id, source_type, channel_key, channel_label, actor_key, actor_label,
     source, direct_link, posted_at, first_seen_at)
SELECT
    p.post_id,
    p.source_type,
    CASE WHEN p.source_type = 'reddit'
         THEN lower(coalesce(substring(p.source from 'reddit[.]com/r/([^/?#]+)'), ''))
         ELSE '' END,
    CASE WHEN p.source_type = 'reddit'
         THEN coalesce(substring(p.source from 'reddit[.]com/(r/[^/?#]+)'), '')
         ELSE '' END,
    CASE WHEN p.source_type = 'reddit' THEN lower(p.author) ELSE '' END,
    p.author,
    p.source,
    p.direct_link,
    p.posted_at,
    p.first_seen_at
FROM content.posts p
ON CONFLICT ON CONSTRAINT post_occurrences_identity DO NOTHING;


-- --------------------------------------------------------------------------
-- Grants
-- --------------------------------------------------------------------------
--
-- There are none, deliberately. 0003 set ALTER DEFAULT PRIVILEGES on this
-- schema, so a table created here is already readable by the web role and
-- writable by the publisher. Repeating the grants would also make this
-- migration depend on those roles existing, which the web application's own
-- test harness does not arrange -- it applies the schema without 0003 on
-- purpose.
--
-- What that default privilege depends on is subtle enough to be worth stating:
-- it only applies to objects created by the same role that set it, so it holds
-- exactly as long as every migration is run with the same credentials. The
-- permission tests assert the outcome on this table rather than trusting it.

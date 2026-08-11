-- 0005: owner curation ratings.
--
-- One curator rates things: individual posts, the channels they arrive
-- through, the accounts that post them, and the domains they link to. This is
-- evidence gathering, not moderation -- nothing here hides anything. Acting on
-- a score is a later, separate decision.
--
-- The schema is its own, because curation is neither collected content nor
-- subscription catalog. It is the only schema the web application writes for a
-- reason other than administering sources, and the Publisher never touches it
-- at all.

CREATE SCHEMA IF NOT EXISTS curation;


-- --------------------------------------------------------------------------
-- Ratings
-- --------------------------------------------------------------------------
--
-- A rating is (target type, target key) -> good or noise. The target key is a
-- text identifier whose meaning depends on the type:
--
--   post     the post's unique_id, not its post_id
--   channel  source_type + channel_key from an occurrence
--   actor    source_type + actor_key from an occurrence
--   domain   the normalized url_host of a linked URL
--
-- `unique_id` rather than `post_id` for posts is the important one. Retention
-- deletes posts after a month and this table must not follow them: the whole
-- point is to accumulate evidence about sources over time, and a foreign key
-- would erase it on schedule. A `unique_id` is stable, so if the same link is
-- collected again the old judgment still attaches to it.
--
-- The compound keys for channels and actors are stored pre-joined as
-- `source_type` and the key separated by a unit-separator character, rather
-- than as two columns. Separate columns would have to be nullable for post and
-- domain targets, and nullable columns inside a uniqueness key are exactly the
-- trap 0004 avoided.

CREATE TABLE IF NOT EXISTS curation.ratings (
    rating_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_type  text        NOT NULL,
    target_key   text        NOT NULL,
    -- What the target was called when it was rated. Labels drift and targets
    -- disappear; a review queue that cannot name what it is asking about is
    -- useless, so the name is snapshotted rather than joined for.
    target_label text        NOT NULL DEFAULT '',
    verdict      text        NOT NULL,
    -- Which post prompted this, when one did. Deliberately not a foreign key,
    -- for the retention reason above: it is provenance, not a relationship.
    post_unique_id text      NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ratings_target_type_known CHECK (
        target_type IN ('post', 'channel', 'actor', 'domain')
    ),
    CONSTRAINT ratings_verdict_known CHECK (verdict IN ('good', 'noise')),
    CONSTRAINT ratings_target_key_present CHECK (target_key <> ''),
    -- One curator, so one verdict per target. Changing your mind updates the
    -- row; clearing a rating deletes it. There is no history table because
    -- nobody would ever read it.
    CONSTRAINT ratings_target_identity UNIQUE (target_type, target_key)
);

-- Rendering a page of posts asks "what do I already think about these?" for
-- every target on every card at once, which is a lookup by type and key.
CREATE INDEX IF NOT EXISTS idx_ratings_target
    ON curation.ratings (target_type, target_key);


-- --------------------------------------------------------------------------
-- Grants
-- --------------------------------------------------------------------------
--
-- Curation is the web application's alone. Migration 0003 revokes PUBLIC on
-- the schemas it created and sets default privileges there, but it knew
-- nothing of this schema, so both have to be stated here.
--
-- The Publisher is granted nothing at all -- not even SELECT. The Pi holds a
-- credential on a residential connection and has no reason to read the
-- owner's private judgments about sources.
--
-- DELETE is granted, unlike on the catalog, because clearing a rating back to
-- neutral is a real thing the owner does and a soft-deleted rating would only
-- complicate every read of this table.

REVOKE ALL ON SCHEMA curation FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fetchlinks_web') THEN
        GRANT USAGE ON SCHEMA curation TO fetchlinks_web;
        GRANT SELECT, INSERT, UPDATE, DELETE ON curation.ratings
            TO fetchlinks_web;
        ALTER DEFAULT PRIVILEGES IN SCHEMA curation
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fetchlinks_web;
        ALTER DEFAULT PRIVILEGES IN SCHEMA curation
            GRANT USAGE ON SEQUENCES TO fetchlinks_web;
    END IF;
END
$$;

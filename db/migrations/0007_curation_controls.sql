-- 0007: cumulative thumbs-down evidence and explicit mutes.
--
-- The first curation pass stored one Good / Noise verdict per target. That was
-- enough to learn whether rating beside an article was usable, but it cannot
-- answer the question the owner now cares about: "how many different articles
-- made me object to this feed, account or domain?" The old uniqueness rule makes
-- every count zero or one.
--
-- Keep `curation.ratings` intact while the old web deployment still uses it.
-- The replacement is additive so this migration can be applied before the UI
-- changes without breaking production. A later migration may remove the legacy
-- table after every environment runs the new code.


-- --------------------------------------------------------------------------
-- Thumbs-down evidence
-- --------------------------------------------------------------------------
--
-- One row means "this target was marked down from this article." The prompting
-- post is text rather than a foreign key because content retention deletes the
-- post after a month while the evidence is supposed to accumulate over time.
-- The compound identity makes repeat clicks on one card idempotent without
-- collapsing objections prompted by different articles.

CREATE TABLE IF NOT EXISTS curation.thumbs_downs (
    thumbs_down_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_unique_id text        NOT NULL,
    target_type    text        NOT NULL,
    target_key     text        NOT NULL,
    target_label   text        NOT NULL DEFAULT '',
    -- Set only on evidence mirrored from the legacy ratings table. It lets the
    -- transition trigger remove or replace that one old opinion without
    -- touching genuine per-article evidence written by Manage.
    legacy_rating_id bigint,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT thumbs_downs_target_type_known CHECK (
        target_type IN ('post', 'channel', 'actor', 'domain')
    ),
    CONSTRAINT thumbs_downs_post_present CHECK (post_unique_id <> ''),
    CONSTRAINT thumbs_downs_target_key_present CHECK (target_key <> ''),
    CONSTRAINT thumbs_downs_legacy_rating_positive CHECK (
        legacy_rating_id IS NULL OR legacy_rating_id > 0
    ),
    CONSTRAINT thumbs_downs_legacy_rating_unique UNIQUE (legacy_rating_id),
    CONSTRAINT thumbs_downs_evidence_identity UNIQUE (
        post_unique_id, target_type, target_key
    )
);

-- A page asks for the accumulated count of every target it renders.
CREATE INDEX IF NOT EXISTS idx_thumbs_downs_target
    ON curation.thumbs_downs (target_type, target_key);


-- Preserve every existing Noise verdict as one observation. Current owner
-- actions always store the prompting post. The two fallbacks cover rows made by
-- early tests or hand-written SQL: a post target identifies its own article,
-- while any other target gets an impossible unit-separator-prefixed legacy key.
-- It still counts as the one opinion it represented without pretending to know
-- which article prompted it.
INSERT INTO curation.thumbs_downs (
    post_unique_id,
    target_type,
    target_key,
    target_label,
    legacy_rating_id,
    created_at,
    updated_at
)
SELECT
    CASE
        WHEN post_unique_id <> '' THEN post_unique_id
        WHEN target_type = 'post' THEN target_key
        ELSE chr(31) || 'legacy-rating:' || rating_id::text
    END,
    target_type,
    target_key,
    target_label,
    rating_id,
    created_at,
    updated_at
FROM curation.ratings
WHERE verdict = 'noise'
ON CONFLICT ON CONSTRAINT thumbs_downs_evidence_identity DO NOTHING;


-- Keep the copied opinion accurate while production still serves the old Rate
-- form. This is transition machinery, not the new evidence model: the old table
-- can still represent only one opinion per target. `legacy_rating_id` ensures a
-- later Good or Clear removes only the row this trigger owns. Once Manage is
-- deployed no application writes `curation.ratings`, so the trigger is dormant.
CREATE OR REPLACE FUNCTION curation.sync_legacy_rating_thumb()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        DELETE FROM curation.thumbs_downs
        WHERE legacy_rating_id = OLD.rating_id;
    END IF;

    IF TG_OP <> 'DELETE' AND NEW.verdict = 'noise' THEN
        INSERT INTO curation.thumbs_downs (
            post_unique_id,
            target_type,
            target_key,
            target_label,
            legacy_rating_id,
            created_at,
            updated_at
        )
        VALUES (
            CASE
                WHEN NEW.post_unique_id <> '' THEN NEW.post_unique_id
                WHEN NEW.target_type = 'post' THEN NEW.target_key
                ELSE chr(31) || 'legacy-rating:' || NEW.rating_id::text
            END,
            NEW.target_type,
            NEW.target_key,
            NEW.target_label,
            NEW.rating_id,
            NEW.created_at,
            NEW.updated_at
        )
        ON CONFLICT ON CONSTRAINT thumbs_downs_evidence_identity DO UPDATE SET
            target_label = EXCLUDED.target_label,
            updated_at = EXCLUDED.updated_at;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sync_legacy_rating_thumb ON curation.ratings;
CREATE TRIGGER sync_legacy_rating_thumb
AFTER INSERT OR UPDATE OR DELETE ON curation.ratings
FOR EACH ROW EXECUTE FUNCTION curation.sync_legacy_rating_thumb();


-- --------------------------------------------------------------------------
-- Explicit mutes
-- --------------------------------------------------------------------------
--
-- A mute is an owner decision, not a score threshold or a derived state.
-- Unmuting deletes the row, so public reads only need the small set that exists
-- now. Labels are snapshots for explaining a mute after its source disappears;
-- identity remains the stable type and key.

CREATE TABLE IF NOT EXISTS curation.mutes (
    mute_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_type  text        NOT NULL,
    target_key   text        NOT NULL,
    target_label text        NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mutes_target_type_known CHECK (
        target_type IN ('post', 'channel', 'actor', 'domain')
    ),
    CONSTRAINT mutes_target_key_present CHECK (target_key <> ''),
    CONSTRAINT mutes_target_identity UNIQUE (target_type, target_key)
);


-- --------------------------------------------------------------------------
-- Grants
-- --------------------------------------------------------------------------
--
-- None here. Migration 0005 made `curation` a web-owned surface and set default
-- table and sequence privileges for `fetchlinks_web`. The Publisher was granted
-- no schema usage at all. Relying on those defaults preserves both boundaries
-- and keeps this migration usable by the web query test harness, which does not
-- create production roles.

-- 0003: runtime roles and grants.
--
-- Three roles, separated by what they are allowed to break:
--
--   fetchlinks_owner      owns the schema; used only to run migrations.
--   fetchlinks_web        Vercel. Reads everything, writes only the catalog.
--   fetchlinks_publisher  the Pi. Reads the catalog, writes only content.
--
-- Roles are created without a password. Setting one is a deliberate operator
-- step (`ALTER ROLE ... WITH PASSWORD ...`) so that no credential can ever
-- arrive by way of this repository.
--
-- Note that soft deletion is enforced here, not just by convention: the web
-- role is granted INSERT and UPDATE on the catalog but never DELETE, so a bug
-- in an admin action cannot destroy subscription history.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fetchlinks_owner') THEN
        CREATE ROLE fetchlinks_owner LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fetchlinks_web') THEN
        CREATE ROLE fetchlinks_web LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fetchlinks_publisher') THEN
        CREATE ROLE fetchlinks_publisher LOGIN;
    END IF;
END
$$;


-- Nothing runtime-facing should be able to add objects, and the default
-- PUBLIC CREATE grant on `public` is exactly the hole that would let it.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA catalog FROM PUBLIC;
REVOKE ALL ON SCHEMA content FROM PUBLIC;

GRANT USAGE ON SCHEMA catalog TO fetchlinks_web, fetchlinks_publisher;
GRANT USAGE ON SCHEMA content TO fetchlinks_web, fetchlinks_publisher;


-- --- web -----------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA catalog TO fetchlinks_web;
GRANT SELECT ON ALL TABLES IN SCHEMA content TO fetchlinks_web;
GRANT INSERT, UPDATE ON catalog.rss_feeds  TO fetchlinks_web;
GRANT INSERT, UPDATE ON catalog.subreddits TO fetchlinks_web;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA catalog TO fetchlinks_web;


-- --- publisher -----------------------------------------------------------
-- Read-only on the catalog: the Pi exports it, it does not curate it.
GRANT SELECT ON ALL TABLES IN SCHEMA catalog TO fetchlinks_publisher;
-- DELETE is required for two legitimate jobs: replacing a follows snapshot
-- wholesale, and applying the retention cutoff to old posts.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA content
    TO fetchlinks_publisher;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA content TO fetchlinks_publisher;


-- --- future objects ------------------------------------------------------
-- Without this, the next migration to add a table silently leaves both runtime
-- roles unable to see it, and the failure shows up in production rather than
-- in the migration.
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog
    GRANT SELECT ON TABLES TO fetchlinks_web, fetchlinks_publisher;
ALTER DEFAULT PRIVILEGES IN SCHEMA content
    GRANT SELECT ON TABLES TO fetchlinks_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA content
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fetchlinks_publisher;
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog
    GRANT USAGE ON SEQUENCES TO fetchlinks_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA content
    GRANT USAGE ON SEQUENCES TO fetchlinks_publisher;

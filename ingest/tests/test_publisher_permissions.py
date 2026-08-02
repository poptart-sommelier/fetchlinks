"""Tests that the runtime roles cannot exceed their remit.

Grants are the last line of defence for the two failure modes that would be
worst here: a compromised Vercel deployment rewriting collected content, and a
publisher bug editing the catalog the admin curates. Both are prevented by the
database, not by application code, so both are tested against the database.
"""

from __future__ import annotations

import unittest

from tests.pg_support import PostgresTestCase, available, role_url

if available():
    import psycopg


class RolePermissionTests(PostgresTestCase):
    def as_role(self, role: str):
        conn = psycopg.connect(role_url(role))
        self.addCleanup(conn.close)
        return conn

    def assertDenied(self, conn, sql: str, params=()):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute(sql, params)
        conn.rollback()

    def assertAllowed(self, conn, sql: str, params=()):
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.rollback()

    # -- web ----------------------------------------------------------------

    def test_web_can_read_content_and_catalog(self):
        conn = self.as_role('fetchlinks_web')
        self.assertAllowed(conn, 'SELECT count(*) FROM content.posts')
        self.assertAllowed(conn, 'SELECT count(*) FROM content.rss_feed_health')
        self.assertAllowed(conn, 'SELECT count(*) FROM catalog.rss_feeds')

    def test_web_can_curate_the_catalog(self):
        conn = self.as_role('fetchlinks_web')
        self.assertAllowed(
            conn,
            'INSERT INTO catalog.rss_feeds (feed_url, normalized_url) '
            "VALUES ('https://a.example/feed', 'https://a.example/feed')",
        )
        self.assertAllowed(
            conn, 'UPDATE catalog.rss_feeds SET enabled = false'
        )

    def test_web_cannot_write_content(self):
        conn = self.as_role('fetchlinks_web')
        self.assertDenied(
            conn,
            'INSERT INTO content.posts (unique_id, source_type, posted_at) '
            "VALUES ('x', 'rss', now())",
        )
        self.assertDenied(conn, 'DELETE FROM content.posts')
        self.assertDenied(conn, 'UPDATE content.rss_feed_health SET last_status = 200')

    def test_web_cannot_hard_delete_catalog_entries(self):
        # Removal is a soft delete, and the grant is what enforces it.
        conn = self.as_role('fetchlinks_web')
        self.assertDenied(conn, 'DELETE FROM catalog.rss_feeds')

    # -- publisher ----------------------------------------------------------

    def test_publisher_can_write_content(self):
        conn = self.as_role('fetchlinks_publisher')
        self.assertAllowed(
            conn,
            'INSERT INTO content.posts (unique_id, source_type, posted_at) '
            "VALUES ('x', 'rss', now())",
        )
        self.assertAllowed(conn, 'DELETE FROM content.bluesky_follows')
        self.assertAllowed(conn, 'DELETE FROM content.posts')

    def test_publisher_can_read_but_not_change_the_catalog(self):
        conn = self.as_role('fetchlinks_publisher')
        self.assertAllowed(conn, 'SELECT count(*) FROM catalog.rss_feeds')
        self.assertDenied(
            conn,
            'INSERT INTO catalog.rss_feeds (feed_url, normalized_url) '
            "VALUES ('https://b.example/feed', 'https://b.example/feed')",
        )
        self.assertDenied(conn, 'UPDATE catalog.subreddits SET enabled = false')

    # -- neither ------------------------------------------------------------

    def test_no_runtime_role_can_change_the_schema(self):
        for role in ('fetchlinks_web', 'fetchlinks_publisher'):
            conn = self.as_role(role)
            with self.subTest(role=role):
                self.assertDenied(conn, 'CREATE TABLE content.sneaky (id int)')
                self.assertDenied(conn, 'CREATE TABLE catalog.sneaky (id int)')
                self.assertDenied(conn, 'DROP TABLE content.posts')
                self.assertDenied(conn, 'ALTER TABLE content.posts ADD COLUMN x int')

    def test_no_runtime_role_can_create_objects_in_public(self):
        for role in ('fetchlinks_web', 'fetchlinks_publisher'):
            conn = self.as_role(role)
            with self.subTest(role=role):
                self.assertDenied(conn, 'CREATE TABLE public.sneaky (id int)')

    def test_no_runtime_role_can_rewrite_the_migration_ledger(self):
        for role in ('fetchlinks_web', 'fetchlinks_publisher'):
            conn = self.as_role(role)
            with self.subTest(role=role):
                self.assertDenied(conn, 'DELETE FROM public.schema_migrations')


if __name__ == '__main__':
    unittest.main()

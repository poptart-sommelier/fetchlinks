"""Tests for the SQL migration runner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.pg_support import PostgresTestCase

from publisher.migrations import (
    MigrationError,
    default_migrations_dir,
    discover,
    migrate,
    pending,
)

#: Well clear of the real migrations so a stray row is obviously synthetic.
SCRATCH_VERSIONS = ('9001', '9002')


class DiscoveryTests(unittest.TestCase):
    def _dir(self, *names: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name in names:
            (root / name).write_text('SELECT 1;', encoding='utf-8')
        return root

    def test_returns_migrations_in_version_order(self):
        root = self._dir('0002_second.sql', '0001_first.sql', '0010_tenth.sql')
        self.assertEqual(
            [m.version for m in discover(root)], ['0001', '0002', '0010']
        )

    def test_rejects_a_filename_that_is_not_versioned(self):
        root = self._dir('0001_first.sql', 'cleanup.sql')
        with self.assertRaises(MigrationError):
            discover(root)

    def test_rejects_two_migrations_claiming_one_version(self):
        root = self._dir('0001_first.sql', '0001_also_first.sql')
        with self.assertRaises(MigrationError):
            discover(root)

    def test_ignores_non_sql_files(self):
        root = self._dir('0001_first.sql')
        (root / 'README.md').write_text('notes', encoding='utf-8')
        self.assertEqual([m.version for m in discover(root)], ['0001'])

    def test_missing_directory_is_an_error_not_an_empty_run(self):
        with self.assertRaises(MigrationError):
            discover(Path(tempfile.gettempdir()) / 'definitely-not-there-12345')

    def test_shipped_migrations_are_discoverable(self):
        # Guards the path arithmetic in default_migrations_dir, which is easy
        # to break by moving the package and hard to notice until deployment.
        versions = [m.version for m in discover(default_migrations_dir())]
        self.assertEqual(versions[:3], ['0001', '0002', '0003'])


class MigrationRunTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(self._forget_scratch_migrations)

    def _forget_scratch_migrations(self):
        with self.conn.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS migtest CASCADE')
            cur.execute(
                'DELETE FROM public.schema_migrations WHERE version = ANY(%s)',
                (list(SCRATCH_VERSIONS),),
            )
        self.conn.commit()

    def write(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(body, encoding='utf-8')
        return path

    def test_applies_and_records_each_migration_once(self):
        self.write('9001_scratch.sql',
                   'CREATE SCHEMA migtest; CREATE TABLE migtest.a (id int);')
        self.write('9002_scratch_more.sql', 'CREATE TABLE migtest.b (id int);')

        self.assertEqual(migrate(self.conn, self.root), ['9001', '9002'])
        self.assertEqual(migrate(self.conn, self.root), [],
                         're-running must be a no-op')
        self.assertEqual(
            self.scalar(
                'SELECT count(*) FROM public.schema_migrations '
                'WHERE version = ANY(%s)', (list(SCRATCH_VERSIONS),)
            ),
            2,
        )

    def test_dry_run_reports_without_applying(self):
        self.write('9001_scratch.sql', 'CREATE SCHEMA migtest;')
        self.assertEqual(migrate(self.conn, self.root, dry_run=True), ['9001'])
        self.assertEqual(
            self.scalar("SELECT count(*) FROM information_schema.schemata "
                        "WHERE schema_name = 'migtest'"),
            0,
        )

    def test_editing_an_applied_migration_is_reported(self):
        self.write('9001_scratch.sql', 'CREATE SCHEMA migtest;')
        migrate(self.conn, self.root)
        self.write('9001_scratch.sql', 'CREATE SCHEMA migtest; -- tweak')

        with self.assertRaises(MigrationError) as ctx:
            pending(self.conn, discover(self.root))
        self.assertIn('has changed since it was applied', str(ctx.exception))

    def test_a_failing_migration_rolls_back_and_is_not_recorded(self):
        self.write('9001_scratch.sql', 'CREATE SCHEMA migtest;')
        self.write('9002_scratch_more.sql', 'THIS IS NOT SQL;')

        with self.assertRaises(MigrationError):
            migrate(self.conn, self.root)

        self.conn.rollback()
        self.assertEqual(
            self.scalar('SELECT count(*) FROM public.schema_migrations '
                        'WHERE version = %s', ('9002',)),
            0,
        )
        self.assertEqual(
            self.scalar('SELECT count(*) FROM public.schema_migrations '
                        'WHERE version = %s', ('9001',)),
            1,
            'an earlier migration that succeeded stays applied and recorded',
        )


class ShippedSchemaTests(PostgresTestCase):
    """The migrations produce the objects the Publisher and web depend on."""

    def table_exists(self, schema: str, table: str) -> bool:
        return bool(self.scalar(
            'SELECT count(*) FROM information_schema.tables '
            'WHERE table_schema = %s AND table_name = %s', (schema, table)
        ))

    def test_every_expected_table_exists(self):
        expected = [
            ('catalog', 'rss_feeds'),
            ('catalog', 'subreddits'),
            ('content', 'posts'),
            ('content', 'post_urls'),
            ('content', 'rss_feed_health'),
            ('content', 'reddit_state'),
            ('content', 'bluesky_state'),
            ('content', 'mastodon_state'),
            ('content', 'bluesky_follows'),
            ('content', 'mastodon_follows'),
            ('content', 'follows_snapshots'),
            ('content', 'published_batches'),
        ]
        missing = [f'{s}.{t}' for s, t in expected if not self.table_exists(s, t)]
        self.assertEqual(missing, [])

    def test_application_timestamps_are_timezone_aware(self):
        # A naive timestamp column would silently reinterpret every UTC value
        # the contract carries as local time.
        rows = self.rows(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema IN ('catalog', 'content') "
            "AND data_type LIKE 'timestamp%'"
        )
        naive = [r for r in rows if r[2] != 'timestamp with time zone']
        self.assertEqual(naive, [])

    def test_runtime_roles_exist(self):
        roles = {row[0] for row in self.rows(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'fetchlinks%'"
        )}
        self.assertEqual(
            roles,
            {'fetchlinks_owner', 'fetchlinks_web', 'fetchlinks_publisher'},
        )


if __name__ == '__main__':
    unittest.main()

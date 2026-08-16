"""Tests for the SQL migration runner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import psycopg

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
        self.assertEqual(versions[:4], ['0001', '0002', '0003', '0004'])


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
            ('content', 'post_occurrences'),
            ('content', 'rss_feed_health'),
            ('content', 'reddit_state'),
            ('content', 'bluesky_state'),
            ('content', 'mastodon_state'),
            ('content', 'bluesky_follows'),
            ('content', 'mastodon_follows'),
            ('content', 'follows_snapshots'),
            ('content', 'published_batches'),
            ('content', 'operation_runs'),
            ('curation', 'ratings'),
            ('curation', 'thumbs_downs'),
            ('curation', 'mutes'),
        ]
        missing = [f'{s}.{t}' for s, t in expected if not self.table_exists(s, t)]
        self.assertEqual(missing, [])

    def test_application_timestamps_are_timezone_aware(self):
        # A naive timestamp column would silently reinterpret every UTC value
        # the contract carries as local time.
        rows = self.rows(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema IN ('catalog', 'content', 'curation') "
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


class CurationControlsTableTests(PostgresTestCase):
    """0007 keeps evidence cumulative and mutes explicit."""

    def insert_thumb(self, post: str, target_type='channel',
                     target_key='rss\x1fhttps://example.com/feed'):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO curation.thumbs_downs '
                '(post_unique_id, target_type, target_key) '
                'VALUES (%s, %s, %s)',
                (post, target_type, target_key),
            )
        self.conn.commit()

    def test_one_target_accumulates_evidence_from_distinct_articles(self):
        self.insert_thumb('post-1')
        self.insert_thumb('post-2')

        self.assertEqual(self.count('curation.thumbs_downs'), 2)

    def test_one_article_cannot_inflate_a_target_count(self):
        self.insert_thumb('post-1')

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.insert_thumb('post-1')
        self.conn.rollback()

        self.assertEqual(self.count('curation.thumbs_downs'), 1)

    def test_evidence_survives_the_prompting_post(self):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO content.posts '
                '(unique_id, source_type, posted_at) '
                "VALUES ('post-1', 'rss', now())"
            )
        self.conn.commit()
        self.insert_thumb('post-1')

        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM content.posts WHERE unique_id = 'post-1'")
        self.conn.commit()

        self.assertEqual(self.count('curation.thumbs_downs'), 1)

    def test_unknown_or_blank_evidence_is_rejected(self):
        for values in (
            ('post-1', 'everything', 'key'),
            ('', 'channel', 'key'),
            ('post-1', 'channel', ''),
        ):
            with self.subTest(values=values):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.insert_thumb(*values)
                self.conn.rollback()

    def test_one_explicit_mute_per_target(self):
        values = ('channel', 'rss\x1fhttps://example.com/feed', 'Example')
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO curation.mutes '
                '(target_type, target_key, target_label) VALUES (%s, %s, %s)',
                values,
            )
        self.conn.commit()

        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO curation.mutes '
                    '(target_type, target_key, target_label) '
                    'VALUES (%s, %s, %s)',
                    values,
                )
            self.conn.commit()
        self.conn.rollback()

        self.assertEqual(self.count('curation.mutes'), 1)

    def test_unknown_or_blank_mutes_are_rejected(self):
        for target_type, target_key in (
            ('everything', 'key'),
            ('channel', ''),
        ):
            with self.subTest(
                target_type=target_type, target_key=target_key
            ):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.cursor() as cur:
                        cur.execute(
                            'INSERT INTO curation.mutes '
                            '(target_type, target_key) VALUES (%s, %s)',
                            (target_type, target_key),
                        )
                    self.conn.commit()
                self.conn.rollback()

    def test_unmuting_is_deleting_the_decision(self):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO curation.mutes (target_type, target_key) '
                "VALUES ('domain', 'example.com')"
            )
            cur.execute(
                "DELETE FROM curation.mutes WHERE target_type = 'domain' "
                "AND target_key = 'example.com'"
            )
        self.conn.commit()

        self.assertEqual(self.count('curation.mutes'), 0)

    def test_existing_noise_is_copied_but_good_is_not(self):
        # Re-run the additive migration after arranging the state 0005 would
        # have left. The recreated tables remain the correct current schema for
        # the rest of the suite.
        with self.conn.cursor() as cur:
            cur.execute(
                'DROP TRIGGER sync_legacy_rating_thumb '
                'ON curation.ratings'
            )
            cur.execute(
                'DROP FUNCTION curation.sync_legacy_rating_thumb()'
            )
            cur.execute('DROP TABLE curation.mutes')
            cur.execute('DROP TABLE curation.thumbs_downs')
            cur.execute(
                'INSERT INTO curation.ratings '
                '(target_type, target_key, target_label, verdict, post_unique_id) '
                'VALUES '
                "('channel', 'rss\x1fnoise', 'Noise feed', 'noise', 'post-1'), "
                "('channel', 'rss\x1fgood', 'Good feed', 'good', 'post-2')"
            )
            migration = (
                default_migrations_dir() / '0007_curation_controls.sql'
            ).read_text(encoding='utf-8')
            cur.execute(migration)
        self.conn.commit()

        self.assertEqual(
            self.rows(
                'SELECT post_unique_id, target_key, target_label '
                'FROM curation.thumbs_downs'
            ),
            [('post-1', 'rss\x1fnoise', 'Noise feed')],
        )

    def test_legacy_rating_changes_stay_synced_during_cutover(self):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO curation.ratings '
                '(target_type, target_key, verdict, post_unique_id) '
                "VALUES ('channel', 'rss\x1ffeed', 'good', 'post-1') "
                'RETURNING rating_id'
            )
            rating_id = cur.fetchone()[0]
        self.conn.commit()
        self.assertEqual(self.count('curation.thumbs_downs'), 0)

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE curation.ratings SET verdict = 'noise' "
                'WHERE rating_id = %s',
                (rating_id,),
            )
        self.conn.commit()
        self.assertEqual(
            self.rows(
                'SELECT post_unique_id, legacy_rating_id '
                'FROM curation.thumbs_downs'
            ),
            [('post-1', rating_id)],
        )

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE curation.ratings SET verdict = 'good' "
                'WHERE rating_id = %s',
                (rating_id,),
            )
        self.conn.commit()
        self.assertEqual(self.count('curation.thumbs_downs'), 0)

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE curation.ratings SET verdict = 'noise' "
                'WHERE rating_id = %s',
                (rating_id,),
            )
            cur.execute(
                'DELETE FROM curation.ratings WHERE rating_id = %s',
                (rating_id,),
            )
        self.conn.commit()
        self.assertEqual(self.count('curation.thumbs_downs'), 0)


class OperationRunsTableTests(PostgresTestCase):
    """The rules 0006 relies on the database, not the caller, to enforce."""

    def insert(self, **values):
        columns = ', '.join(values)
        placeholders = ', '.join(['%s'] * len(values))
        with self.conn.cursor() as cur:
            cur.execute(
                f'INSERT INTO content.operation_runs ({columns}) '
                f'VALUES ({placeholders})',
                list(values.values()),
            )
        self.conn.commit()

    def assertRejected(self, **values):
        try:
            with self.assertRaises(psycopg.errors.Error):
                self.insert(**values)
        finally:
            self.conn.rollback()

    def test_an_unfinished_run_is_recorded_as_running(self):
        self.insert(job='publish', started_at='2026-01-01T00:00:00Z', result='running')
        self.assertEqual(self.count('content.operation_runs'), 1)

    def test_a_running_row_may_not_claim_a_finish_time(self):
        self.assertRejected(
            job='publish',
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z',
            result='running',
        )

    def test_a_finished_row_must_say_when_it_finished(self):
        # Otherwise a forgotten assignment reads as a job that is still going,
        # forever, which is indistinguishable from the outage being looked for.
        self.assertRejected(
            job='publish', started_at='2026-01-01T00:00:00Z', result='ok'
        )

    def test_an_unknown_result_is_refused(self):
        self.assertRejected(
            job='publish',
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z',
            result='probably fine',
        )

    def test_an_unbounded_error_message_is_refused(self):
        self.assertRejected(
            job='collection',
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z',
            result='failed',
            error_message='x' * 501,
        )

    def test_details_must_be_an_object(self):
        self.assertRejected(
            job='publish',
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z',
            result='ok',
            details='[1, 2]',
        )

    def test_a_replayed_collection_cannot_be_recorded_twice(self):
        batch_id = '20260101T000000000000Z-abcdef01'
        self.insert(
            job='collection',
            run_key=batch_id,
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z',
            result='ok',
        )
        self.assertRejected(
            job='collection',
            run_key=batch_id,
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z',
            result='ok',
        )

    def test_runs_with_no_natural_key_do_not_collide(self):
        # The publisher writes one of these every hour and they are all
        # distinct events, so the uniqueness rule must not reach them.
        for _ in range(3):
            self.insert(
                job='publish',
                started_at='2026-01-01T00:00:00Z',
                finished_at='2026-01-01T00:01:00Z',
                result='ok',
            )
        self.assertEqual(self.count('content.operation_runs'), 3)

    def test_the_same_key_may_be_reused_by_a_different_job(self):
        self.insert(
            job='collection', run_key='k',
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z', result='ok',
        )
        self.insert(
            job='retention', run_key='k',
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-01-01T00:01:00Z', result='ok',
        )
        self.assertEqual(self.count('content.operation_runs'), 2)


if __name__ == '__main__':
    unittest.main()

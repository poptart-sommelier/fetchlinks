"""Tests for seeding the PostgreSQL catalog and exporting it for the Collector."""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from tests.pg_support import PostgresTestCase, available

from pipeline.catalog import Catalog

if available():
    from publisher.bootstrap import bootstrap_catalog
    from publisher.catalog_sync import sync_catalog


def reddit_config(*names, seed_file=None):
    return types.SimpleNamespace(subreddits=list(names), seed_file=seed_file)


class BootstrapTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def seed_file(self, *lines: str) -> Path:
        path = self.root / 'rss_feeds.txt'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def test_seeds_feeds_and_subreddits(self):
        path = self.seed_file(
            '# comment', '', 'https://a.example/feed', 'https://B.example/Feed',
        )
        report = bootstrap_catalog(
            self.conn,
            feeds_seed_path=path,
            reddit_config=reddit_config('netsec', 'r/Python'),
        )

        self.assertEqual(report.feeds_inserted, 2)
        self.assertEqual(report.subreddits_inserted, 2)
        self.assertEqual(
            [r[0] for r in self.rows(
                'SELECT normalized_url FROM catalog.rss_feeds ORDER BY 1')],
            ['https://a.example/feed', 'https://b.example/Feed'],
        )
        self.assertEqual(
            self.rows('SELECT normalized_name, name FROM catalog.subreddits '
                      "WHERE normalized_name = 'python'")[0],
            ('python', 'Python'),
        )

    def test_rerunning_inserts_nothing_new(self):
        path = self.seed_file('https://a.example/feed')
        bootstrap_catalog(self.conn, feeds_seed_path=path,
                          reddit_config=reddit_config('netsec'))
        report = bootstrap_catalog(self.conn, feeds_seed_path=path,
                                   reddit_config=reddit_config('netsec'))

        self.assertEqual(report.feeds_inserted, 0)
        self.assertEqual(report.subreddits_inserted, 0)
        self.assertEqual(self.count('catalog.rss_feeds'), 1)

    def test_does_not_re_enable_a_feed_the_admin_disabled(self):
        # The seed file is a historical bootstrap list; the catalog is live
        # admin state. Re-running the bootstrap must not undo a decision.
        path = self.seed_file('https://a.example/feed')
        bootstrap_catalog(self.conn, feeds_seed_path=path)
        with self.conn.cursor() as cur:
            cur.execute('UPDATE catalog.rss_feeds SET enabled = false, '
                        'deleted_at = now()')
        self.conn.commit()

        bootstrap_catalog(self.conn, feeds_seed_path=path)

        row = self.rows('SELECT enabled, deleted_at FROM catalog.rss_feeds')[0]
        self.assertFalse(row[0])
        self.assertIsNotNone(row[1])

    def test_no_seed_sources_is_a_harmless_no_op(self):
        report = bootstrap_catalog(self.conn)
        self.assertEqual(report.feeds_seen, 0)
        self.assertEqual(self.count('catalog.rss_feeds'), 0)


class CatalogSyncTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'catalog' / 'catalog.v1.json'

    def add_feed(self, normalized, url, *, enabled=True, deleted=False):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO catalog.rss_feeds '
                '(feed_url, normalized_url, enabled, deleted_at) '
                'VALUES (%s, %s, %s, CASE WHEN %s THEN now() END)',
                (url, normalized, enabled, deleted),
            )
        self.conn.commit()

    def add_subreddit(self, normalized, name, *, enabled=True):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO catalog.subreddits (name, normalized_name, enabled) '
                'VALUES (%s, %s, %s)', (name, normalized, enabled)
            )
        self.conn.commit()

    def test_exports_only_live_entries(self):
        self.add_feed('https://a.example/feed', 'https://a.example/feed')
        self.add_feed('https://off.example/feed', 'https://off.example/feed',
                      enabled=False)
        self.add_feed('https://gone.example/feed', 'https://gone.example/feed',
                      deleted=True)
        self.add_subreddit('netsec', 'netsec')
        self.add_subreddit('old', 'old', enabled=False)

        catalog = sync_catalog(self.conn, self.path)

        self.assertEqual(
            [f.normalized_url for f in catalog.feeds], ['https://a.example/feed']
        )
        self.assertEqual(
            [s.normalized_name for s in catalog.subreddits], ['netsec']
        )
        self.assertEqual(catalog.source, 'postgresql')

    def test_written_snapshot_reloads_and_verifies(self):
        self.add_feed('https://a.example/feed', 'https://a.example/feed')
        written = sync_catalog(self.conn, self.path)
        reloaded = Catalog.load(self.path)
        self.assertEqual(reloaded.revision, written.revision)

    def test_revision_is_stable_when_the_list_is_unchanged(self):
        self.add_feed('https://a.example/feed', 'https://a.example/feed')
        first = sync_catalog(self.conn, self.path)
        second = sync_catalog(self.conn, self.path)
        self.assertEqual(first.revision, second.revision)

    def test_revision_changes_when_the_list_changes(self):
        self.add_feed('https://a.example/feed', 'https://a.example/feed')
        first = sync_catalog(self.conn, self.path)
        self.add_feed('https://b.example/feed', 'https://b.example/feed')
        second = sync_catalog(self.conn, self.path)
        self.assertNotEqual(first.revision, second.revision)

    def test_refuses_to_replace_a_populated_snapshot_with_an_empty_one(self):
        self.add_feed('https://a.example/feed', 'https://a.example/feed')
        sync_catalog(self.conn, self.path)

        with self.conn.cursor() as cur:
            cur.execute('UPDATE catalog.rss_feeds SET enabled = false')
        self.conn.commit()

        with self.assertRaises(RuntimeError):
            sync_catalog(self.conn, self.path)

        self.assertEqual(len(Catalog.load(self.path).feeds), 1,
                         'the last good snapshot must survive')

    def test_first_export_of_an_empty_catalog_is_allowed(self):
        # Nothing to protect yet, and refusing would block a fresh install.
        catalog = sync_catalog(self.conn, self.path)
        self.assertTrue(catalog.is_empty)
        self.assertTrue(self.path.exists())


if __name__ == '__main__':
    unittest.main()

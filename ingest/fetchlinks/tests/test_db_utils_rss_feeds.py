"""Tests for the rss_feeds helpers in db_utils."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_setup
import db_utils


def _fresh_db(tmp: Path) -> Path:
    db_path = tmp / 'fetchlinks.db'
    db_setup.db_initial_setup(db_path)
    return db_path


def _row_by_id(db_path: Path, feed_id: int) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            'SELECT * FROM rss_feeds WHERE feed_id = ?', (feed_id,)
        ).fetchone())


class CountAndInsertTests(unittest.TestCase):
    def test_count_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            self.assertEqual(db_utils.db_count_rss_feeds(db_path), 0)

    def test_insert_deduplicates_on_normalized_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            feeds = [
                ('https://a.example/feed', 'https://a.example/feed'),
                ('https://b.example/feed', 'https://b.example/feed'),
                ('https://A.EXAMPLE/feed', 'https://a.example/feed'),  # dup
            ]
            inserted = db_utils.db_insert_rss_feeds(feeds, db_path)
            self.assertEqual(inserted, 2)
            self.assertEqual(db_utils.db_count_rss_feeds(db_path), 2)

    def test_insert_empty_list_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            self.assertEqual(db_utils.db_insert_rss_feeds([], db_path), 0)


class GetActiveTests(unittest.TestCase):
    def test_excludes_disabled_and_tombstoned(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            db_utils.db_insert_rss_feeds(
                [
                    ('https://a/', 'https://a/'),
                    ('https://b/', 'https://b/'),
                    ('https://c/', 'https://c/'),
                ],
                db_path,
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute('UPDATE rss_feeds SET enabled = 0 WHERE feed_url = ?',
                             ('https://b/',))
                conn.execute("UPDATE rss_feeds SET deleted_at = '2026-05-01' "
                             "WHERE feed_url = ?", ('https://c/',))
                conn.commit()

            active = db_utils.db_get_active_rss_feeds(db_path)
            urls = {row[1] for row in active}
            self.assertEqual(urls, {'https://a/'})
            # Cache headers normalised to empty string.
            self.assertEqual(active[0][2], '')
            self.assertEqual(active[0][3], '')


class UpdateAfterFetchTests(unittest.TestCase):
    def _setup_two(self, db_path: Path) -> tuple[int, int]:
        db_utils.db_insert_rss_feeds(
            [('https://a/', 'https://a/'), ('https://b/', 'https://b/')],
            db_path,
        )
        with sqlite3.connect(db_path) as conn:
            ids = [row[0] for row in conn.execute(
                'SELECT feed_id FROM rss_feeds ORDER BY feed_id').fetchall()]
        return ids[0], ids[1]

    def test_success_resets_counter_and_records_cache_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            fid_a, _ = self._setup_two(db_path)
            # Pre-set a failure count.
            with sqlite3.connect(db_path) as conn:
                conn.execute('UPDATE rss_feeds SET consecutive_failures = 5 '
                             'WHERE feed_id = ?', (fid_a,))
                conn.commit()

            disabled = db_utils.db_update_rss_feed_after_fetch(
                [{'feed_id': fid_a, 'status': 200, 'etag': 'e1',
                  'last_modified': 'lm1', 'error': None}],
                db_path, 10,
            )

            self.assertEqual(disabled, 0)
            row = _row_by_id(db_path, fid_a)
            self.assertEqual(row['consecutive_failures'], 0)
            self.assertEqual(row['etag'], 'e1')
            self.assertEqual(row['last_modified'], 'lm1')
            self.assertEqual(row['last_status'], 200)
            self.assertIsNone(row['last_error'])
            self.assertIsNotNone(row['last_success_at'])

    def test_304_counts_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            fid_a, _ = self._setup_two(db_path)
            db_utils.db_update_rss_feed_after_fetch(
                [{'feed_id': fid_a, 'status': 304, 'etag': 'e2',
                  'last_modified': 'lm2', 'error': None}],
                db_path, 10,
            )
            row = _row_by_id(db_path, fid_a)
            self.assertEqual(row['last_status'], 304)
            self.assertEqual(row['consecutive_failures'], 0)

    def test_failure_increments_counter_and_records_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            fid_a, _ = self._setup_two(db_path)

            db_utils.db_update_rss_feed_after_fetch(
                [{'feed_id': fid_a, 'status': 500, 'etag': '',
                  'last_modified': '', 'error': 'HTTP 500'}],
                db_path, 10,
            )
            row = _row_by_id(db_path, fid_a)
            self.assertEqual(row['consecutive_failures'], 1)
            self.assertEqual(row['last_status'], 500)
            self.assertEqual(row['last_error'], 'HTTP 500')
            self.assertEqual(row['enabled'], 1)

    def test_auto_disables_when_threshold_reached(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            fid_a, _ = self._setup_two(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute('UPDATE rss_feeds SET consecutive_failures = 9 '
                             'WHERE feed_id = ?', (fid_a,))
                conn.commit()

            disabled = db_utils.db_update_rss_feed_after_fetch(
                [{'feed_id': fid_a, 'status': 0, 'etag': '',
                  'last_modified': '', 'error': 'ConnectionError'}],
                db_path, 10,
            )

            self.assertEqual(disabled, 1)
            row = _row_by_id(db_path, fid_a)
            self.assertEqual(row['enabled'], 0)
            self.assertEqual(row['consecutive_failures'], 10)

    def test_auto_disable_threshold_zero_means_never(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            fid_a, _ = self._setup_two(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute('UPDATE rss_feeds SET consecutive_failures = 999 '
                             'WHERE feed_id = ?', (fid_a,))
                conn.commit()

            disabled = db_utils.db_update_rss_feed_after_fetch(
                [{'feed_id': fid_a, 'status': 500, 'etag': '',
                  'last_modified': '', 'error': 'HTTP 500'}],
                db_path, 0,
            )

            self.assertEqual(disabled, 0)
            row = _row_by_id(db_path, fid_a)
            self.assertEqual(row['enabled'], 1)


class GetAllRssFeedsTests(unittest.TestCase):
    def test_returns_dicts_with_canonical_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = _fresh_db(Path(tmp))
            db_utils.db_insert_rss_feeds(
                [('https://a/', 'https://a/')], db_path)
            rows = db_utils.db_get_all_rss_feeds(db_path)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            for key in ('feed_id', 'feed_url', 'normalized_url', 'enabled',
                        'added_at', 'deleted_at', 'last_fetched_at',
                        'last_success_at', 'last_status', 'last_error',
                        'consecutive_failures', 'etag', 'last_modified',
                        'latest_entry_at'):
                self.assertIn(key, row)
            self.assertEqual(row['feed_url'], 'https://a/')


if __name__ == '__main__':
    unittest.main()

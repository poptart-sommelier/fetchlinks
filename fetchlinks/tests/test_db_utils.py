import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db_setup
import db_utils
from utils import Post


def _make_post(unique_id, urls):
    p = Post()
    p.source = 'https://example.com'
    p.author = 'a'
    p.description = 'd'
    p.direct_link = 'https://example.com/post'
    p.date_created = '2026-01-01 00:00:00'
    for u in urls:
        p.add_url(u)
    p.unique_id_string = unique_id
    return p


class _TmpDbCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_dir = Path(self._tmp.name) / 'db'
        self.db_name = 'fetchlinks.db'
        db_setup.db_initial_setup(str(self.db_dir), self.db_name)
        self.db_path = self.db_dir / self.db_name

    def tearDown(self):
        self._tmp.cleanup()


class DbInsertTests(_TmpDbCase):
    def test_returns_zero_for_empty_input(self):
        self.assertEqual(db_utils.db_insert([], self.db_path), 0)

    def test_inserts_post_and_url_rows(self):
        post = _make_post('uid-1', ['https://example.com/a', 'https://example.com/b'])

        inserted = db_utils.db_insert([post], self.db_path)

        self.assertEqual(inserted, 1)
        with sqlite3.connect(self.db_path) as conn:
            posts = conn.execute('SELECT unique_id_string FROM posts').fetchall()
            urls = conn.execute('SELECT url, position FROM post_urls ORDER BY position').fetchall()
        self.assertEqual(posts, [('uid-1',)])
        self.assertEqual(urls, [('https://example.com/a', 0), ('https://example.com/b', 1)])

    def test_duplicate_unique_id_skips_post_and_urls(self):
        first = _make_post('uid-1', ['https://example.com/a'])
        # Same unique_id_string but different URLs — must not re-insert URL rows.
        dup = _make_post('uid-1', ['https://example.com/different'])

        inserted_first = db_utils.db_insert([first], self.db_path)
        inserted_dup = db_utils.db_insert([dup], self.db_path)

        self.assertEqual(inserted_first, 1)
        self.assertEqual(inserted_dup, 0)
        with sqlite3.connect(self.db_path) as conn:
            urls = [row[0] for row in conn.execute('SELECT url FROM post_urls')]
        self.assertEqual(urls, ['https://example.com/a'])

    def test_sqlite_error_wrapped_in_runtime_error(self):
        post = _make_post('uid-1', ['https://example.com/a'])
        with patch('db_utils.sqlite3.connect', side_effect=sqlite3.Error('boom')):
            with self.assertRaises(RuntimeError):
                db_utils.db_insert([post], self.db_path)

    def test_post_with_no_urls_still_inserts_post_row(self):
        post = _make_post('uid-1', [])
        inserted = db_utils.db_insert([post], self.db_path)
        self.assertEqual(inserted, 1)
        with sqlite3.connect(self.db_path) as conn:
            url_count = conn.execute('SELECT COUNT(*) FROM post_urls').fetchone()[0]
        self.assertEqual(url_count, 0)


class BlueskyCursorTests(_TmpDbCase):
    def test_get_returns_none_on_empty(self):
        self.assertIsNone(db_utils.db_get_bluesky_cursor(self.db_path))

    def test_set_and_get_round_trip(self):
        db_utils.db_set_bluesky_cursor('abc', self.db_path)
        self.assertEqual(db_utils.db_get_bluesky_cursor(self.db_path), 'abc')

    def test_set_upserts_single_row(self):
        db_utils.db_set_bluesky_cursor('one', self.db_path)
        db_utils.db_set_bluesky_cursor('two', self.db_path)
        db_utils.db_set_bluesky_cursor('three', self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT idx, cursor FROM bluesky_state').fetchall()

        self.assertEqual(rows, [(1, 'three')])

    def test_empty_cursor_treated_as_none(self):
        db_utils.db_set_bluesky_cursor('', self.db_path)
        self.assertIsNone(db_utils.db_get_bluesky_cursor(self.db_path))

    def test_cleans_up_legacy_rows(self):
        # Simulate legacy multi-row state.
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                'INSERT INTO bluesky_state (idx, cursor, time_created) VALUES (?, ?, ?)',
                [(2, 'old1', 't'), (3, 'old2', 't')],
            )
            conn.commit()

        db_utils.db_set_bluesky_cursor('new', self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT idx, cursor FROM bluesky_state ORDER BY idx').fetchall()
        self.assertEqual(rows, [(1, 'new')])


class RssFeedStateTests(_TmpDbCase):
    def test_get_returns_empty_dict_on_empty_table(self):
        self.assertEqual(db_utils.db_get_rss_feed_states(self.db_path), {})

    def test_set_then_get_round_trip(self):
        rows = [
            ('https://feed1.example/', 'etag1', 'lm1', 200),
            ('https://feed2.example/', '', 'lm2', 304),
        ]
        db_utils.db_set_rss_feed_states(rows, self.db_path)

        states = db_utils.db_get_rss_feed_states(self.db_path)
        self.assertEqual(states['https://feed1.example/'], ('etag1', 'lm1'))
        self.assertEqual(states['https://feed2.example/'], ('', 'lm2'))

    def test_set_upserts_existing_row(self):
        db_utils.db_set_rss_feed_states([('https://f.example/', 'e1', 'lm1', 200)], self.db_path)
        db_utils.db_set_rss_feed_states([('https://f.example/', 'e2', 'lm2', 200)], self.db_path)

        states = db_utils.db_get_rss_feed_states(self.db_path)
        self.assertEqual(states, {'https://f.example/': ('e2', 'lm2')})

    def test_set_with_empty_list_is_noop(self):
        db_utils.db_set_rss_feed_states([], self.db_path)  # must not raise
        self.assertEqual(db_utils.db_get_rss_feed_states(self.db_path), {})


class MastodonStateTests(_TmpDbCase):
    def test_get_returns_none_on_empty(self):
        self.assertIsNone(db_utils.db_get_mastodon_last_seen_id('infosec', self.db_path))

    def test_set_and_get_round_trip(self):
        db_utils.db_set_mastodon_last_seen_id('infosec', 'https://infosec.exchange', '123', self.db_path)
        self.assertEqual(db_utils.db_get_mastodon_last_seen_id('infosec', self.db_path), '123')

    def test_state_is_keyed_by_source_name(self):
        db_utils.db_set_mastodon_last_seen_id('infosec', 'https://infosec.exchange', '123', self.db_path)
        db_utils.db_set_mastodon_last_seen_id('hachyderm', 'https://hachyderm.io', '456', self.db_path)

        self.assertEqual(db_utils.db_get_mastodon_last_seen_id('infosec', self.db_path), '123')
        self.assertEqual(db_utils.db_get_mastodon_last_seen_id('hachyderm', self.db_path), '456')

    def test_set_upserts_existing_row(self):
        db_utils.db_set_mastodon_last_seen_id('infosec', 'https://infosec.exchange', '123', self.db_path)
        db_utils.db_set_mastodon_last_seen_id('infosec', 'https://infosec.exchange', '789', self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT source_name, instance_url, last_seen_id FROM mastodon_state').fetchall()

        self.assertEqual(rows, [('infosec', 'https://infosec.exchange', '789')])


if __name__ == '__main__':
    unittest.main()

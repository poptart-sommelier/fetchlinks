import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_setup


class TablesAndSchemaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_dir = Path(self._tmp.name) / 'db'
        self.db_name = 'fetchlinks.db'
        db_setup.db_initial_setup(str(self.db_dir), self.db_name)
        self.db_path = self.db_dir / self.db_name

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_expected_tables_created(self):
        with sqlite3.connect(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for expected in ('posts', 'post_urls', 'bluesky_state', 'rss_feed_state'):
            self.assertIn(expected, tables)

    def test_unique_id_string_constraint(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO posts (source, author, description, direct_link, '
                'date_created, unique_id_string) VALUES (?, ?, ?, ?, ?, ?)',
                ('s', 'a', 'd', 'dl', 't', 'uid-1'),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    'INSERT INTO posts (source, author, description, direct_link, '
                    'date_created, unique_id_string) VALUES (?, ?, ?, ?, ?, ?)',
                    ('s', 'a', 'd', 'dl', 't', 'uid-1'),
                )

    def test_post_urls_foreign_key_enforced(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    'INSERT INTO post_urls (post_id, position, url, url_hash) '
                    'VALUES (?, ?, ?, ?)',
                    (9999, 0, 'https://example.com', 'h'),
                )

    def test_post_urls_cascade_on_post_delete(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO posts (source, author, description, direct_link, '
                'date_created, unique_id_string) VALUES (?, ?, ?, ?, ?, ?)',
                ('s', 'a', 'd', 'dl', 't', 'uid-1'),
            )
            post_id = cur.lastrowid
            cur.execute(
                'INSERT INTO post_urls (post_id, position, url, url_hash) '
                'VALUES (?, ?, ?, ?)',
                (post_id, 0, 'https://example.com', 'h'),
            )
            cur.execute('DELETE FROM posts WHERE idx = ?', (post_id,))
            count = cur.execute('SELECT COUNT(*) FROM post_urls').fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()

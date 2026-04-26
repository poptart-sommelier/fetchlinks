import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_setup
import export_links


class _ExportCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / 'db' / 'fetchlinks.db'
        db_setup.db_initial_setup(str(self.db_path.parent), self.db_path.name)
        self.out_path = self.tmp / 'out' / 'links.txt'

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, rows):
        # rows is list of (url, unshortened_url|None)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO posts (source, author, description, direct_link, '
                'date_created, unique_id_string) VALUES (?, ?, ?, ?, ?, ?)',
                ('s', 'a', 'd', 'dl', '2026-01-01 00:00:00', 'uid-1'),
            )
            post_id = cur.lastrowid
            for i, (url, unshort) in enumerate(rows):
                cur.execute(
                    'INSERT INTO post_urls (post_id, position, url, url_hash, unshortened_url) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (post_id, i, url, f'hash{i}', unshort),
                )
            conn.commit()


class ResolveDbPathTests(unittest.TestCase):
    def test_absolute_db_location_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            cfg.write_text(json.dumps({
                'db_info': {'db_name': 'x.db', 'db_location': '/abs/dir'}
            }), encoding='utf-8')
            self.assertEqual(export_links.resolve_db_path(cfg), Path('/abs/dir/x.db'))

    def test_relative_db_location_anchored_to_script_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'config.json'
            cfg.write_text(json.dumps({
                'db_info': {'db_name': 'x.db', 'db_location': 'db/'}
            }), encoding='utf-8')
            resolved = export_links.resolve_db_path(cfg)
            self.assertTrue(resolved.is_absolute())
            # Ends with db/x.db under the export_links script dir.
            self.assertEqual(resolved.name, 'x.db')


class ExportLinksTests(_ExportCase):
    def test_missing_db_raises(self):
        with self.assertRaises(FileNotFoundError):
            export_links.export_links(self.tmp / 'no.db', self.out_path, None)

    def test_writes_sorted_links_one_per_line(self):
        self._seed([
            ('https://b.example/', None),
            ('https://a.example/', None),
        ])

        count = export_links.export_links(self.db_path, self.out_path, None)

        self.assertEqual(count, 2)
        lines = self.out_path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines, ['https://a.example/', 'https://b.example/'])

    def test_prefers_unshortened_url_when_present(self):
        self._seed([
            ('https://t.co/abc', 'https://target.example/article'),
            ('https://example.com/', ''),  # empty -> falls back to url
        ])

        export_links.export_links(self.db_path, self.out_path, None)

        lines = self.out_path.read_text(encoding='utf-8').splitlines()
        self.assertIn('https://target.example/article', lines)
        self.assertIn('https://example.com/', lines)
        self.assertNotIn('https://t.co/abc', lines)

    def test_respects_limit(self):
        self._seed([(f'https://example{i}.com/', None) for i in range(5)])

        count = export_links.export_links(self.db_path, self.out_path, limit=2)

        self.assertEqual(count, 2)
        self.assertEqual(len(self.out_path.read_text(encoding='utf-8').splitlines()), 2)


if __name__ == '__main__':
    unittest.main()

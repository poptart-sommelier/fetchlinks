"""Tests for export_links against PostgreSQL."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import export_links
from tests.pg_support import PostgresTestCase


class ExportLinksTests(PostgresTestCase):
    def _post(self, unique_id: str, urls, unshortened=None) -> None:
        """Insert one post with its URLs. ``unshortened`` maps url -> value."""
        unshortened = unshortened or {}
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO content.posts (unique_id, source_type, posted_at) '
                "VALUES (%s, 'rss', now()) RETURNING post_id",
                (unique_id,),
            )
            post_id = cur.fetchone()[0]
            for position, url in enumerate(urls):
                cur.execute(
                    'INSERT INTO content.post_urls '
                    '(post_id, position, url, url_hash, unshortened_url) '
                    'VALUES (%s, %s, %s, %s, %s)',
                    (post_id, position, url, f'hash-{unique_id}-{position}',
                     unshortened.get(url)),
                )
        self.conn.commit()

    def test_writes_sorted_links_one_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._post('p1', ['https://Zebra.example/', 'https://alpha.example/'])
            self._post('p2', ['https://middle.example/'])
            out = Path(tmp) / 'links.txt'

            count = export_links.export_links(self.conn, out, None)

            self.assertEqual(count, 3)
            self.assertEqual(
                out.read_text(encoding='utf-8').splitlines(),
                ['https://alpha.example/', 'https://middle.example/',
                 'https://Zebra.example/'],
            )

    def test_prefers_unshortened_url_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._post(
                'p1', ['https://t.co/abc'],
                unshortened={'https://t.co/abc': 'https://real.example/story'},
            )
            out = Path(tmp) / 'links.txt'

            export_links.export_links(self.conn, out, None)

            self.assertEqual(out.read_text(encoding='utf-8').strip(),
                             'https://real.example/story')

    def test_empty_unshortened_url_falls_back_to_the_collected_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._post('p1', ['https://real.example/'],
                       unshortened={'https://real.example/': ''})
            out = Path(tmp) / 'links.txt'

            export_links.export_links(self.conn, out, None)

            self.assertEqual(out.read_text(encoding='utf-8').strip(),
                             'https://real.example/')

    def test_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._post('p1', ['https://a.example/', 'https://b.example/',
                              'https://c.example/'])
            out = Path(tmp) / 'links.txt'

            count = export_links.export_links(self.conn, out, 2)

            self.assertEqual(count, 2)
            self.assertEqual(len(out.read_text(encoding='utf-8').splitlines()), 2)

    def test_negative_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                export_links.export_links(self.conn, Path(tmp) / 'links.txt', -1)

    def test_creates_the_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._post('p1', ['https://a.example/'])
            out = Path(tmp) / 'nested' / 'deeper' / 'links.txt'

            export_links.export_links(self.conn, out, None)

            self.assertTrue(out.exists())


class MainTests(PostgresTestCase):
    def test_main_writes_file_and_prints_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'links.txt'

            exit_code = export_links.main(
                ['--out', str(out), '--database-url', self.database_url]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(out.read_text(encoding='utf-8'), '')


if __name__ == '__main__':
    unittest.main()

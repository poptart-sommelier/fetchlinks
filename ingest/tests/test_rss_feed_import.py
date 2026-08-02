from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import rss_feed_import as importer
from tests.pg_support import PostgresTestCase


def _rss_feed(
        item_date: str | None = 'Sat, 01 May 2026 12:00:00 GMT',
        title: str = 'Example Feed',
        feed_link: str = 'https://example.com/',
        item_link: str = 'https://example.com/post',
        item_title: str = 'Example item',
) -> bytes:
    date_xml = f'<pubDate>{item_date}</pubDate>' if item_date else ''
    return f'''<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
        <title>{title}</title>
        <link>{feed_link}</link>
    <item>
            <title>{item_title}</title>
            <link>{item_link}</link>
      {date_xml}
    </item>
  </channel>
</rss>'''.encode('utf-8')


def _write_feeds_file(path: Path, feeds=None):
    lines = ['# managed by tests\n']
    for feed in feeds or []:
        lines.append(f'{feed}\n')
    path.write_text(''.join(lines), encoding='utf-8')


def _quiet_call(func, *args, **kwargs):
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        return func(*args, **kwargs)


class FakeResponse:
    def __init__(self, url='https://example.com/feed.xml', status_code=200, content=b'', text=None):
        self.url = url
        self.status_code = status_code
        self.content = content
        self.text = text if text is not None else content.decode('utf-8', errors='ignore')


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, _url, timeout):
        return self.responses.pop(0)


class ExtractUrlTests(unittest.TestCase):
    def test_extract_urls_from_messy_blob_and_dedupes(self):
        text = '''Feeds:
        https://example.com/feed.xml,
        (https://example.com/feed.xml)
        https://other.example/rss?format=xml.
        '''

        self.assertEqual(
            importer.extract_urls(text),
            ['https://example.com/feed.xml', 'https://other.example/rss?format=xml'],
        )

    def test_dedupe_against_existing(self):
        candidates = [
            'https://example.com/feed.xml',
            'https://new.example/rss',
            'https://new.example/rss#fragment',
        ]
        existing = ['https://EXAMPLE.com/feed.xml']

        new_candidates, already_present, duplicate_in_input = importer.dedupe_against_existing(candidates, existing)

        self.assertEqual(new_candidates, ['https://new.example/rss'])
        self.assertEqual(already_present, ['https://example.com/feed.xml'])
        self.assertEqual(duplicate_in_input, ['https://new.example/rss#fragment'])

    def test_feed_site_key_strips_www(self):
        self.assertEqual(
            importer.feed_site_key('https://www.trustedsec.com/feed/'),
            importer.feed_site_key('https://trustedsec.com/feed.rss'),
        )


class FeedCheckTests(unittest.TestCase):
    def test_check_feed_classifies_recent_feed_as_active(self):
        session = FakeSession([FakeResponse(content=_rss_feed('Sat, 01 May 2026 12:00:00 GMT'))])

        check = importer.check_feed(
            'https://example.com/feed.xml',
            session,
            abandoned_days=365,
            now=datetime(2026, 5, 2, tzinfo=UTC),
        )

        self.assertEqual(check.status, 'active')
        self.assertEqual(check.title, 'Example Feed')
        self.assertEqual(check.entry_count, 1)

    def test_check_feed_classifies_old_feed_as_abandoned(self):
        session = FakeSession([FakeResponse(content=_rss_feed('Sat, 01 May 2021 12:00:00 GMT'))])

        check = importer.check_feed(
            'https://example.com/feed.xml',
            session,
            abandoned_days=365,
            now=datetime(2026, 5, 2, tzinfo=UTC),
        )

        self.assertEqual(check.status, 'abandoned')

    def test_check_feed_classifies_missing_dates_as_unknown(self):
        session = FakeSession([FakeResponse(content=_rss_feed(None))])

        check = importer.check_feed(
            'https://example.com/feed.xml',
            session,
            abandoned_days=365,
            now=datetime(2026, 5, 2, tzinfo=UTC),
        )

        self.assertEqual(check.status, 'unknown_date')

    def test_check_feed_classifies_http_error_as_dead(self):
        session = FakeSession([FakeResponse(status_code=404, content=b'nope')])

        check = importer.check_feed('https://example.com/feed.xml', session)

        self.assertEqual(check.status, 'dead')
        self.assertEqual(check.reason, 'HTTP 404')

    def test_discovers_feed_from_html_alternate_link(self):
        html = b'''<html><head>
            <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        </head></html>'''
        session = FakeSession([
            FakeResponse(url='https://example.com/blog', content=html),
            FakeResponse(url='https://example.com/feed.xml', content=_rss_feed('Sat, 01 May 2026 12:00:00 GMT')),
        ])

        check = importer.check_feed(
            'https://example.com/blog',
            session,
            abandoned_days=365,
            now=datetime(2026, 5, 2, tzinfo=UTC),
        )

        self.assertEqual(check.status, 'active')
        self.assertEqual(check.feed_url, 'https://example.com/feed.xml')

    def test_checks_are_duplicate_when_feed_link_matches(self):
        candidate = importer.FeedCheck(
            input_url='https://trustedsec.com/feed.rss',
            feed_url='https://trustedsec.com/feed.rss',
            final_url='https://trustedsec.com/feed.rss',
            status='active',
            title='TrustedSec Blog',
            feed_link='https://trustedsec.com/blog',
            entry_links=('https://trustedsec.com/blog/a',),
        )
        existing = importer.FeedCheck(
            input_url='https://www.trustedsec.com/feed/',
            feed_url='https://www.trustedsec.com/feed/',
            final_url='https://www.trustedsec.com/feed/',
            status='active',
            title='TrustedSec',
            feed_link='https://trustedsec.com/blog',
            entry_links=('https://trustedsec.com/blog/b',),
        )

        is_duplicate, reason = importer.checks_are_duplicate_feed(candidate, existing)

        self.assertTrue(is_duplicate)
        self.assertIn('feed link matches existing', reason)

    def test_checks_are_duplicate_when_recent_entry_links_overlap(self):
        candidate = importer.FeedCheck(
            input_url='https://trustedsec.com/feed.rss',
            feed_url='https://trustedsec.com/feed.rss',
            final_url='https://trustedsec.com/feed.rss',
            status='active',
            entry_links=(
                'https://trustedsec.com/blog/a',
                'https://trustedsec.com/blog/b',
                'https://trustedsec.com/blog/c',
            ),
        )
        existing = importer.FeedCheck(
            input_url='https://www.trustedsec.com/feed/',
            feed_url='https://www.trustedsec.com/feed/',
            final_url='https://www.trustedsec.com/feed/',
            status='active',
            entry_links=(
                'https://www.trustedsec.com/blog/a',
                'https://www.trustedsec.com/blog/b',
                'https://www.trustedsec.com/blog/c',
            ),
        )

        is_duplicate, reason = importer.checks_are_duplicate_feed(candidate, existing)

        self.assertTrue(is_duplicate)
        self.assertIn('entry link', reason)

    def test_checks_allow_distinct_same_site_feeds(self):
        candidate = importer.FeedCheck(
            input_url='https://example.com/security.rss',
            feed_url='https://example.com/security.rss',
            final_url='https://example.com/security.rss',
            status='active',
            title='Example Security',
            feed_link='https://example.com/security',
            entry_links=('https://example.com/security/a',),
            entry_titles=('security story',),
        )
        existing = importer.FeedCheck(
            input_url='https://example.com/news.rss',
            feed_url='https://example.com/news.rss',
            final_url='https://example.com/news.rss',
            status='active',
            title='Example News',
            feed_link='https://example.com/news',
            entry_links=('https://example.com/news/a',),
            entry_titles=('news story',),
        )

        is_duplicate, _reason = importer.checks_are_duplicate_feed(candidate, existing)

        self.assertFalse(is_duplicate)




class ImportWorkflowTests(PostgresTestCase):
    """The catalog-writing half of the importer, against a real database."""

    def _seed(self, urls: list[str]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                'INSERT INTO catalog.rss_feeds (feed_url, normalized_url) VALUES (%s, %s)',
                [(u, importer.normalize_feed_url(u)) for u in urls],
            )
        self.conn.commit()

    def _active_check(self, url: str) -> "importer.FeedCheck":
        return importer.FeedCheck(
            input_url=url,
            feed_url=url,
            final_url=url,
            status='active',
            latest_entry=datetime(2026, 5, 1, tzinfo=UTC),
        )

    def test_dry_run_writes_pruned_without_modifying_the_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / 'rss-list.txt'
            input_path.write_text('https://new.example/feed.xml\n', encoding='utf-8')
            self._seed(['https://existing.example/rss'])
            checks = [self._active_check('https://new.example/feed.xml')]

            with patch.object(importer, 'check_candidates', return_value=checks):
                added = _quiet_call(importer.import_from_input, input_path,
                                    self.conn, dry_run=True, abandoned_days=365)

            self.assertEqual(added, 0)
            self.assertEqual(self.count('catalog.rss_feeds'), 1)
            self.assertEqual(
                (tmp_path / 'rss-list.txt.pruned').read_text(encoding='utf-8'),
                'https://new.example/feed.xml\n',
            )

    def test_dry_run_excludes_same_site_duplicate_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / 'rss-list.txt'
            input_path.write_text('https://trustedsec.com/feed.rss\n', encoding='utf-8')
            self._seed(['https://www.trustedsec.com/feed/'])
            candidate_check = importer.FeedCheck(
                input_url='https://trustedsec.com/feed.rss',
                feed_url='https://trustedsec.com/feed.rss',
                final_url='https://trustedsec.com/feed.rss',
                status='active',
                feed_link='https://trustedsec.com/blog',
                entry_links=('https://trustedsec.com/blog/a',),
                latest_entry=datetime(2026, 5, 1, tzinfo=UTC),
            )
            existing_check = importer.FeedCheck(
                input_url='https://www.trustedsec.com/feed/',
                feed_url='https://www.trustedsec.com/feed/',
                final_url='https://www.trustedsec.com/feed/',
                status='active',
                feed_link='https://trustedsec.com/blog',
                entry_links=('https://trustedsec.com/blog/b',),
                latest_entry=datetime(2026, 5, 1, tzinfo=UTC),
            )

            with patch.object(importer, 'check_candidates', return_value=[candidate_check]), \
                 patch.object(importer, 'check_feed', return_value=existing_check):
                added = _quiet_call(importer.import_from_input, input_path,
                                    self.conn, dry_run=True, abandoned_days=365)

            self.assertEqual(added, 0)
            self.assertEqual(
                (tmp_path / 'rss-list.txt.pruned').read_text(encoding='utf-8'), '')

    def test_default_input_mode_inserts_into_the_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'rss-list.txt'
            input_path.write_text('https://new.example/feed.xml\n', encoding='utf-8')
            self._seed(['https://existing.example/rss'])
            checks = [self._active_check('https://new.example/feed.xml')]

            with patch.object(importer, 'check_candidates', return_value=checks):
                added = _quiet_call(importer.import_from_input, input_path,
                                    self.conn, dry_run=False, abandoned_days=365)

            self.assertEqual(added, 1)
            feeds = importer.load_existing_feeds_from_db(self.conn)
            self.assertIn('https://new.example/feed.xml', feeds)
            self.assertIn('https://existing.example/rss', feeds)

    def test_pruned_mode_applies_without_network_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            pruned_path = Path(tmp) / 'rss-list.txt.pruned'
            pruned_path.write_text('https://new.example/feed.xml\n', encoding='utf-8')
            self._seed(['https://existing.example/rss'])

            with patch.object(importer, 'check_candidates') as check_candidates:
                added = _quiet_call(importer.import_from_pruned, pruned_path,
                                    self.conn, dry_run=False)

            self.assertEqual(added, 1)
            self.assertIn('https://new.example/feed.xml',
                          importer.load_existing_feeds_from_db(self.conn))
            check_candidates.assert_not_called()

    def test_existing_feed_is_not_inserted_twice(self):
        self._seed(['https://existing.example/rss'])

        added = importer.insert_feeds_into_db(
            self.conn, ['HTTPS://Existing.example/rss'])

        self.assertEqual(added, 0)
        self.assertEqual(self.count('catalog.rss_feeds'), 1)

    def test_a_removed_feed_is_not_resurrected(self):
        """Re-adding what an admin deleted must be an explicit restore."""
        self._seed(['https://gone.example/rss'])
        with self.conn.cursor() as cur:
            cur.execute('UPDATE catalog.rss_feeds SET deleted_at = now(), enabled = false')
        self.conn.commit()

        added = importer.insert_feeds_into_db(self.conn, ['https://gone.example/rss'])

        self.assertEqual(added, 0)
        self.assertIsNotNone(self.scalar('SELECT deleted_at FROM catalog.rss_feeds'))
        self.assertFalse(self.scalar('SELECT enabled FROM catalog.rss_feeds'))

    def test_duplicates_within_one_batch_are_collapsed(self):
        added = importer.insert_feeds_into_db(
            self.conn,
            ['https://a.example/rss', 'HTTPS://A.example/rss', 'https://b.example/rss'],
        )

        self.assertEqual(added, 2)
        self.assertEqual(self.count('catalog.rss_feeds'), 2)


class ParseArgsTests(unittest.TestCase):
    def test_rejects_abandoned_days_with_pruned(self):
        with self.assertRaises(SystemExit):
            _quiet_call(importer.parse_args,
                        ['--pruned', '/tmp/rss-list.txt.pruned', '--abandoned-days', '30'])

    def test_requires_an_input_mode(self):
        with self.assertRaises(SystemExit):
            _quiet_call(importer.parse_args, [])

    def test_input_and_pruned_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            _quiet_call(importer.parse_args,
                        ['--input', '/tmp/a.txt', '--pruned', '/tmp/b.txt'])


if __name__ == '__main__':
    unittest.main()

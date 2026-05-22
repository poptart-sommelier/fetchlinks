from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import rss_links
from utils import Post


class _FakeResponse:
    def __init__(self, status_code=200, content=b'', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class _FeedEntry(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _atom_bytes(link='https://example.com/post', title='entry'):
    return (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<title>F</title>'
        '<link href="https://example.com/"/>'
        '<id>tag:example.com,2026:/</id>'
        '<updated>2026-04-19T12:00:00Z</updated>'
        f'<entry><title>{title}</title>'
        f'<link href="{link}"/>'
        '<id>tag:example.com,2026:/1</id>'
        '<updated>2026-04-19T12:00:00Z</updated>'
        '<published>2026-04-19T12:00:00Z</published>'
        '</entry></feed>'
    ).encode()


def _rss_source(**overrides):
    """Build a minimal config.RssSource-like namespace for run() tests."""
    defaults = dict(
        enabled=True,
        seed_file=None,
        export_path=None,
        auto_disable_after_failures=10,
        request_timeout_seconds=10,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FetchOneTests(unittest.TestCase):
    def test_sends_conditional_headers_when_cached(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(304, headers={'ETag': 'e2', 'Last-Modified': 'lm2'})

        result = rss_links._fetch_one(session, 1, 'https://feed/', 'etag-1', 'lm-1', 10)

        _args, kwargs = session.get.call_args
        headers = kwargs['headers']
        self.assertEqual(headers['If-None-Match'], 'etag-1')
        self.assertEqual(headers['If-Modified-Since'], 'lm-1')
        self.assertEqual(result, (1, 'https://feed/', None, 'e2', 'lm2', 304, None))

    def test_no_conditional_headers_when_no_cache(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=_atom_bytes())

        rss_links._fetch_one(session, 1, 'https://feed/', '', '', 10)

        headers = session.get.call_args.kwargs['headers']
        self.assertNotIn('If-None-Match', headers)
        self.assertNotIn('If-Modified-Since', headers)

    def test_request_exception_preserves_cached_state(self):
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError('boom')

        result = rss_links._fetch_one(session, 7, 'https://feed/', 'etag-1', 'lm-1', 10)

        self.assertEqual(result[0], 7)
        self.assertEqual(result[1], 'https://feed/')
        self.assertIsNone(result[2])
        self.assertEqual(result[3], 'etag-1')
        self.assertEqual(result[4], 'lm-1')
        self.assertEqual(result[5], 0)
        self.assertEqual(result[6], 'ConnectionError')

    def test_non_200_non_304_returns_none_feed_with_error(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(500)

        fid, url, feed, etag, lm, status, err = rss_links._fetch_one(
            session, 1, 'https://feed/', '', '', 10)

        self.assertIsNone(feed)
        self.assertEqual(status, 500)
        self.assertEqual(err, 'HTTP 500')

    def test_200_with_valid_feed_returns_parsed_feed(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(
            200,
            content=_atom_bytes(),
            headers={'ETag': 'e1', 'Last-Modified': 'lm1'},
        )

        fid, url, feed, etag, lm, status, err = rss_links._fetch_one(
            session, 1, 'https://feed/', '', '', 10)

        self.assertIsNotNone(feed)
        self.assertEqual(status, 200)
        self.assertEqual(etag, 'e1')
        self.assertEqual(lm, 'lm1')
        self.assertIsNone(err)
        self.assertEqual(len(feed.entries), 1)

    def test_bozo_with_no_entries_returns_none_feed(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=b'<<<not xml>>>')

        fid, url, feed, etag, lm, status, err = rss_links._fetch_one(
            session, 1, 'https://feed/', '', '', 10)

        self.assertIsNone(feed)
        self.assertEqual(status, 200)
        self.assertIn('parse error', err)


class ParsePostsTests(unittest.TestCase):
    def test_skips_none_feeds(self):
        results = [(1, 'https://x/', None, '', '', 304, None)]
        self.assertEqual(rss_links.parse_posts(results), [])

    def test_uses_feed_link_as_source(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=_atom_bytes())
        fetch_results = [rss_links._fetch_one(session, 1, 'https://feedurl/', '', '', 10)]

        posts = rss_links.parse_posts(fetch_results)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].source, 'https://example.com/')
        self.assertEqual(posts[0].urls, ['https://example.com/post'])

    def test_skips_entries_with_no_urls(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=_atom_bytes(link=''))
        fetch_results = [rss_links._fetch_one(session, 1, 'https://feedurl/', '', '', 10)]

        self.assertEqual(rss_links.parse_posts(fetch_results), [])

    def test_skips_malformed_entry_without_stopping_feed(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [object()]
        fetch_results = [(1, 'https://feedurl/', feed, '', '', 200, None)]

        with patch.object(rss_links, 'RssPost', side_effect=RuntimeError('bad entry')):
            self.assertEqual(rss_links.parse_posts(fetch_results), [])

    def test_falls_back_to_feed_url_when_feed_metadata_missing(self):
        feed = MagicMock()
        feed.feed = {}
        feed.entries = [_FeedEntry(
            title='Example post',
            link='https://example.com/post',
            published='2026-04-19T12:00:00Z',
        )]
        fetch_results = [(1, 'https://feedurl.example/rss.xml', feed, '', '', 200, None)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].source, 'https://feedurl.example/rss.xml')
        self.assertEqual(posts[0].author, 'https://feedurl.example/rss.xml')

    def test_parse_posts_preserves_old_entries_for_shared_age_filter(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [_FeedEntry(
            title='Old post',
            link='https://example.com/old',
            published='2026-01-25T12:00:00Z',
        )]
        fetch_results = [(1, 'https://feedurl.example/rss.xml', feed, '', '', 200, None)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].date_created, '2026-01-25 12:00:00')

    def test_parse_posts_uses_updated_date_when_no_published(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [_FeedEntry(
            title='Old post',
            link='https://example.com/old',
            updated='2026-01-25T12:00:00Z',
        )]
        fetch_results = [(1, 'https://feedurl.example/rss.xml', feed, '', '', 200, None)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].date_created, '2026-01-25 12:00:00')

    def test_keeps_entries_within_three_months(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [_FeedEntry(
            title='Recent post',
            link='https://example.com/recent',
            published='2026-01-26T12:00:00Z',
        )]
        fetch_results = [(1, 'https://feedurl.example/rss.xml', feed, '', '', 200, None)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].urls, ['https://example.com/recent'])

    def test_keeps_entries_without_dates(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [_FeedEntry(title='No date', link='https://example.com/no-date')]
        fetch_results = [(1, 'https://feedurl.example/rss.xml', feed, '', '', 200, None)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].urls, ['https://example.com/no-date'])


class FetchFeedsTests(unittest.TestCase):
    def test_fetches_each_feed_once_with_cached_state(self):
        captured = []

        def _fake(session, feed_id, url, etag, lm, timeout):
            captured.append((feed_id, url, etag, lm, timeout))
            return (feed_id, url, None, etag, lm, 304, None)

        with patch.object(rss_links, '_fetch_one', side_effect=_fake):
            feeds = [
                (1, 'https://a/', 'eA', 'lA'),
                (2, 'https://b/', '', ''),
                (3, 'https://c/', '', ''),
            ]
            results = rss_links.fetch_feeds(feeds, 15)

        self.assertEqual({r[0] for r in results}, {1, 2, 3})
        by_id = {row[0]: row for row in captured}
        self.assertEqual(by_id[1], (1, 'https://a/', 'eA', 'lA', 15))
        self.assertEqual(by_id[2], (2, 'https://b/', '', '', 15))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.db_path = Path('/tmp/db/fetchlinks.db')

    def test_run_no_active_feeds_returns_early(self):
        with patch.object(rss_links.db_utils, 'db_get_active_rss_feeds',
                          return_value=[]) as get_active, \
             patch.object(rss_links, 'fetch_feeds') as fetch_feeds, \
             patch.object(rss_links.db_utils,
                          'db_update_rss_feed_after_fetch') as update:
            rss_links.run(_rss_source(), self.db_path)

        get_active.assert_called_once_with(self.db_path)
        fetch_feeds.assert_not_called()
        update.assert_not_called()

    def test_run_persists_health_even_when_no_posts(self):
        active = [
            (1, 'https://a.example/feed.xml', 'old-etag', 'old-lm'),
            (2, 'https://b.example/feed.xml', '', ''),
        ]
        fetch_results = [
            (1, 'https://a.example/feed.xml', None, 'new-etag', 'new-lm', 304, None),
            (2, 'https://b.example/feed.xml', None, '', '', 0, 'ConnectionError'),
        ]

        with patch.object(rss_links.db_utils, 'db_get_active_rss_feeds',
                          return_value=active) as get_active, \
             patch.object(rss_links, 'fetch_feeds',
                          return_value=fetch_results) as fetch_feeds, \
             patch.object(rss_links.db_utils,
                          'db_update_rss_feed_after_fetch',
                          return_value=0) as update, \
             patch.object(rss_links, 'parse_posts', return_value=[]), \
             patch.object(rss_links.db_utils, 'db_insert') as db_insert:
            rss_links.run(_rss_source(request_timeout_seconds=20), self.db_path)

        get_active.assert_called_once_with(self.db_path)
        fetch_feeds.assert_called_once_with(active, 20)
        update.assert_called_once()
        rows, db_path_arg, auto_disable = update.call_args.args
        self.assertEqual(db_path_arg, self.db_path)
        self.assertEqual(auto_disable, 10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['feed_id'], 1)
        self.assertEqual(rows[0]['status'], 304)
        self.assertEqual(rows[1]['error'], 'ConnectionError')
        db_insert.assert_not_called()

    def test_run_inserts_parsed_posts(self):
        active = [(1, 'https://feed.example/rss.xml', '', '')]
        parsed_posts = [object()]

        with patch.object(rss_links.db_utils, 'db_get_active_rss_feeds',
                          return_value=active), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils,
                          'db_update_rss_feed_after_fetch', return_value=0), \
             patch.object(rss_links, 'parse_posts', return_value=parsed_posts), \
             patch.object(rss_links.db_utils, 'db_insert',
                          return_value=1) as db_insert:
            rss_links.run(_rss_source(), self.db_path)

        db_insert.assert_called_once_with(parsed_posts, self.db_path)

    def test_run_filters_old_posts_before_insert(self):
        active = [(1, 'https://feed.example/rss.xml', '', '')]
        old_post = SimpleNamespace(date_created='2000-01-01 00:00:00')
        recent_post = SimpleNamespace(date_created='2999-01-01 00:00:00')

        with patch.object(rss_links.db_utils, 'db_get_active_rss_feeds',
                          return_value=active), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils,
                          'db_update_rss_feed_after_fetch', return_value=0), \
             patch.object(rss_links, 'parse_posts',
                          return_value=[old_post, recent_post]), \
             patch.object(rss_links.db_utils, 'db_insert',
                          return_value=1) as db_insert:
            rss_links.run(_rss_source(), self.db_path, max_post_age_months=3)

        db_insert.assert_called_once_with([recent_post], self.db_path)

    def test_run_filters_denied_host_keywords_before_insert(self):
        active = [(1, 'https://feed.example/rss.xml', '', '')]
        post = Post()
        post.date_created = '2999-01-01 00:00:00'
        post.add_url('https://www.businessinsider.com/story')
        post.add_url('https://example.com/allowed')
        post._generate_unique_url_string()

        with patch.object(rss_links.db_utils, 'db_get_active_rss_feeds',
                          return_value=active), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils,
                          'db_update_rss_feed_after_fetch', return_value=0), \
             patch.object(rss_links, 'parse_posts', return_value=[post]), \
             patch.object(rss_links.db_utils, 'db_insert',
                          return_value=1) as db_insert:
            rss_links.run(_rss_source(), self.db_path,
                          excluded_url_host_keywords=['insider'])

        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/allowed'])

    def test_run_filters_denied_url_or_description_keywords_before_insert(self):
        active = [(1, 'https://feed.example/rss.xml', '', '')]
        blocked = Post()
        blocked.date_created = '2999-01-01 00:00:00'
        blocked.description = 'Politics story'
        blocked.add_url('https://example.com/story')
        blocked._generate_unique_url_string()
        allowed = Post()
        allowed.date_created = '2999-01-01 00:00:00'
        allowed.description = 'Technology story'
        allowed.add_url('https://example.com/allowed')
        allowed._generate_unique_url_string()

        with patch.object(rss_links.db_utils, 'db_get_active_rss_feeds',
                          return_value=active), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils,
                          'db_update_rss_feed_after_fetch', return_value=0), \
             patch.object(rss_links, 'parse_posts',
                          return_value=[blocked, allowed]), \
             patch.object(rss_links.db_utils, 'db_insert',
                          return_value=1) as db_insert:
            rss_links.run(
                _rss_source(), self.db_path,
                excluded_url_or_description_keywords=['politics'],
            )

        db_insert.assert_called_once_with([allowed], self.db_path)


if __name__ == '__main__':
    unittest.main()

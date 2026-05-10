from types import SimpleNamespace
import unittest
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
    # Minimal Atom feed feedparser will parse cleanly.
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


class FetchOneTests(unittest.TestCase):
    def test_sends_conditional_headers_when_cached(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(304, headers={'ETag': 'e2', 'Last-Modified': 'lm2'})

        result = rss_links._fetch_one(session, 'https://feed/', ('etag-1', 'lm-1'))

        # Headers passed in
        _args, kwargs = session.get.call_args
        headers = kwargs['headers']
        self.assertEqual(headers['If-None-Match'], 'etag-1')
        self.assertEqual(headers['If-Modified-Since'], 'lm-1')
        # 304 yields None feed, picks up new etag/lm.
        self.assertEqual(result, ('https://feed/', None, 'e2', 'lm2', 304))

    def test_no_conditional_headers_when_no_cache(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=_atom_bytes())

        rss_links._fetch_one(session, 'https://feed/', ('', ''))

        headers = session.get.call_args.kwargs['headers']
        self.assertNotIn('If-None-Match', headers)
        self.assertNotIn('If-Modified-Since', headers)

    def test_request_exception_preserves_cached_state(self):
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError('boom')

        result = rss_links._fetch_one(session, 'https://feed/', ('etag-1', 'lm-1'))

        self.assertEqual(result, ('https://feed/', None, 'etag-1', 'lm-1', 0))

    def test_non_200_non_304_returns_none_feed(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(500)

        url, feed, etag, lm, status = rss_links._fetch_one(session, 'https://feed/', ('', ''))

        self.assertIsNone(feed)
        self.assertEqual(status, 500)

    def test_200_with_valid_feed_returns_parsed_feed(self):
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(
            200,
            content=_atom_bytes(),
            headers={'ETag': 'e1', 'Last-Modified': 'lm1'},
        )

        url, feed, etag, lm, status = rss_links._fetch_one(session, 'https://feed/', ('', ''))

        self.assertIsNotNone(feed)
        self.assertEqual(status, 200)
        self.assertEqual(etag, 'e1')
        self.assertEqual(lm, 'lm1')
        self.assertEqual(len(feed.entries), 1)

    def test_bozo_with_no_entries_returns_none_feed(self):
        session = MagicMock(spec=requests.Session)
        # Garbage that won't parse.
        session.get.return_value = _FakeResponse(200, content=b'<<<not xml>>>')

        url, feed, etag, lm, status = rss_links._fetch_one(session, 'https://feed/', ('', ''))

        self.assertIsNone(feed)
        self.assertEqual(status, 200)


class ParsePostsTests(unittest.TestCase):
    def test_skips_none_feeds(self):
        results = [('https://x/', None, '', '', 304)]
        self.assertEqual(rss_links.parse_posts(results), [])

    def test_uses_feed_link_as_source(self):
        # Build a single fetch result by reusing _fetch_one with a fake response.
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=_atom_bytes())
        fetch_results = [rss_links._fetch_one(session, 'https://feedurl/', ('', ''))]

        posts = rss_links.parse_posts(fetch_results)
        self.assertEqual(len(posts), 1)
        # Atom feed self-link is https://example.com/, so source should match.
        self.assertEqual(posts[0].source, 'https://example.com/')
        self.assertEqual(posts[0].urls, ['https://example.com/post'])

    def test_skips_entries_with_no_urls(self):
        # Atom entry with empty link
        session = MagicMock(spec=requests.Session)
        session.get.return_value = _FakeResponse(200, content=_atom_bytes(link=''))
        fetch_results = [rss_links._fetch_one(session, 'https://feedurl/', ('', ''))]

        self.assertEqual(rss_links.parse_posts(fetch_results), [])

    def test_skips_malformed_entry_without_stopping_feed(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [object()]
        fetch_results = [('https://feedurl/', feed, '', '', 200)]

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
        fetch_results = [('https://feedurl.example/rss.xml', feed, '', '', 200)]

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
        fetch_results = [('https://feedurl.example/rss.xml', feed, '', '', 200)]

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
        fetch_results = [('https://feedurl.example/rss.xml', feed, '', '', 200)]

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
        fetch_results = [('https://feedurl.example/rss.xml', feed, '', '', 200)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].urls, ['https://example.com/recent'])

    def test_keeps_entries_without_dates(self):
        feed = MagicMock()
        feed.feed = {'link': 'https://example.com/', 'title': 'Example'}
        feed.entries = [_FeedEntry(title='No date', link='https://example.com/no-date')]
        fetch_results = [('https://feedurl.example/rss.xml', feed, '', '', 200)]

        posts = rss_links.parse_posts(fetch_results)

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].urls, ['https://example.com/no-date'])


class FetchFeedsTests(unittest.TestCase):
    def test_fetches_each_url_once(self):
        # Patch _fetch_one to avoid real network and confirm dispatch.
        with patch.object(rss_links, '_fetch_one') as mock_fetch:
            mock_fetch.side_effect = lambda session, url, cached: (url, None, '', '', 304)
            urls = ['https://a/', 'https://b/', 'https://c/']
            results = rss_links.fetch_feeds(urls, {'https://a/': ('e', 'l')})

        self.assertEqual({r[0] for r in results}, set(urls))
        # The cached state for 'a' was forwarded; the others got ('', '').
        called_with = {call.args[1]: call.args[2] for call in mock_fetch.call_args_list}
        self.assertEqual(called_with['https://a/'], ('e', 'l'))
        self.assertEqual(called_with['https://b/'], ('', ''))


class RunTests(unittest.TestCase):
    def test_run_persists_feed_state_even_when_no_posts(self):
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        feed_links = ['https://a.example/feed.xml', 'https://b.example/feed.xml']
        cached_states = {'https://a.example/feed.xml': ('old-etag', 'old-lm')}
        fetch_results = [
            ('https://a.example/feed.xml', None, 'new-etag', 'new-lm', 304),
            ('https://b.example/feed.xml', None, '', '', 0),
        ]

        with patch.object(rss_links.db_utils, 'db_get_rss_feed_states', return_value=cached_states) as get_states, \
             patch.object(rss_links, 'fetch_feeds', return_value=fetch_results) as fetch_feeds, \
             patch.object(rss_links.db_utils, 'db_set_rss_feed_states') as set_states, \
             patch.object(rss_links, 'parse_posts', return_value=[]) as parse_posts, \
             patch.object(rss_links.db_utils, 'db_insert') as db_insert:
            rss_links.run(feed_links, db_info)

        db_path = rss_links.Path('/tmp/db') / 'fetchlinks.db'
        get_states.assert_called_once_with(db_path)
        fetch_feeds.assert_called_once_with(feed_links, cached_states)
        set_states.assert_called_once_with([
            ('https://a.example/feed.xml', 'new-etag', 'new-lm', 304),
            ('https://b.example/feed.xml', '', '', 0),
        ], db_path)
        parse_posts.assert_called_once_with(fetch_results)
        db_insert.assert_not_called()

    def test_run_inserts_parsed_posts(self):
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        parsed_posts = [object()]

        with patch.object(rss_links.db_utils, 'db_get_rss_feed_states', return_value={}), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils, 'db_set_rss_feed_states'), \
             patch.object(rss_links, 'parse_posts', return_value=parsed_posts), \
             patch.object(rss_links.db_utils, 'db_insert', return_value=1) as db_insert:
            rss_links.run(['https://feed.example/rss.xml'], db_info)

        db_insert.assert_called_once_with(parsed_posts, rss_links.Path('/tmp/db') / 'fetchlinks.db')

    def test_run_filters_old_posts_before_insert(self):
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        old_post = SimpleNamespace(date_created='2000-01-01 00:00:00')
        recent_post = SimpleNamespace(date_created='2999-01-01 00:00:00')

        with patch.object(rss_links.db_utils, 'db_get_rss_feed_states', return_value={}), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils, 'db_set_rss_feed_states'), \
             patch.object(rss_links, 'parse_posts', return_value=[old_post, recent_post]), \
             patch.object(rss_links.db_utils, 'db_insert', return_value=1) as db_insert:
            rss_links.run(['https://feed.example/rss.xml'], db_info, max_post_age_months=3)

        db_insert.assert_called_once_with([recent_post], rss_links.Path('/tmp/db') / 'fetchlinks.db')

    def test_run_filters_denied_host_keywords_before_insert(self):
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        post = Post()
        post.date_created = '2999-01-01 00:00:00'
        post.add_url('https://www.businessinsider.com/story')
        post.add_url('https://example.com/allowed')
        post._generate_unique_url_string()

        with patch.object(rss_links.db_utils, 'db_get_rss_feed_states', return_value={}), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils, 'db_set_rss_feed_states'), \
             patch.object(rss_links, 'parse_posts', return_value=[post]), \
             patch.object(rss_links.db_utils, 'db_insert', return_value=1) as db_insert:
            rss_links.run(['https://feed.example/rss.xml'], db_info, excluded_url_host_keywords=['insider'])

        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/allowed'])

    def test_run_filters_denied_url_or_description_keywords_before_insert(self):
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
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

        with patch.object(rss_links.db_utils, 'db_get_rss_feed_states', return_value={}), \
             patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links.db_utils, 'db_set_rss_feed_states'), \
             patch.object(rss_links, 'parse_posts', return_value=[blocked, allowed]), \
             patch.object(rss_links.db_utils, 'db_insert', return_value=1) as db_insert:
            rss_links.run(
                ['https://feed.example/rss.xml'],
                db_info,
                excluded_url_or_description_keywords=['politics'],
            )

        db_insert.assert_called_once_with([allowed], rss_links.Path('/tmp/db') / 'fetchlinks.db')


if __name__ == '__main__':
    unittest.main()

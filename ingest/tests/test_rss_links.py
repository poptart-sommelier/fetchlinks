from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import rss_links
from pipeline.catalog import build_catalog
from pipeline.state import CollectorState
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
    """``run`` collects; it must never reach for a database."""

    @staticmethod
    def _catalog(*pairs):
        return build_catalog(pairs or [('https://feed.example/rss.xml',
                                        'https://feed.example/rss.xml')])

    @staticmethod
    def _post(url, description='', date_created='2999-01-01 00:00:00'):
        post = Post()
        post.source = 'https://feed.example'
        post.source_type = 'rss'
        post.date_created = date_created
        post.description = description
        post.add_url(url)
        post._generate_unique_url_string()
        return post

    def test_run_no_catalogued_feeds_returns_an_empty_result(self):
        with patch.object(rss_links, 'fetch_feeds') as fetch_feeds:
            result = rss_links.run(_rss_source(), build_catalog(), CollectorState())

        fetch_feeds.assert_not_called()
        self.assertTrue(result.is_empty)

    def test_run_passes_cached_validators_from_state(self):
        state = CollectorState()
        state.set_rss_headers('https://a.example/feed.xml', 'old-etag', 'old-lm')
        catalog = self._catalog(
            ('https://a.example/feed.xml', 'https://a.example/feed.xml'),
            ('https://b.example/feed.xml', 'https://b.example/feed.xml'),
        )

        with patch.object(rss_links, 'fetch_feeds', return_value=[]) as fetch_feeds, \
             patch.object(rss_links, 'parse_posts', return_value=[]):
            rss_links.run(_rss_source(request_timeout_seconds=20), catalog, state)

        feeds, timeout = fetch_feeds.call_args.args
        self.assertEqual(timeout, 20)
        self.assertEqual(sorted(feeds), [
            ('https://a.example/feed.xml', 'https://a.example/feed.xml',
             'old-etag', 'old-lm'),
            ('https://b.example/feed.xml', 'https://b.example/feed.xml', None, None),
        ])

    def test_run_observes_every_feed_even_when_no_posts(self):
        catalog = self._catalog(
            ('https://a.example/feed.xml', 'https://a.example/feed.xml'),
            ('https://b.example/feed.xml', 'https://b.example/feed.xml'),
        )
        fetch_results = [
            ('https://a.example/feed.xml', 'https://a.example/feed.xml',
             None, 'new-etag', 'new-lm', 304, None),
            ('https://b.example/feed.xml', 'https://b.example/feed.xml',
             None, '', '', 0, 'ConnectionError'),
        ]

        with patch.object(rss_links, 'fetch_feeds', return_value=fetch_results), \
             patch.object(rss_links, 'parse_posts', return_value=[]):
            result = rss_links.run(_rss_source(), catalog, CollectorState())

        self.assertEqual(result.posts, [])
        by_url = {obs.normalized_url: obs for obs in result.rss_observations}
        self.assertEqual(len(by_url), 2)

        conditional = by_url['https://a.example/feed.xml']
        # 304 confirms the cached copy; it is a success, not a miss.
        self.assertTrue(conditional.success)
        self.assertEqual(conditional.status, 304)
        self.assertEqual(conditional.etag, 'new-etag')

        failed = by_url['https://b.example/feed.xml']
        self.assertFalse(failed.success)
        # A request that never got a response has no status, rather than zero.
        self.assertIsNone(failed.status)
        self.assertEqual(failed.error, 'ConnectionError')
        self.assertIsNone(failed.etag)

    def test_run_carries_no_failure_counter(self):
        """Counters are the publisher's job, so a replay cannot inflate them."""
        fetch_results = [('https://a.example/feed.xml', 'https://a.example/feed.xml',
                          None, '', '', 500, 'HTTP 500')]
        catalog = self._catalog(('https://a.example/feed.xml',
                                 'https://a.example/feed.xml'))

        with patch.object(rss_links, 'fetch_feeds', return_value=fetch_results), \
             patch.object(rss_links, 'parse_posts', return_value=[]):
            result = rss_links.run(_rss_source(), catalog, CollectorState())

        document = result.rss_observations[0].to_dict()
        self.assertNotIn('consecutive_failures', document)

    def test_run_returns_parsed_posts_as_records(self):
        post = self._post('https://example.com/story')

        with patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links, 'parse_posts', return_value=[post]):
            result = rss_links.run(_rss_source(), self._catalog(), CollectorState())

        self.assertEqual(len(result.posts), 1)
        record = result.posts[0]
        self.assertEqual(record.unique_id, post.unique_id_string)
        self.assertEqual(record.urls, ['https://example.com/story'])
        self.assertEqual(record.source_type, 'rss')

    def test_run_filters_old_posts(self):
        old_post = self._post('https://example.com/old', date_created='2000-01-01 00:00:00')
        recent_post = self._post('https://example.com/new')

        with patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links, 'parse_posts',
                          return_value=[old_post, recent_post]):
            result = rss_links.run(_rss_source(), self._catalog(), CollectorState(),
                                   max_post_age_months=3)

        self.assertEqual([record.unique_id for record in result.posts],
                         [recent_post.unique_id_string])

    def test_run_filters_denied_host_keywords(self):
        post = Post()
        post.date_created = '2999-01-01 00:00:00'
        post.add_url('https://www.businessinsider.com/story')
        post.add_url('https://example.com/allowed')
        post._generate_unique_url_string()

        with patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links, 'parse_posts', return_value=[post]):
            result = rss_links.run(_rss_source(), self._catalog(), CollectorState(),
                                   excluded_url_host_keywords=['insider'])

        self.assertEqual(result.posts[0].urls, ['https://example.com/allowed'])

    def test_run_filters_denied_url_or_description_keywords(self):
        blocked = self._post('https://example.com/story', description='Politics story')
        allowed = self._post('https://example.com/allowed', description='Technology story')

        with patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links, 'parse_posts', return_value=[blocked, allowed]):
            result = rss_links.run(
                _rss_source(), self._catalog(), CollectorState(),
                excluded_url_or_description_keywords=['politics'],
            )

        self.assertEqual([record.unique_id for record in result.posts],
                         [allowed.unique_id_string])

    def test_run_produces_no_checkpoints(self):
        """RSS resumes by cache validators, not a cursor."""
        with patch.object(rss_links, 'fetch_feeds', return_value=[]), \
             patch.object(rss_links, 'parse_posts', return_value=[]):
            result = rss_links.run(_rss_source(), self._catalog(), CollectorState())

        self.assertEqual(result.checkpoints, [])


class LatestEntryAtTests(unittest.TestCase):
    @staticmethod
    def _feed(*structs):
        entries = [_FeedEntry({'published_parsed': struct}) for struct in structs]
        return SimpleNamespace(entries=entries)

    def test_returns_none_without_a_feed(self):
        self.assertIsNone(rss_links.latest_entry_at(None))

    def test_returns_none_when_no_entry_is_datable(self):
        feed = SimpleNamespace(entries=[_FeedEntry({'title': 'no date'})])
        self.assertIsNone(rss_links.latest_entry_at(feed))

    def test_returns_the_newest_entry(self):
        feed = self._feed(
            (2026, 1, 1, 0, 0, 0, 0, 1, 0),
            (2026, 3, 4, 5, 6, 7, 0, 63, 0),
            (2025, 12, 31, 0, 0, 0, 0, 365, 0),
        )
        self.assertEqual(rss_links.latest_entry_at(feed), '2026-03-04T05:06:07Z')

    def test_falls_back_to_updated_when_published_is_absent(self):
        feed = SimpleNamespace(entries=[
            _FeedEntry({'updated_parsed': (2026, 2, 2, 1, 2, 3, 0, 33, 0)}),
        ])
        self.assertEqual(rss_links.latest_entry_at(feed), '2026-02-02T01:02:03Z')

    def test_ignores_unparseable_timestamps(self):
        feed = SimpleNamespace(entries=[
            _FeedEntry({'published_parsed': 'not a struct'}),
            _FeedEntry({'published_parsed': (2026, 1, 1, 0, 0, 0, 0, 1, 0)}),
        ])
        self.assertEqual(rss_links.latest_entry_at(feed), '2026-01-01T00:00:00Z')


class DestinationIndependenceTests(unittest.TestCase):
    def test_module_holds_no_database_imports(self):
        source = Path(rss_links.__file__).read_text(encoding='utf-8')
        for forbidden in ('db_utils', 'db_setup', 'sqlite3', 'psycopg'):
            self.assertNotIn(forbidden, source,
                             f'{forbidden} must not appear in rss_links.py')


class PickSiteLinkTests(unittest.TestCase):
    @staticmethod
    def _wrap(channel_dict):
        """Build a feedparser-shaped object: top-level dict with a 'feed' key
        holding the channel-level metadata (link, links, ...)."""
        channel = SimpleNamespace(
            get=lambda key, default=None, _d=channel_dict: _d.get(key, default),
        )
        return SimpleNamespace(
            get=lambda key, default=None, _c=channel: (
                _c if key == 'feed' else default
            ),
        )

    def test_returns_none_when_feed_missing(self):
        self.assertIsNone(rss_links.pick_site_link(None))

    def test_returns_none_when_channel_missing(self):
        bare = SimpleNamespace(get=lambda key, default=None: default)
        self.assertIsNone(rss_links.pick_site_link(bare))

    def test_prefers_top_level_link(self):
        feed = self._wrap({'link': 'https://example.com/', 'links': []})
        self.assertEqual(rss_links.pick_site_link(feed), 'https://example.com/')

    def test_strips_whitespace_on_link(self):
        feed = self._wrap({'link': '  https://example.com/news  '})
        self.assertEqual(
            rss_links.pick_site_link(feed), 'https://example.com/news',
        )

    def test_falls_back_to_alternate_html_link(self):
        link_entries = [
            SimpleNamespace(get=lambda k, d=None: {
                'rel': 'self', 'type': 'application/rss+xml',
                'href': 'https://example.com/feed.xml',
            }.get(k, d)),
            SimpleNamespace(get=lambda k, d=None: {
                'rel': 'alternate', 'type': 'text/html',
                'href': 'https://example.com/',
            }.get(k, d)),
        ]
        feed = self._wrap({'link': '', 'links': link_entries})
        self.assertEqual(rss_links.pick_site_link(feed), 'https://example.com/')

    def test_returns_none_when_only_non_http_scheme(self):
        feed = self._wrap({'link': 'javascript:alert(1)', 'links': []})
        self.assertIsNone(rss_links.pick_site_link(feed))

    def test_returns_none_when_nothing_usable(self):
        feed = self._wrap({'link': None, 'links': []})
        self.assertIsNone(rss_links.pick_site_link(feed))


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch

import requests

import rss_links


class _FakeResponse:
    def __init__(self, status_code=200, content=b'', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


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


if __name__ == '__main__':
    unittest.main()

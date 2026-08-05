import unittest

from utils import Post, RssPost, normalize_url


class NormalizeUrlTests(unittest.TestCase):
    def test_passes_through_https(self):
        self.assertEqual(
            normalize_url('https://example.com/a'),
            'https://example.com/a',
        )

    def test_resolves_site_relative_against_base(self):
        self.assertEqual(
            normalize_url('/blog/post', base='https://example.com/feed.xml'),
            'https://example.com/blog/post',
        )

    def test_resolves_protocol_relative_against_base(self):
        self.assertEqual(
            normalize_url('//other.com/x', base='https://example.com/'),
            'https://other.com/x',
        )

    def test_drops_relative_with_no_base(self):
        self.assertEqual(normalize_url('/blog/post'), '')

    def test_drops_typo_scheme(self):
        self.assertEqual(normalize_url('hhttps://example.com/x'), '')

    def test_drops_empty(self):
        self.assertEqual(normalize_url(''), '')
        self.assertEqual(normalize_url('   '), '')

    def test_drops_non_http_scheme(self):
        self.assertEqual(normalize_url('ftp://example.com/x'), '')
        self.assertEqual(normalize_url('javascript:alert(1)'), '')

    def test_drops_urls_that_urlsplit_refuses_to_parse(self):
        # An unbalanced bracket in the netloc is read as a malformed IPv6
        # literal and urlsplit raises rather than returning empty parts. One of
        # these in one Bluesky post used to abort the whole collection cycle,
        # for every source, on every run -- the cursor never advanced past it.
        for hostile in (
            'https://[example.com/x',
            'https://exa[mple.com/x',
            'http://[::1junk]/x',
        ):
            with self.subTest(url=hostile):
                self.assertEqual(normalize_url(hostile), '')

    def test_drops_unparseable_relative_url_resolved_against_base(self):
        # The same failure is reachable through the urljoin path.
        self.assertEqual(
            normalize_url('//[bad/path', base='https://example.com/feed.xml'),
            '',
        )

    def test_post_skips_unparseable_url_but_keeps_the_rest(self):
        post = Post()
        post.add_url('https://[example.com/x')
        post.add_url('https://example.com/good')
        self.assertEqual(post.urls, ['https://example.com/good'])

    def test_strips_whitespace(self):
        self.assertEqual(
            normalize_url('  https://example.com/x  '),
            'https://example.com/x',
        )


class RssPostUrlResolutionTests(unittest.TestCase):
    @staticmethod
    def _entry(link):
        # feedparser entries support both dict-style .get() and attribute access.
        class Entry(dict):
            def __getattr__(self, name):
                try:
                    return self[name]
                except KeyError as exc:
                    raise AttributeError(name) from exc

        return Entry(title='t', link=link, published='2026-01-01T00:00:00Z')

    def test_relative_link_resolved_against_feed_source(self):
        post = RssPost('https://example.com/feed.xml', 'Example', self._entry('/posts/foo'))
        self.assertEqual(post.urls, ['https://example.com/posts/foo'])

    def test_typo_scheme_dropped(self):
        post = RssPost('https://example.com/feed.xml', 'Example', self._entry('hhttps://example.com/x'))
        self.assertEqual(post.urls, [])
        self.assertFalse(post.post_has_urls)


if __name__ == '__main__':
    unittest.main()

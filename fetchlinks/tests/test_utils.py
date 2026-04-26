import unittest
from unittest.mock import patch

from utils import (
    BlueskyPost,
    Post,
    RedditPost,
    RssPost,
    build_hash,
    convert_date_string_for_mysql,
    convert_epoch_to_mysql,
    extract_urls_from_text,
)


class _RssEntry(dict):
    """feedparser entries support both dict-style .get() and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _make_reddit_post(url, **overrides):
    data = {
        'subreddit_name_prefixed': 'r/netsec',
        'author': 'someone',
        'title': 'a post',
        'permalink': '/r/netsec/comments/abc/a_post/',
        'created_utc': 1700000000,
        'url': url,
    }
    data.update(overrides)
    return {'data': data}


class BuildHashTests(unittest.TestCase):
    def test_known_input_produces_expected_hash(self):
        # SHA-256 of the bytes "hello"
        expected = '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
        self.assertEqual(build_hash('hello'), expected)

    def test_different_inputs_produce_different_hashes(self):
        self.assertNotEqual(build_hash('a'), build_hash('b'))


class ConvertDateStringTests(unittest.TestCase):
    def test_iso_string_round_trips_to_mysql_format(self):
        self.assertEqual(
            convert_date_string_for_mysql('2026-04-19T12:34:56Z'),
            '2026-04-19 12:34:56',
        )

    def test_unparseable_string_falls_back_to_now_utc(self):
        result = convert_date_string_for_mysql('not a date at all')
        # Should be a 19-char "YYYY-MM-DD HH:MM:SS" string.
        self.assertRegex(result, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


class ConvertEpochTests(unittest.TestCase):
    def test_known_epoch_converts_to_utc(self):
        # 1700000000 -> 2023-11-14 22:13:20 UTC
        self.assertEqual(convert_epoch_to_mysql(1700000000), '2023-11-14 22:13:20')

    def test_accepts_float_epoch(self):
        self.assertEqual(convert_epoch_to_mysql(1700000000.7), '2023-11-14 22:13:20')


class ExtractUrlsFromTextTests(unittest.TestCase):
    def test_finds_http_and_https(self):
        text = 'see http://a.com and https://b.com/x for more'
        self.assertEqual(
            extract_urls_from_text(text),
            ['http://a.com', 'https://b.com/x'],
        )

    def test_strips_trailing_punctuation(self):
        # The regex excludes ) ] > " ' from the match.
        text = 'check (https://example.com/foo) and [https://example.com/bar]'
        self.assertEqual(
            extract_urls_from_text(text),
            ['https://example.com/foo', 'https://example.com/bar'],
        )

    def test_empty_or_none_returns_empty_list(self):
        self.assertEqual(extract_urls_from_text(''), [])
        self.assertEqual(extract_urls_from_text(None), [])

    def test_ignores_non_http_schemes(self):
        text = 'ftp://x.com mailto:a@b.com javascript:foo()'
        self.assertEqual(extract_urls_from_text(text), [])


class PostTests(unittest.TestCase):
    def test_add_url_dedupes_and_preserves_order(self):
        p = Post()
        p.add_url('https://a.com')
        p.add_url('https://b.com')
        p.add_url('https://a.com')  # duplicate
        self.assertEqual(p.urls, ['https://a.com', 'https://b.com'])

    def test_add_url_drops_invalid(self):
        p = Post()
        p.add_url('not a url')
        p.add_url('')
        p.add_url('javascript:alert(1)')
        self.assertEqual(p.urls, [])

    def test_unique_id_string_is_order_invariant(self):
        a = Post()
        a.add_url('https://a.com')
        a.add_url('https://b.com')
        a._generate_unique_url_string()

        b = Post()
        b.add_url('https://b.com')
        b.add_url('https://a.com')
        b._generate_unique_url_string()

        self.assertEqual(a.unique_id_string, b.unique_id_string)
        self.assertNotEqual(a.unique_id_string, '')

    def test_get_post_row_shape(self):
        p = Post()
        p.source = 's'
        p.author = 'a'
        p.description = 'd'
        p.direct_link = 'dl'
        p.date_created = '2026-01-01 00:00:00'
        p.unique_id_string = 'u'
        self.assertEqual(p.get_post_row(), ('s', 'a', 'd', 'dl', '2026-01-01 00:00:00', 'u'))

    def test_get_url_rows_shape(self):
        p = Post()
        p.add_url('https://a.com')
        p.add_url('https://b.com')
        rows = p.get_url_rows()
        self.assertEqual(len(rows), 2)
        # (position, url, url_hash)
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(rows[0][1], 'https://a.com')
        self.assertEqual(rows[0][2], build_hash('https://a.com'))
        self.assertEqual(rows[1][0], 1)


class RssPostTests(unittest.TestCase):
    def test_falls_back_to_updated_when_no_published(self):
        entry = _RssEntry(title='t', link='https://example.com/a', updated='2026-04-19T12:34:56Z')
        post = RssPost('https://example.com/feed.xml', 'Example', entry)
        self.assertEqual(post.date_created, '2026-04-19 12:34:56')

    def test_falls_back_to_now_when_no_dates(self):
        entry = _RssEntry(title='t', link='https://example.com/a')
        post = RssPost('https://example.com/feed.xml', 'Example', entry)
        self.assertRegex(post.date_created, r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')

    def test_missing_title_defaults_to_empty(self):
        entry = _RssEntry(link='https://example.com/a', published='2026-04-19T12:00:00Z')
        post = RssPost('https://example.com/feed.xml', 'Example', entry)
        self.assertEqual(post.description, '')


class RedditPostTests(unittest.TestCase):
    def test_extract_data_builds_source_and_direct_link(self):
        post = RedditPost(_make_reddit_post('https://example.com/article'))
        self.assertEqual(post.source, 'https://www.reddit.com/r/netsec')
        self.assertEqual(post.author, 'someone')
        self.assertEqual(post.description, 'a post')
        self.assertEqual(
            post.direct_link,
            'https://www.reddit.com/r/netsec/comments/abc/a_post/',
        )
        self.assertEqual(post.date_created, '2023-11-14 22:13:20')

    def test_keeps_http_external_url(self):
        post = RedditPost(_make_reddit_post('http://example.com/article'))
        self.assertEqual(post.urls, ['http://example.com/article'])

    def test_unique_id_string_set_after_construction(self):
        post = RedditPost(_make_reddit_post('https://example.com/article'))
        self.assertNotEqual(post.unique_id_string, '')


class BlueskyPostTests(unittest.TestCase):
    def test_filters_invalid_urls(self):
        post = BlueskyPost(
            source='https://bsky.app/profile/alice',
            author='Alice',
            description='hi',
            direct_link='https://bsky.app/profile/alice/post/xyz',
            created_at='2026-04-19T12:34:56Z',
            urls=['https://example.com/a', 'javascript:alert(1)', '', 'https://example.com/b'],
        )
        self.assertEqual(post.urls, ['https://example.com/a', 'https://example.com/b'])
        self.assertEqual(post.date_created, '2026-04-19 12:34:56')


if __name__ == '__main__':
    unittest.main()

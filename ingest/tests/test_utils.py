import datetime as dt
import unittest
from unittest.mock import patch

from utils import (
    BlueskyPost,
    Post,
    RedditPost,
    RssPost,
    build_hash,
    clamp_date_not_in_future,
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

    def test_tz_aware_string_is_converted_to_utc(self):
        # +09:00 offset should subtract 9 hours when normalised to UTC.
        self.assertEqual(
            convert_date_string_for_mysql('2026-05-22T20:00:00+09:00'),
            '2026-05-22 11:00:00',
        )

    def test_naive_string_is_assumed_utc(self):
        # No tz offset: leave the wall-clock values as-is.
        self.assertEqual(
            convert_date_string_for_mysql('2026-04-19 12:34:56'),
            '2026-04-19 12:34:56',
        )

    def test_unparseable_string_falls_back_to_now_utc(self):
        class FixedDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 4, 26, 12, 34, 56, tzinfo=tz)

        with patch('utils.datetime.datetime', FixedDateTime):
            result = convert_date_string_for_mysql('not a date at all')

        self.assertEqual(result, '2026-04-26 12:34:56')


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

    def test_url_records_are_ordered(self):
        p = Post()
        p.add_url('https://a.com')
        p.add_url('https://b.com')
        p.source_type = 'rss'
        p.date_created = '2026-01-01 00:00:00'
        p.unique_id_string = 'u'
        record = p.to_record()
        self.assertEqual(list(record.urls),
                         ['https://a.com', 'https://b.com'])


class RssPostTests(unittest.TestCase):
    def test_falls_back_to_updated_when_no_published(self):
        entry = _RssEntry(title='t', link='https://example.com/a', updated='2026-04-19T12:34:56Z')
        post = RssPost('https://example.com/feed.xml', 'Example', entry)
        self.assertEqual(post.date_created, '2026-04-19 12:34:56')

    def test_falls_back_to_now_when_no_dates(self):
        entry = _RssEntry(title='t', link='https://example.com/a')

        class FixedDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 4, 26, 12, 34, 56, tzinfo=tz)

        with patch('utils.datetime.datetime', FixedDateTime):
            post = RssPost('https://example.com/feed.xml', 'Example', entry)

        self.assertEqual(post.date_created, '2026-04-26 12:34:56')

    def test_missing_title_defaults_to_empty(self):
        entry = _RssEntry(link='https://example.com/a', published='2026-04-19T12:00:00Z')
        post = RssPost('https://example.com/feed.xml', 'Example', entry)
        self.assertEqual(post.description, '')

    def test_source_type_is_rss(self):
        entry = _RssEntry(title='t', link='https://example.com/a', published='2026-04-19T12:00:00Z')
        post = RssPost('https://example.com/feed.xml', 'Example', entry)
        self.assertEqual(post.source_type, 'rss')

    def test_prefers_site_link_over_feed_source(self):
        entry = _RssEntry(title='t', link='https://example.com/a', published='2026-04-19T12:00:00Z')
        post = RssPost(
            'https://example.com/feed.xml',
            'Example',
            entry,
            site_link='https://example.com/',
        )
        self.assertEqual(post.source, 'https://example.com/')

    def test_falls_back_to_feed_source_when_site_link_missing(self):
        entry = _RssEntry(title='t', link='https://example.com/a', published='2026-04-19T12:00:00Z')
        post = RssPost('https://example.com/feed.xml', 'Example', entry, site_link=None)
        self.assertEqual(post.source, 'https://example.com/feed.xml')


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

    def test_source_type_is_reddit(self):
        post = RedditPost(_make_reddit_post('https://example.com/article'))
        self.assertEqual(post.source_type, 'reddit')


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

    def test_source_type_is_bluesky(self):
        post = BlueskyPost(
            source='https://bsky.app/profile/alice',
            author='Alice',
            description='hi',
            direct_link='https://bsky.app/profile/alice/post/xyz',
            created_at='2026-04-19T12:34:56Z',
            urls=['https://example.com/a'],
        )
        self.assertEqual(post.source_type, 'bluesky')


class ClampDateNotInFutureTests(unittest.TestCase):
    def _now_str(self, offset_seconds: int = 0) -> str:
        when = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(seconds=offset_seconds)
        return when.strftime('%Y-%m-%d %H:%M:%S')

    def test_past_date_passes_through_unchanged(self):
        self.assertEqual(
            clamp_date_not_in_future('2000-01-01 00:00:00'),
            '2000-01-01 00:00:00',
        )

    def test_empty_string_passes_through_unchanged(self):
        self.assertEqual(clamp_date_not_in_future(''), '')

    def test_unparseable_string_passes_through_unchanged(self):
        self.assertEqual(clamp_date_not_in_future('not-a-date'), 'not-a-date')

    def test_small_skew_within_tolerance_passes_through(self):
        candidate = self._now_str(offset_seconds=10)
        self.assertEqual(clamp_date_not_in_future(candidate), candidate)

    def test_far_future_date_is_clamped(self):
        clamped = clamp_date_not_in_future('2999-12-31 23:59:59')
        # Result is a valid timestamp that is no longer in the future.
        parsed = dt.datetime.strptime(clamped, '%Y-%m-%d %H:%M:%S')
        self.assertLessEqual(parsed, dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(seconds=1))

    def test_record_clamps_future_date(self):
        post = Post()
        post.source = 'https://x/'
        post.source_type = 'rss'
        post.author = 'a'
        post.description = 'd'
        post.direct_link = 'https://x/1'
        post.date_created = '2999-12-31 23:59:59'
        post.add_url('https://example.com/x')
        post._generate_unique_url_string()

        record = post.to_record()
        parsed = dt.datetime.fromisoformat(record.posted_at)
        self.assertLess(parsed.year, 2999)
        # In-memory value is untouched; only the persisted value changes.
        self.assertEqual(post.date_created, '2999-12-31 23:59:59')


class PostToRecordTests(unittest.TestCase):
    @staticmethod
    def _post(date_created='2026-01-02 03:04:05'):
        post = Post()
        post.source = 'https://feed.example'
        post.source_type = 'rss'
        post.author = 'someone'
        post.description = 'a description'
        post.direct_link = 'https://feed.example/post/1'
        post.date_created = date_created
        post.add_url('https://example.com/one')
        post.add_url('https://example.com/two')
        post._generate_unique_url_string()
        return post

    def test_carries_the_natural_identity_and_ordered_urls(self):
        post = self._post()

        record = post.to_record()

        self.assertEqual(record.unique_id, post.unique_id_string)
        self.assertEqual(record.source, 'https://feed.example')
        self.assertEqual(record.source_type, 'rss')
        self.assertEqual(record.author, 'someone')
        self.assertEqual(record.description, 'a description')
        self.assertEqual(record.direct_link, 'https://feed.example/post/1')
        self.assertEqual(record.urls,
                         ['https://example.com/one', 'https://example.com/two'])

    def test_carries_no_row_ids_positions_or_url_hashes(self):
        """The publisher derives those, so a stale one can never be inherited."""
        document = self._post().to_record().to_dict()

        self.assertNotIn('id', document)
        self.assertNotIn('post_id', document)
        self.assertNotIn('url_hash', document)
        self.assertNotIn('position', document)
        self.assertEqual(sorted(document), [
            'author', 'description', 'direct_link', 'posted_at', 'source',
            'source_type', 'unique_id', 'urls',
        ])

    def test_clamps_a_future_date_the_same_way_the_row_does(self):
        post = self._post(date_created='2999-12-31 23:59:59')

        record = post.to_record()

        self.assertLess(int(record.posted_at[:4]), 2999)
        # The in-memory post is untouched; only the emitted record is clamped.
        self.assertEqual(post.date_created, '2999-12-31 23:59:59')

    def test_urls_are_copied_not_aliased(self):
        post = self._post()

        record = post.to_record()
        post.add_url('https://example.com/three')

        self.assertEqual(len(record.urls), 2)


if __name__ == '__main__':
    unittest.main()

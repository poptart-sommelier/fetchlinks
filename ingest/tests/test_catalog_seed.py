"""Tests for destination-independent seed-file parsing."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import catalog_seed


def _write(tmp_dir, name, text):
    path = Path(tmp_dir) / name
    path.write_text(text, encoding='utf-8')
    return path


class ReadListFileTests(unittest.TestCase):
    def test_skips_blanks_and_comments_and_trims(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write(tmp_dir, 'seed.txt', (
                '# a comment\n'
                '\n'
                '  https://a.example/feed.xml  \n'
                '   # indented comment\n'
                'https://b.example/feed.xml\n'
            ))

            self.assertEqual(catalog_seed.read_list_file(path), [
                'https://a.example/feed.xml',
                'https://b.example/feed.xml',
            ])


class NormalizeFeedUrlTests(unittest.TestCase):
    def test_lowercases_scheme_and_host_only(self):
        self.assertEqual(
            catalog_seed.normalize_feed_url('HTTPS://Example.COM/Feed.XML'),
            'https://example.com/Feed.XML',
        )

    def test_an_empty_path_becomes_a_slash(self):
        """Otherwise one feed would be catalogued as two."""
        self.assertEqual(catalog_seed.normalize_feed_url('https://example.com'),
                         catalog_seed.normalize_feed_url('https://example.com/'))

    def test_drops_the_fragment_but_keeps_the_query(self):
        # A query string frequently *is* the feed selector.
        self.assertEqual(
            catalog_seed.normalize_feed_url('https://example.com/feed?id=7#top'),
            'https://example.com/feed?id=7',
        )

    def test_handles_empty_input(self):
        self.assertEqual(catalog_seed.normalize_feed_url(''), '/')
        self.assertEqual(catalog_seed.normalize_feed_url(None), '/')


class CleanCandidateUrlTests(unittest.TestCase):
    def test_trims_trailing_prose_punctuation(self):
        for raw in ('https://example.com/story.', 'https://example.com/story,',
                    'https://example.com/story!', 'https://example.com/story;'):
            with self.subTest(raw=raw):
                self.assertEqual(catalog_seed.clean_candidate_url(raw),
                                 'https://example.com/story')

    def test_keeps_a_balanced_closing_parenthesis(self):
        self.assertEqual(
            catalog_seed.clean_candidate_url('https://example.com/a_(b)'),
            'https://example.com/a_(b)',
        )

    def test_strips_prose_around_a_balanced_parenthesis(self):
        self.assertEqual(
            catalog_seed.clean_candidate_url('https://example.com/a_(b)).'),
            'https://example.com/a_(b)',
        )

    def test_rejects_a_non_http_scheme(self):
        self.assertEqual(catalog_seed.clean_candidate_url('javascript:alert(1)'), '')
        self.assertEqual(catalog_seed.clean_candidate_url('ftp://example.com/f'), '')

    def test_rejects_input_without_a_host(self):
        self.assertEqual(catalog_seed.clean_candidate_url('https:///path'), '')
        self.assertEqual(catalog_seed.clean_candidate_url('not a url'), '')
        self.assertEqual(catalog_seed.clean_candidate_url(''), '')


class SeedFeedPairsTests(unittest.TestCase):
    def test_returns_normalized_display_pairs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write(tmp_dir, 'feeds.txt',
                          'HTTPS://A.example/Feed.xml\nhttps://b.example/feed.xml\n')

            self.assertEqual(catalog_seed.seed_feed_pairs(path), [
                ('https://a.example/Feed.xml', 'HTTPS://A.example/Feed.xml'),
                ('https://b.example/feed.xml', 'https://b.example/feed.xml'),
            ])

    def test_deduplicates_on_the_normalized_key_keeping_the_first(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write(tmp_dir, 'feeds.txt', (
                'https://a.example/feed.xml\n'
                'https://A.EXAMPLE/feed.xml\n'
                'https://a.example/feed.xml#top\n'
            ))

            self.assertEqual(catalog_seed.seed_feed_pairs(path), [
                ('https://a.example/feed.xml', 'https://a.example/feed.xml'),
            ])

    def test_unusable_entries_are_dropped_rather_than_catalogued(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write(tmp_dir, 'feeds.txt',
                          'not a url\nhttps://a.example/feed.xml\n')

            self.assertEqual(catalog_seed.seed_feed_pairs(path),
                             [('https://a.example/feed.xml',
                               'https://a.example/feed.xml')])


class ResolveSeedFeedUrlsTests(unittest.TestCase):
    def test_returns_nothing_without_a_config(self):
        self.assertEqual(catalog_seed.resolve_seed_feed_urls(None), [])

    def test_returns_nothing_when_the_seed_file_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = SimpleNamespace(seed_file=Path(tmp_dir) / 'missing.txt')

            self.assertEqual(catalog_seed.resolve_seed_feed_urls(config), [])

    def test_reads_the_configured_seed_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write(tmp_dir, 'feeds.txt', 'https://a.example/feed.xml\n')
            config = SimpleNamespace(seed_file=path)

            self.assertEqual(catalog_seed.resolve_seed_feed_urls(config),
                             ['https://a.example/feed.xml'])


class SubredditNameTests(unittest.TestCase):
    def test_clean_strips_the_r_prefix_and_slashes_but_keeps_case(self):
        for raw in ('r/NetSec', '/r/NetSec/', 'R/NetSec', ' NetSec ', 'NetSec/'):
            with self.subTest(raw=raw):
                self.assertEqual(catalog_seed.clean_subreddit_name(raw), 'NetSec')

    def test_normalize_lowercases_the_cleaned_name(self):
        self.assertEqual(catalog_seed.normalize_subreddit_name('/r/NetSec/'), 'netsec')

    def test_handles_empty_input(self):
        self.assertEqual(catalog_seed.clean_subreddit_name(''), '')
        self.assertEqual(catalog_seed.normalize_subreddit_name(None), '')


class SeedSubredditPairsTests(unittest.TestCase):
    def test_prefers_the_seed_file_over_the_inline_list(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = _write(tmp_dir, 'subs.txt', 'r/NetSec\nblueteamsec\n')
            config = SimpleNamespace(seed_file=path, subreddits=('ignored',))

            self.assertEqual(catalog_seed.seed_subreddit_pairs(config), [
                ('netsec', 'NetSec'),
                ('blueteamsec', 'blueteamsec'),
            ])

    def test_falls_back_to_the_inline_list(self):
        config = SimpleNamespace(seed_file=None, subreddits=('r/NetSec',))

        self.assertEqual(catalog_seed.seed_subreddit_pairs(config),
                         [('netsec', 'NetSec')])

    def test_deduplicates_case_insensitively_keeping_the_first(self):
        config = SimpleNamespace(seed_file=None,
                                 subreddits=('NetSec', 'netsec', 'r/NETSEC'))

        self.assertEqual(catalog_seed.seed_subreddit_pairs(config),
                         [('netsec', 'NetSec')])

    def test_blank_entries_are_dropped(self):
        config = SimpleNamespace(seed_file=None, subreddits=('r/', '  ', 'netsec'))

        self.assertEqual(catalog_seed.seed_subreddit_pairs(config),
                         [('netsec', 'netsec')])

    def test_returns_nothing_without_a_config(self):
        self.assertEqual(catalog_seed.seed_subreddit_pairs(None), [])


class DestinationIndependenceTests(unittest.TestCase):
    def test_module_holds_no_database_code(self):
        source = Path(catalog_seed.__file__).read_text(encoding='utf-8')
        for forbidden in ('db_utils', 'db_setup', 'sqlite3', 'psycopg'):
            self.assertNotIn(forbidden, source,
                             f'{forbidden} must not appear in catalog_seed.py')


if __name__ == '__main__':
    unittest.main()

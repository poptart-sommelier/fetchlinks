import unittest

import url_filters
from utils import Post


def _make_post(urls, description='description'):
    post = Post()
    post.source = 'https://source.example'
    post.author = 'author'
    post.description = description
    post.direct_link = 'https://source.example/post'
    post.date_created = '2026-05-02 12:00:00'
    for url in urls:
        post.add_url(url)
    post._generate_unique_url_string()
    return post


class UrlHostKeywordTests(unittest.TestCase):
    def test_keyword_matches_hostname_contains(self):
        self.assertTrue(
            url_filters.url_matches_host_keyword(
                'https://www.businessinsider.com/story',
                ['insider'],
            )
        )

    def test_domain_keyword_with_dot_matches_hostname(self):
        self.assertTrue(
            url_filters.url_matches_host_keyword(
                'https://markets.businessinsider.com/story',
                ['businessinsider.com'],
            )
        )

    def test_keyword_is_case_insensitive(self):
        self.assertTrue(
            url_filters.url_matches_host_keyword(
                'https://News.BusinessInsider.com/story',
                ['INSIDER'],
            )
        )

    def test_path_only_match_does_not_block_url(self):
        self.assertFalse(
            url_filters.url_matches_host_keyword(
                'https://example.com/businessinsider.com/story',
                ['businessinsider.com'],
            )
        )

    def test_empty_or_invalid_url_does_not_match(self):
        self.assertFalse(url_filters.url_matches_host_keyword('', ['insider']))
        self.assertFalse(url_filters.url_matches_host_keyword('not a url', ['insider']))

    def test_non_list_keywords_do_not_match(self):
        self.assertFalse(
            url_filters.url_matches_host_keyword(
                'https://www.businessinsider.com/story',
                'insider',
            )
        )


class FilterPostsTests(unittest.TestCase):
    def test_filters_denied_urls_and_keeps_allowed_urls(self):
        post = _make_post([
            'https://www.businessinsider.com/story',
            'https://example.com/allowed',
        ])
        original_unique_id = post.unique_id_string

        filtered = url_filters.filter_posts_by_url_host_keywords([post], ['insider'], 'test')

        self.assertEqual(filtered, [post])
        self.assertEqual(post.urls, ['https://example.com/allowed'])
        self.assertNotEqual(post.unique_id_string, original_unique_id)

    def test_skips_post_when_all_urls_denied(self):
        post = _make_post(['https://www.businessinsider.com/story'])

        filtered = url_filters.filter_posts_by_url_host_keywords([post], ['insider'], 'test')

        self.assertEqual(filtered, [])

    def test_empty_keywords_is_noop(self):
        post = _make_post(['https://www.businessinsider.com/story'])

        filtered = url_filters.filter_posts_by_url_host_keywords([post], [], 'test')

        self.assertEqual(filtered, [post])
        self.assertEqual(post.urls, ['https://www.businessinsider.com/story'])

    def test_reads_keywords_from_sources(self):
        sources = {
            'ingest': {
                'excluded_url_host_keywords': [' Insider ', '', 12, 'businessinsider.com'],
            },
        }

        self.assertEqual(
            url_filters.excluded_url_host_keywords_from_sources(sources),
            ['insider', 'businessinsider.com'],
        )

    def test_full_url_keyword_skips_post(self):
        blocked = _make_post(['https://example.com/news/politics/story'])
        allowed = _make_post(['https://example.com/news/technology/story'])

        filtered = url_filters.filter_posts_by_url_or_description_keywords(
            [blocked, allowed],
            ['politics'],
            'test',
        )

        self.assertEqual(filtered, [allowed])

    def test_description_keyword_skips_post_case_insensitive(self):
        blocked = _make_post(['https://example.com/story'], description='A Politics story')
        allowed = _make_post(['https://example.com/other'], description='A technology story')

        filtered = url_filters.filter_posts_by_url_or_description_keywords(
            [blocked, allowed],
            ['politics'],
            'test',
        )

        self.assertEqual(filtered, [allowed])

    def test_description_keyword_uses_word_boundary(self):
        post = _make_post(['https://example.com/story'], description='A trumpet lesson')

        filtered = url_filters.filter_posts_by_url_or_description_keywords([post], ['trump'], 'test')

        self.assertEqual(filtered, [post])

    def test_empty_url_or_description_keywords_is_noop(self):
        post = _make_post(['https://example.com/news/politics/story'], description='Politics')

        filtered = url_filters.filter_posts_by_url_or_description_keywords([post], [], 'test')

        self.assertEqual(filtered, [post])

    def test_reads_url_or_description_keywords_from_sources(self):
        sources = {
            'ingest': {
                'excluded_url_or_description_keywords': [' Politics ', '', 12, 'trump'],
            },
        }

        self.assertEqual(
            url_filters.excluded_url_or_description_keywords_from_sources(sources),
            ['politics', 'trump'],
        )


if __name__ == '__main__':
    unittest.main()

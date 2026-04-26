import unittest
from unittest.mock import Mock, patch

import reddit_links
from utils import RedditPost


def _make_reddit_post(url):
    return {
        'data': {
            'subreddit_name_prefixed': 'r/netsec',
            'author': 'someone',
            'title': 'a post',
            'permalink': '/r/netsec/comments/abc/a_post/',
            'created_utc': 1700000000,
            'url': url,
        }
    }


class RedditLinksTests(unittest.TestCase):
    @patch("reddit_links.requests.get")
    def test_get_subreddit_returns_empty_on_request_error(self, mock_get):
        mock_get.side_effect = reddit_links.requests.exceptions.Timeout("timeout")

        posts = reddit_links.get_subreddit("netsec", "token", "test-ua/0.1")

        self.assertEqual(posts, [])

    @patch("reddit_links.requests.get")
    def test_get_subreddit_returns_children_on_success(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": {
                "children": [{"data": {"title": "x"}}]
            }
        }
        mock_get.return_value = mock_response

        posts = reddit_links.get_subreddit("netsec", "token", "test-ua/0.1")

        self.assertEqual(len(posts), 1)
        mock_get.assert_called_once_with(
            url='https://oauth.reddit.com/r/netsec/new/.json',
            params={'sort': 'new', 'show': 'all', 't': 'all', 'limit': '100'},
            headers={'Authorization': 'Bearer token', 'User-Agent': 'test-ua/0.1'},
            timeout=reddit_links.REQUEST_TIMEOUT_SECONDS,
        )

    @patch("reddit_links.requests.get")
    def test_get_subreddit_returns_empty_on_invalid_json(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError('not json')
        mock_get.return_value = mock_response

        posts = reddit_links.get_subreddit("netsec", "token", "test-ua/0.1")

        self.assertEqual(posts, [])

    @patch("reddit_links.requests.get")
    def test_get_subreddit_returns_empty_on_http_error(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = reddit_links.requests.exceptions.HTTPError('bad')
        mock_get.return_value = mock_response

        posts = reddit_links.get_subreddit("netsec", "token", "test-ua/0.1")

        self.assertEqual(posts, [])

    @patch("reddit_links.requests.get")
    def test_get_subreddit_returns_empty_when_children_not_list(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'data': {'children': {'unexpected': 'shape'}}}
        mock_get.return_value = mock_response

        posts = reddit_links.get_subreddit("netsec", "token", "test-ua/0.1")

        self.assertEqual(posts, [])


class RedditPostExtractUrlsTests(unittest.TestCase):
    def test_skips_relative_subreddit_path(self):
        # Regression: url like "/r/netsec/comments/..." used to leak through
        # and produce malformed entries.
        post = RedditPost(_make_reddit_post('/r/netsec/comments/xyz/another/'))
        self.assertEqual(post.urls, [])
        self.assertFalse(post.post_has_urls)

    def test_skips_self_post_url(self):
        post = RedditPost(_make_reddit_post('https://www.reddit.com/r/netsec/comments/xyz/another/'))
        self.assertEqual(post.urls, [])

    def test_keeps_external_https_url(self):
        post = RedditPost(_make_reddit_post('https://example.com/article'))
        self.assertEqual(post.urls, ['https://example.com/article'])

    def test_skips_missing_url_field(self):
        data = _make_reddit_post('https://example.com')['data']
        del data['url']
        post = RedditPost({'data': data})
        self.assertEqual(post.urls, [])


class RedditRunTests(unittest.TestCase):
    def test_run_does_not_insert_when_no_posts_parse(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}

        with patch.object(reddit_links, 'get_subreddits', return_value=[_make_reddit_post('https://example.com')]) as get_subreddits, \
             patch.object(reddit_links, 'parse_posts', return_value=[]) as parse_posts, \
             patch.object(reddit_links.db_utils, 'db_insert') as db_insert:
            reddit_links.run(reddit_config, db_info)

        get_subreddits.assert_called_once_with(reddit_config)
        parse_posts.assert_called_once()
        db_insert.assert_not_called()

    def test_run_inserts_parsed_posts(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        parsed_posts = [RedditPost(_make_reddit_post('https://example.com/article'))]

        with patch.object(reddit_links, 'get_subreddits', return_value=[_make_reddit_post('https://example.com/article')]), \
             patch.object(reddit_links, 'parse_posts', return_value=parsed_posts), \
             patch.object(reddit_links.db_utils, 'db_insert', return_value=1) as db_insert:
            reddit_links.run(reddit_config, db_info)

        db_insert.assert_called_once_with(parsed_posts, reddit_links.Path('/tmp/db') / 'fetchlinks.db')


if __name__ == "__main__":
    unittest.main()

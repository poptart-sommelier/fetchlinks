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


if __name__ == "__main__":
    unittest.main()

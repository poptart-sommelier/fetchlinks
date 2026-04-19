import unittest
from unittest.mock import Mock, patch

import reddit_links


class RedditLinksTests(unittest.TestCase):
    @patch("reddit_links.requests.get")
    def test_get_subreddit_returns_empty_on_request_error(self, mock_get):
        mock_get.side_effect = reddit_links.requests.exceptions.Timeout("timeout")

        posts = reddit_links.get_subreddit("netsec", "token")

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

        posts = reddit_links.get_subreddit("netsec", "token")

        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()

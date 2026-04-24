import tempfile
import unittest
from pathlib import Path

import bluesky_links
import db_setup
import db_utils


class BlueskyLinksTests(unittest.TestCase):
    def test_parse_feed_item_extracts_embed_facet_and_text_links(self):
        item = {
            'post': {
                'uri': 'at://did:plc:alice/app.bsky.feed.post/xyz123',
                'author': {
                    'handle': 'alice.bsky.social',
                    'displayName': 'Alice',
                    'did': 'did:plc:alice',
                },
                'record': {
                    'text': 'Interesting writeup https://example.org/article',
                    'createdAt': '2026-04-19T12:00:00.000Z',
                    'facets': [
                        {
                            'features': [
                                {
                                    '$type': 'app.bsky.richtext.facet#link',
                                    'uri': 'https://facet.example/one',
                                }
                            ]
                        }
                    ],
                },
                'embed': {
                    'external': {
                        'uri': 'https://embed.example/two'
                    }
                },
            }
        }

        parsed = bluesky_links._parse_feed_item(item)

        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.post_has_urls)
        self.assertEqual(parsed.author, 'Alice')
        self.assertIn('https://facet.example/one', parsed.urls)
        self.assertIn('https://embed.example/two', parsed.urls)
        self.assertIn('https://example.org/article', parsed.urls)

    def test_bluesky_cursor_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_dir = Path(tmp_dir) / 'db'
            db_name = 'fetchlinks.db'
            db_setup.db_initial_setup(str(db_dir), db_name)
            db_path = db_dir / db_name

            self.assertIsNone(db_utils.db_get_bluesky_cursor(db_path))

            db_utils.db_set_bluesky_cursor('cursor-123', db_path)

            self.assertEqual(db_utils.db_get_bluesky_cursor(db_path), 'cursor-123')


if __name__ == '__main__':
    unittest.main()

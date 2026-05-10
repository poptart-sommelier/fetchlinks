import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


def _timeline_item(url='https://example.com/article', created_at='2026-04-19T12:00:00.000Z', text=None):
    return {
        'post': {
            'uri': 'at://did:plc:alice/app.bsky.feed.post/xyz123',
            'author': {'handle': 'alice.bsky.social', 'displayName': 'Alice'},
            'record': {
                'text': text or f'Interesting writeup {url}',
                'createdAt': created_at,
            },
        }
    }


class BlueskyRunTests(unittest.TestCase):
    def test_run_skips_when_disabled(self):
        with patch.object(bluesky_links, 'BlueskyAuth') as auth_cls, \
             patch.object(bluesky_links.db_utils, 'db_get_bluesky_cursor') as get_cursor:
            bluesky_links.run({'enabled': False}, {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'})

        auth_cls.assert_not_called()
        get_cursor.assert_not_called()

    def test_run_fetches_pages_inserts_posts_and_persists_cursor(self):
        config = {'enabled': True, 'credential_location': '/tmp/bsky.json', 'timeline_limit': 250}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        client = object()
        auth_client = Mock()
        auth_client.get_client.return_value = client
        fetch_results = [
            ([_timeline_item('https://example.com/one')], 'cursor-2'),
            ([], 'cursor-3'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client) as auth_cls, \
             patch.object(bluesky_links.db_utils, 'db_get_bluesky_cursor', return_value='cursor-1') as get_cursor, \
             patch.object(bluesky_links, '_fetch_timeline_page', side_effect=fetch_results) as fetch_page, \
             patch.object(bluesky_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(bluesky_links.db_utils, 'db_set_bluesky_cursor') as set_cursor:
            bluesky_links.run(config, db_info)

        db_path = bluesky_links.Path('/tmp/db') / 'fetchlinks.db'
        auth_cls.assert_called_once_with('/tmp/bsky.json')
        get_cursor.assert_called_once_with(db_path)
        self.assertEqual(fetch_page.call_args_list[0].args, (client, 'cursor-1', bluesky_links.MAX_TIMELINE_LIMIT))
        self.assertEqual(fetch_page.call_args_list[1].args, (client, 'cursor-2', bluesky_links.MAX_TIMELINE_LIMIT))
        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(len(inserted_posts), 1)
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/one'])
        db_insert.assert_called_once_with(inserted_posts, db_path)
        set_cursor.assert_called_once_with('cursor-3', db_path)

    def test_run_persists_cursor_even_when_no_items_returned(self):
        config = {'enabled': True, 'credential_location': '/tmp/bsky.json'}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        auth_client = Mock()
        auth_client.get_client.return_value = object()

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links.db_utils, 'db_get_bluesky_cursor', return_value='cursor-1'), \
             patch.object(bluesky_links, '_fetch_timeline_page', return_value=([], 'cursor-2')), \
             patch.object(bluesky_links.db_utils, 'db_insert', return_value=0) as db_insert, \
             patch.object(bluesky_links.db_utils, 'db_set_bluesky_cursor') as set_cursor:
            bluesky_links.run(config, db_info)

        db_path = bluesky_links.Path('/tmp/db') / 'fetchlinks.db'
        db_insert.assert_called_once_with([], db_path)
        set_cursor.assert_called_once_with('cursor-2', db_path)

    def test_run_filters_old_posts_before_insert(self):
        config = {'enabled': True, 'credential_location': '/tmp/bsky.json'}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        auth_client = Mock()
        auth_client.get_client.return_value = object()
        feed_items = [
            _timeline_item('https://example.com/old', created_at='2000-01-01T00:00:00.000Z'),
            _timeline_item('https://example.com/recent', created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links.db_utils, 'db_get_bluesky_cursor', return_value=None), \
             patch.object(bluesky_links, '_fetch_timeline_page', return_value=(feed_items, None)), \
             patch.object(bluesky_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(bluesky_links.db_utils, 'db_set_bluesky_cursor'):
            bluesky_links.run(config, db_info, max_post_age_months=3)

        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(len(inserted_posts), 1)
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/recent'])

    def test_run_filters_denied_host_keywords_before_insert(self):
        config = {'enabled': True, 'credential_location': '/tmp/bsky.json'}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        auth_client = Mock()
        auth_client.get_client.return_value = object()
        feed_items = [
            _timeline_item('https://www.businessinsider.com/story', created_at='2999-01-01T00:00:00.000Z'),
            _timeline_item('https://example.com/recent', created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links.db_utils, 'db_get_bluesky_cursor', return_value=None), \
             patch.object(bluesky_links, '_fetch_timeline_page', return_value=(feed_items, None)), \
             patch.object(bluesky_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(bluesky_links.db_utils, 'db_set_bluesky_cursor'):
            bluesky_links.run(config, db_info, excluded_url_host_keywords=['insider'])

        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(len(inserted_posts), 1)
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/recent'])

    def test_run_filters_denied_url_or_description_keywords_before_insert(self):
        config = {'enabled': True, 'credential_location': '/tmp/bsky.json'}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        auth_client = Mock()
        auth_client.get_client.return_value = object()
        feed_items = [
            _timeline_item('https://example.com/story', created_at='2999-01-01T00:00:00.000Z', text='Politics story https://example.com/story'),
            _timeline_item('https://example.com/recent', created_at='2999-01-01T00:00:00.000Z', text='Technology story https://example.com/recent'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links.db_utils, 'db_get_bluesky_cursor', return_value=None), \
             patch.object(bluesky_links, '_fetch_timeline_page', return_value=(feed_items, None)), \
             patch.object(bluesky_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(bluesky_links.db_utils, 'db_set_bluesky_cursor'):
            bluesky_links.run(config, db_info, excluded_url_or_description_keywords=['politics'])

        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(len(inserted_posts), 1)
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/recent'])


if __name__ == '__main__':
    unittest.main()

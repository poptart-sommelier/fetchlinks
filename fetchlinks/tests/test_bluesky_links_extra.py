import unittest
from unittest.mock import Mock, patch

import bluesky_links


class IsExcludedHostTests(unittest.TestCase):
    def test_bsky_app_excluded(self):
        self.assertTrue(bluesky_links._is_excluded_host('https://bsky.app/profile/x'))

    def test_subdomain_of_excluded_host(self):
        self.assertTrue(bluesky_links._is_excluded_host('https://staging.bsky.social/x'))

    def test_external_host_allowed(self):
        self.assertFalse(bluesky_links._is_excluded_host('https://example.com/'))

    def test_malformed_url_returns_false(self):
        # urlparse rarely raises but handle gracefully either way.
        self.assertFalse(bluesky_links._is_excluded_host(''))


class ExtractLinksFromFacetsTests(unittest.TestCase):
    def test_extracts_http_links_only(self):
        record = {
            'facets': [
                {'features': [{'uri': 'https://a.example/x'}]},
                {'features': [{'uri': 'at://did:plc:xyz/foo'}, {'uri': 'http://b.example/'}]},
            ]
        }
        self.assertEqual(
            bluesky_links._extract_links_from_facets(record),
            ['https://a.example/x', 'http://b.example/'],
        )

    def test_no_facets_returns_empty(self):
        self.assertEqual(bluesky_links._extract_links_from_facets({}), [])


class ExtractLinksFromEmbedTests(unittest.TestCase):
    def test_walks_nested_dicts_and_lists(self):
        embed = {
            'external': {'uri': 'https://outer.example/x'},
            'images': [
                {'fullsize': 'https://img.example/1'},
                {'thumb': {'uri': 'https://img.example/2'}},
            ],
            'noise': {'uri': 'at://did:plc:abc/y'},
        }
        links = bluesky_links._extract_links_from_embed(embed)
        self.assertIn('https://outer.example/x', links)
        self.assertIn('https://img.example/2', links)
        self.assertNotIn('at://did:plc:abc/y', links)

    def test_handles_none(self):
        self.assertEqual(bluesky_links._extract_links_from_embed(None), [])


class TimelineCallTests(unittest.TestCase):
    def test_call_timeline_uses_sdk_nested_client_shape(self):
        client = Mock()
        client.app.bsky.feed.get_timeline.return_value = {'feed': [], 'cursor': 'next'}

        response = bluesky_links._call_timeline(client, limit=25, cursor='cursor-1')

        client.app.bsky.feed.get_timeline.assert_called_once_with(
            params={'limit': 25, 'cursor': 'cursor-1'}
        )
        self.assertEqual(response, {'feed': [], 'cursor': 'next'})

    def test_call_timeline_uses_legacy_client_shape(self):
        class LegacyClient:
            def __init__(self):
                self.get_timeline = Mock(return_value={'feed': [], 'cursor': 'next'})

        client = LegacyClient()

        response = bluesky_links._call_timeline(client, limit=25, cursor=None)

        client.get_timeline.assert_called_once_with(limit=25, cursor=None)
        self.assertEqual(response, {'feed': [], 'cursor': 'next'})

    def test_as_dict_accepts_model_dump_objects(self):
        class ModelDumpObject:
            def model_dump(self, exclude_none=True, by_alias=True):
                return {'feed': [], 'cursor': 'next'}

        client = Mock()
        client.app.bsky.feed.get_timeline.return_value = ModelDumpObject()

        self.assertEqual(
            bluesky_links._call_timeline(client, limit=10, cursor='c'),
            {'feed': [], 'cursor': 'next'},
        )


class FetchTimelinePageTests(unittest.TestCase):
    def test_non_list_feed_payload_returns_empty_page(self):
        with patch.object(bluesky_links, '_call_timeline', return_value={'feed': 'not-a-list', 'cursor': 'next'}):
            items, cursor = bluesky_links._fetch_timeline_page(object(), cursor='old', limit=25)

        self.assertEqual(items, [])
        self.assertIsNone(cursor)

    def test_valid_feed_payload_returns_items_and_cursor(self):
        expected_items = [{'post': {'record': {'text': 'hi'}}}]
        with patch.object(bluesky_links, '_call_timeline', return_value={'feed': expected_items, 'cursor': 'next'}):
            items, cursor = bluesky_links._fetch_timeline_page(object(), cursor='old', limit=25)

        self.assertEqual(items, expected_items)
        self.assertEqual(cursor, 'next')


class BuildDirectLinkTests(unittest.TestCase):
    def test_builds_url_from_handle_and_uri(self):
        author = {'handle': 'alice.bsky.social'}
        post = {'uri': 'at://did:plc:alice/app.bsky.feed.post/xyz123'}
        self.assertEqual(
            bluesky_links._build_direct_link(author, post),
            'https://bsky.app/profile/alice.bsky.social/post/xyz123',
        )

    def test_returns_empty_when_handle_missing(self):
        self.assertEqual(bluesky_links._build_direct_link({}, {'uri': 'at://x/post/abc'}), '')

    def test_returns_empty_when_uri_missing(self):
        self.assertEqual(
            bluesky_links._build_direct_link({'handle': 'alice'}, {}),
            '',
        )


class BuildSourceTests(unittest.TestCase):
    def test_with_handle(self):
        self.assertEqual(
            bluesky_links._build_source({'handle': 'alice.bsky.social'}),
            'https://bsky.app/profile/alice.bsky.social',
        )

    def test_falls_back_to_did(self):
        self.assertEqual(
            bluesky_links._build_source({'did': 'did:plc:abc'}),
            'https://bsky.app/profile/did:plc:abc',
        )

    def test_no_author_info(self):
        self.assertEqual(bluesky_links._build_source({}), 'https://bsky.app')


def _item_with(text='hi', created_at='2026-04-19T12:00:00Z', facets=None, embed=None):
    return {
        'post': {
            'uri': 'at://did:plc:alice/app.bsky.feed.post/xyz',
            'author': {'handle': 'alice.bsky.social', 'displayName': 'Alice'},
            'record': {
                'text': text,
                'createdAt': created_at,
                'facets': facets or [],
            },
            'embed': embed,
        }
    }


class ParseFeedItemTests(unittest.TestCase):
    def test_missing_post_returns_none(self):
        self.assertIsNone(bluesky_links._parse_feed_item({}))

    def test_missing_text_returns_none(self):
        item = _item_with(text='')
        self.assertIsNone(bluesky_links._parse_feed_item(item))

    def test_missing_created_at_returns_none(self):
        item = _item_with(created_at='')
        self.assertIsNone(bluesky_links._parse_feed_item(item))

    def test_only_excluded_links_returns_none(self):
        item = _item_with(text='see https://bsky.app/profile/x')
        self.assertIsNone(bluesky_links._parse_feed_item(item))


if __name__ == '__main__':
    unittest.main()

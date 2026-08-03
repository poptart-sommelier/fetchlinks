import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bluesky_links
from config import BlueskySource
from pipeline.state import CollectorState


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
        """The timeline resume point now lives in local collector state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / 'collector-state.v1.json'
            state = CollectorState()

            self.assertIsNone(state.checkpoint(
                bluesky_links.CHECKPOINT_SOURCE_TYPE,
                bluesky_links.CHECKPOINT_SOURCE_KEY,
            ))

            state.set_checkpoint(
                bluesky_links.CHECKPOINT_SOURCE_TYPE,
                bluesky_links.CHECKPOINT_SOURCE_KEY,
                cursor='cursor-123',
            )
            state.save(state_path)

            reloaded = CollectorState.load(state_path)
            self.assertEqual(
                reloaded.checkpoint(
                    bluesky_links.CHECKPOINT_SOURCE_TYPE,
                    bluesky_links.CHECKPOINT_SOURCE_KEY,
                ),
                'cursor-123',
            )


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
    def _config(self, enabled=True, credential_location='/tmp/bsky.json', timeline_limit=50):
        return BlueskySource(
            enabled=enabled,
            credential_location=Path(credential_location),
            timeline_limit=timeline_limit,
        )

    @staticmethod
    def _state(cursor=None):
        state = CollectorState()
        if cursor:
            state.set_checkpoint(
                bluesky_links.CHECKPOINT_SOURCE_TYPE,
                bluesky_links.CHECKPOINT_SOURCE_KEY,
                cursor=cursor,
            )
        return state

    def test_run_skips_when_disabled(self):
        with patch.object(bluesky_links, 'BlueskyAuth') as auth_cls:
            result = bluesky_links.run(self._config(enabled=False), self._state())

        auth_cls.assert_not_called()
        self.assertTrue(result.is_empty)

    def test_run_fetches_pages_collects_posts_and_advances_the_cursor(self):
        config = self._config(timeline_limit=250)
        client = object()
        auth_client = Mock()
        auth_client.get_client.return_value = client
        fetch_results = [
            ([_timeline_item('https://example.com/one',
                             created_at='2999-01-01T00:00:00.000Z')], 'cursor-2'),
            ([], 'cursor-3'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client) as auth_cls, \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          side_effect=fetch_results) as fetch_page:
            result = bluesky_links.run(config, self._state(cursor='cursor-1'))

        auth_cls.assert_called_once_with(str(Path('/tmp/bsky.json')))
        # The configured limit is clamped to the API maximum.
        self.assertEqual(fetch_page.call_args_list[0].args,
                         (client, 'cursor-1', bluesky_links.MAX_TIMELINE_LIMIT))
        self.assertEqual(fetch_page.call_args_list[1].args,
                         (client, 'cursor-2', bluesky_links.MAX_TIMELINE_LIMIT))
        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls, ['https://example.com/one'])
        self.assertEqual(len(result.checkpoints), 1)
        self.assertEqual(result.checkpoints[0].cursor, 'cursor-3')
        self.assertEqual(result.checkpoints[0].source_type, 'bluesky')
        self.assertEqual(result.checkpoints[0].source_key, 'timeline')

    def test_run_advances_the_cursor_even_when_no_items_returned(self):
        auth_client = Mock()
        auth_client.get_client.return_value = object()

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=([], 'cursor-2')):
            result = bluesky_links.run(self._config(), self._state(cursor='cursor-1'))

        self.assertEqual(result.posts, [])
        self.assertEqual([cp.cursor for cp in result.checkpoints], ['cursor-2'])

    def test_run_emits_no_checkpoint_when_the_cursor_does_not_move(self):
        """Rewriting an identical cursor would only churn the observation time."""
        auth_client = Mock()
        auth_client.get_client.return_value = object()

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=([], 'cursor-1')):
            result = bluesky_links.run(self._config(), self._state(cursor='cursor-1'))

        self.assertEqual(result.checkpoints, [])

    def test_run_emits_no_checkpoint_when_the_cursor_is_lost(self):
        """An empty cursor would restart the timeline from the beginning."""
        auth_client = Mock()
        auth_client.get_client.return_value = object()

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=([_timeline_item(created_at='2999-01-01T00:00:00.000Z')], None)):
            result = bluesky_links.run(self._config(), self._state(cursor='cursor-1'))

        self.assertEqual(result.checkpoints, [])
        self.assertEqual(len(result.posts), 1)

    def test_run_filters_old_posts(self):
        auth_client = Mock()
        auth_client.get_client.return_value = object()
        feed_items = [
            _timeline_item('https://example.com/old', created_at='2000-01-01T00:00:00.000Z'),
            _timeline_item('https://example.com/recent', created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=(feed_items, None)):
            result = bluesky_links.run(self._config(), self._state(),
                                       max_post_age_months=3)

        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls, ['https://example.com/recent'])

    def test_run_filters_denied_host_keywords(self):
        auth_client = Mock()
        auth_client.get_client.return_value = object()
        feed_items = [
            _timeline_item('https://www.businessinsider.com/story',
                           created_at='2999-01-01T00:00:00.000Z'),
            _timeline_item('https://example.com/recent',
                           created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=(feed_items, None)):
            result = bluesky_links.run(self._config(), self._state(),
                                       excluded_url_host_keywords=['insider'])

        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls, ['https://example.com/recent'])

    def test_run_filters_denied_url_or_description_keywords(self):
        auth_client = Mock()
        auth_client.get_client.return_value = object()
        feed_items = [
            _timeline_item('https://example.com/story',
                           created_at='2999-01-01T00:00:00.000Z',
                           text='Politics story https://example.com/story'),
            _timeline_item('https://example.com/recent',
                           created_at='2999-01-01T00:00:00.000Z',
                           text='Technology story https://example.com/recent'),
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=(feed_items, None)):
            result = bluesky_links.run(
                self._config(), self._state(),
                excluded_url_or_description_keywords=['politics'],
            )

        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls, ['https://example.com/recent'])

    def test_run_never_reports_a_follows_snapshot(self):
        """Timeline collection says nothing about the follows list."""
        auth_client = Mock()
        auth_client.get_client.return_value = object()

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_fetch_timeline_page',
                          return_value=([], None)):
            result = bluesky_links.run(self._config(), self._state())

        self.assertIsNone(result.bluesky_follows)


class BlueskySyncFollowsTests(unittest.TestCase):
    def _config(self, enabled=True):
        return BlueskySource(
            enabled=enabled,
            credential_location=Path('/tmp/bsky.json'),
            timeline_limit=50,
        )

    def test_skips_when_disabled(self):
        with patch.object(bluesky_links, 'BlueskyAuth') as auth_cls:
            result = bluesky_links.sync_follows(self._config(enabled=False))

        auth_cls.assert_not_called()
        self.assertIsNone(result.bluesky_follows)

    def test_paginates_and_captures_a_complete_snapshot(self):
        client = Mock()
        client.me.did = 'did:self'
        auth_client = Mock()
        auth_client.get_client.return_value = client
        auth_client.identifier = 'self.bsky.social'

        pages = [
            {
                'follows': [
                    {'did': 'did:a', 'handle': 'a.bsky.social', 'displayName': 'A'},
                    {'did': 'did:b', 'handle': 'b.bsky.social', 'displayName': ''},
                ],
                'cursor': 'page-2',
            },
            {
                'follows': [{'did': 'did:c', 'handle': 'c.bsky.social', 'displayName': 'C'}],
                'cursor': None,
            },
        ]

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_call_get_follows',
                          side_effect=pages) as call_follows:
            result = bluesky_links.sync_follows(self._config())

        # Uses the resolved self DID as the actor, paginates by cursor.
        self.assertEqual(call_follows.call_args_list[0].args[1], 'did:self')
        self.assertEqual(call_follows.call_args_list[1].args[2], 'page-2')
        snapshot = result.bluesky_follows
        self.assertIsNotNone(snapshot)
        self.assertEqual(
            [(record.did, record.handle, record.display_name)
             for record in snapshot.records],
            [
                ('did:a', 'a.bsky.social', 'A'),
                ('did:b', 'b.bsky.social', ''),
                ('did:c', 'c.bsky.social', 'C'),
            ],
        )

    def test_an_account_following_nobody_yields_an_empty_snapshot(self):
        """Empty is a real observation and must clear the published list."""
        client = Mock()
        client.me.did = 'did:self'
        auth_client = Mock()
        auth_client.get_client.return_value = client

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_call_get_follows',
                          return_value={'follows': [], 'cursor': None}):
            result = bluesky_links.sync_follows(self._config())

        self.assertIsNotNone(result.bluesky_follows)
        self.assertEqual(result.bluesky_follows.records, ())
        self.assertFalse(result.is_empty)

    def test_failure_reports_no_snapshot_rather_than_an_empty_one(self):
        auth_client = Mock()
        auth_client.get_client.side_effect = RuntimeError('login failed')

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client):
            result = bluesky_links.sync_follows(self._config())

        # A transient failure must never be read as "follows nobody".
        self.assertIsNone(result.bluesky_follows)
        self.assertTrue(result.is_empty)

    def test_unresolvable_actor_reports_no_snapshot(self):
        client = Mock()
        client.me = None
        auth_client = Mock()
        auth_client.get_client.return_value = client
        auth_client.identifier = ''

        with patch.object(bluesky_links, 'BlueskyAuth', return_value=auth_client), \
             patch.object(bluesky_links, '_call_get_follows') as call_follows:
            result = bluesky_links.sync_follows(self._config())

        call_follows.assert_not_called()
        self.assertIsNone(result.bluesky_follows)


class DestinationIndependenceTests(unittest.TestCase):
    def test_module_holds_no_database_imports(self):
        source = Path(bluesky_links.__file__).read_text(encoding='utf-8')
        for forbidden in ('db_utils', 'db_setup', 'sqlite3', 'psycopg'):
            self.assertNotIn(forbidden, source,
                             f'{forbidden} must not appear in bluesky_links.py')


if __name__ == '__main__':
    unittest.main()

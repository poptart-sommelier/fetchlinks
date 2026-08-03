import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests

import mastodon_links
from config import MastodonInstance, MastodonSource
from pipeline.collection import CollectionResult
from pipeline.contract import CheckpointRecord, utc_now
from pipeline.state import CollectorState


def _instance(
    name='infosec',
    instance_url='https://infosec.exchange',
    credential_location='/tmp/mastodon.json',
    timeline='home',
    timeline_limit=40,
    enabled=True,
):
    return MastodonInstance(
        name=name,
        instance_url=instance_url,
        credential_location=Path(credential_location),
        timeline=timeline,
        timeline_limit=timeline_limit,
        enabled=enabled,
    )


def _status(
    status_id='11',
    url='https://example.com/article',
    card_url='https://card.example/story',
    created_at='2026-04-26T12:00:00.000Z',
    content=None,
):
    return {
        'id': status_id,
        'created_at': created_at,
        'url': f'https://infosec.exchange/@alice/{status_id}',
        'content': content or f'<p>Read <a href="{url}">the article</a></p>',
        'card': {'url': card_url} if card_url else None,
        'account': {
            'url': 'https://infosec.exchange/@alice',
            'display_name': 'Alice',
            'acct': 'alice',
        },
    }


class ParseStatusTests(unittest.TestCase):
    def test_parse_status_extracts_content_and_card_links(self):
        parsed = mastodon_links._parse_status(_status())

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.source, 'https://infosec.exchange/@alice')
        self.assertEqual(parsed.author, 'Alice')
        self.assertEqual(parsed.description, 'Read the article')
        self.assertEqual(parsed.direct_link, 'https://infosec.exchange/@alice/11')
        self.assertIn('https://example.com/article', parsed.urls)
        self.assertIn('https://card.example/story', parsed.urls)

    def test_parse_status_skips_when_no_links(self):
        status = _status(url='', card_url='')
        status['content'] = '<p>No links here</p>'
        status['card'] = None

        self.assertIsNone(mastodon_links._parse_status(status))

    def test_parse_status_skips_missing_created_at(self):
        status = _status()
        status.pop('created_at')

        self.assertIsNone(mastodon_links._parse_status(status))

    def test_truncated_anchor_text_does_not_add_fragment_url(self):
        status = _status(
            url='https://www.theregister.com/2026/04/23/claude_opus_47_auc_overzealous/?td=rt-3a',
            card_url='',
        )
        status['content'] = (
            '<p><a href="https://www.theregister.com/2026/04/23/claude_opus_47_auc_overzealous/?td=rt-3a">'
            'https://www. theregister.com/2026/04/23/cla ude_opus_47_auc_overzealous/?td=rt-3a'
            '</a> whoops</p>'
        )

        parsed = mastodon_links._parse_status(status)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.urls, ['https://www.theregister.com/2026/04/23/claude_opus_47_auc_overzealous/?td=rt-3a'])

    def test_bare_non_anchor_text_urls_are_still_extracted(self):
        status = _status(url='', card_url='')
        status['content'] = '<p>Read this https://example.com/from-text and enjoy</p>'
        status['card'] = None

        parsed = mastodon_links._parse_status(status)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.urls, ['https://example.com/from-text'])

    def test_tag_urls_are_ignored(self):
        status = _status(url='', card_url='')
        status['content'] = (
            '<p><a href="https://mastodon.social/tags/nopesauce">#nopesauce</a> '
            '<a href="https://infosec.exchange/tags/podcast">#podcast</a> '
            '<a href="https://example.com/article">article</a></p>'
        )

        parsed = mastodon_links._parse_status(status)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.urls, ['https://example.com/article'])

    def test_tag_card_url_is_ignored(self):
        status = _status(url='', card_url='https://infosec.exchange/tags/podcast')
        status['content'] = '<p>No article links here</p>'

        self.assertIsNone(mastodon_links._parse_status(status))


class FetchTimelinePageTests(unittest.TestCase):
    def test_extracts_next_max_id_from_link_header(self):
        link_header = (
            '<https://infosec.exchange/api/v1/timelines/home?max_id=8>; rel="next", '
            '<https://infosec.exchange/api/v1/timelines/home?min_id=12>; rel="prev"'
        )

        self.assertEqual(mastodon_links._next_max_id_from_link_header(link_header), '8')

    def test_builds_home_timeline_request_with_since_id_and_capped_limit(self):
        session = MagicMock(spec=requests.Session)
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = [_status()]
        response.headers = {
            'Link': '<https://infosec.exchange/api/v1/timelines/home?max_id=8>; rel="next"'
        }
        session.get.return_value = response
        instance_config = _instance(instance_url='https://infosec.exchange/', timeline_limit=999)

        statuses, next_max_id = mastodon_links._fetch_timeline_page(session, instance_config, '10')

        self.assertEqual(statuses, [_status()])
        self.assertEqual(next_max_id, '8')
        session.get.assert_called_once_with(
            'https://infosec.exchange/api/v1/timelines/home',
            params={'limit': mastodon_links.MAX_TIMELINE_LIMIT, 'since_id': '10'},
            timeout=mastodon_links.REQUEST_TIMEOUT_SECONDS,
        )

    def test_adds_max_id_when_fetching_older_page(self):
        session = MagicMock(spec=requests.Session)
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        response.headers = {}
        session.get.return_value = response
        instance_config = _instance()

        mastodon_links._fetch_timeline_page(session, instance_config, '10', '8')

        session.get.assert_called_once_with(
            'https://infosec.exchange/api/v1/timelines/home',
            params={'limit': mastodon_links.DEFAULT_TIMELINE_LIMIT, 'since_id': '10', 'max_id': '8'},
            timeout=mastodon_links.REQUEST_TIMEOUT_SECONDS,
        )

    def test_returns_empty_on_request_error(self):
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError('boom')
        instance_config = _instance()

        self.assertEqual(mastodon_links._fetch_timeline_page(session, instance_config, None), ([], None))

    def test_returns_empty_on_unexpected_payload_shape(self):
        session = MagicMock(spec=requests.Session)
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'not': 'a list'}
        response.headers = {}
        session.get.return_value = response
        instance_config = _instance()

        self.assertEqual(mastodon_links._fetch_timeline_page(session, instance_config, None), ([], None))


class FetchTimelinePagesTests(unittest.TestCase):
    def test_follows_next_max_id_until_no_more_pages(self):
        session = MagicMock(spec=requests.Session)
        instance_config = _instance()

        with patch.object(mastodon_links, '_fetch_timeline_page') as fetch_page:
            fetch_page.side_effect = [
                ([_status('12', 'https://example.com/newer')], '11'),
                ([_status('11', 'https://example.com/older')], None),
            ]
            statuses = mastodon_links._fetch_timeline_pages(session, instance_config, '10')

        self.assertEqual([status['id'] for status in statuses], ['12', '11'])
        self.assertEqual(fetch_page.call_args_list[0].args, (session, instance_config, '10', None))
        self.assertEqual(fetch_page.call_args_list[1].args, (session, instance_config, '10', '11'))

    def test_stops_at_max_pages(self):
        session = MagicMock(spec=requests.Session)
        instance_config = _instance()

        with patch.object(mastodon_links, 'MAX_PAGES', 2), \
             patch.object(mastodon_links, '_fetch_timeline_page') as fetch_page:
            fetch_page.side_effect = [
                ([_status('12', 'https://example.com/one')], '11'),
                ([_status('11', 'https://example.com/two')], '10'),
                ([_status('10', 'https://example.com/three')], None),
            ]
            statuses = mastodon_links._fetch_timeline_pages(session, instance_config, None)

        self.assertEqual([status['id'] for status in statuses], ['12', '11'])
        self.assertEqual(fetch_page.call_count, 2)


class RunInstanceTests(unittest.TestCase):
    @staticmethod
    def _state(last_seen_id=None, name='infosec'):
        state = CollectorState()
        if last_seen_id:
            state.set_checkpoint('mastodon', name, cursor=last_seen_id)
        return state

    def test_run_instance_collects_posts_and_checkpoints(self):
        instance_config = _instance(instance_url='https://infosec.exchange/',
                                    timeline_limit=40)
        auth_client = Mock()
        auth_client.headers = {'Authorization': 'Bearer token'}
        statuses = [
            _status('11', 'https://example.com/one', card_url='',
                    created_at='2999-01-01T00:00:00.000Z'),
            _status('12', 'https://example.com/two', card_url='',
                    created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(mastodon_links, 'MastodonAuth',
                          return_value=auth_client) as auth_cls, \
             patch.object(mastodon_links, '_fetch_timeline_pages',
                          return_value=statuses) as fetch_pages:
            result = mastodon_links._run_instance(
                instance_config, self._state(last_seen_id='10'),
            )

        auth_cls.assert_called_once_with(str(Path('/tmp/mastodon.json')))
        # Resumes from the checkpoint held in local collector state.
        self.assertEqual(fetch_pages.call_args.args[2], '10')
        self.assertEqual(len(result.posts), 2)
        self.assertEqual(result.posts[0].urls[0], 'https://example.com/one')
        self.assertEqual(len(result.checkpoints), 1)
        checkpoint = result.checkpoints[0]
        self.assertEqual(checkpoint.source_type, 'mastodon')
        self.assertEqual(checkpoint.source_key, 'infosec')
        self.assertEqual(checkpoint.cursor, '12')
        self.assertEqual(checkpoint.source_url, 'https://infosec.exchange')

    def test_run_instance_skips_disabled_instance(self):
        with patch.object(mastodon_links, 'MastodonAuth') as auth_cls:
            result = mastodon_links._run_instance(_instance(enabled=False),
                                                  CollectorState())

        self.assertTrue(result.is_empty)
        auth_cls.assert_not_called()

    def test_run_instance_checkpoints_the_highest_id_seen_not_kept(self):
        instance_config = _instance(instance_url='https://infosec.exchange/')
        auth_client = Mock()
        auth_client.headers = {}
        statuses = [
            _status('11', 'https://example.com/old',
                    created_at='2000-01-01T00:00:00.000Z'),
            _status('12', 'https://example.com/recent', card_url='',
                    created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_fetch_timeline_pages',
                          return_value=statuses):
            result = mastodon_links._run_instance(instance_config, self._state(),
                                                  max_post_age_months=3)

        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls[0], 'https://example.com/recent')
        self.assertEqual(result.checkpoints[0].cursor, '12')

    def test_run_instance_emits_no_checkpoint_when_nothing_was_returned(self):
        auth_client = Mock()
        auth_client.headers = {}

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_fetch_timeline_pages', return_value=[]):
            result = mastodon_links._run_instance(_instance(), self._state('10'))

        self.assertEqual(result.checkpoints, [])
        self.assertTrue(result.is_empty)

    def test_run_instance_filters_denied_host_keywords(self):
        instance_config = _instance(instance_url='https://infosec.exchange/')
        auth_client = Mock()
        auth_client.headers = {}
        statuses = [
            _status('11', 'https://www.businessinsider.com/story', card_url='',
                    created_at='2999-01-01T00:00:00.000Z'),
            _status('12', 'https://example.com/recent', card_url='',
                    created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_fetch_timeline_pages',
                          return_value=statuses):
            result = mastodon_links._run_instance(
                instance_config, self._state(),
                excluded_url_host_keywords=['insider'],
            )

        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls, ['https://example.com/recent'])

    def test_run_instance_filters_denied_url_or_description_keywords(self):
        instance_config = _instance(instance_url='https://infosec.exchange/')
        auth_client = Mock()
        auth_client.headers = {}
        statuses = [
            _status(
                '11',
                'https://example.com/story',
                card_url='',
                created_at='2999-01-01T00:00:00.000Z',
                content='<p>Politics <a href="https://example.com/story">story</a></p>',
            ),
            _status('12', 'https://example.com/recent', card_url='',
                    created_at='2999-01-01T00:00:00.000Z'),
        ]

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_fetch_timeline_pages',
                          return_value=statuses):
            result = mastodon_links._run_instance(
                instance_config, self._state(),
                excluded_url_or_description_keywords=['politics'],
            )

        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.posts[0].urls, ['https://example.com/recent'])


class RunTests(unittest.TestCase):
    def test_run_skips_when_disabled(self):
        with patch.object(mastodon_links, '_run_instance') as run_instance:
            result = mastodon_links.run(
                MastodonSource(enabled=False, instances=()), CollectorState(),
            )

        run_instance.assert_not_called()
        self.assertTrue(result.is_empty)

    def test_run_merges_every_instance_into_one_result(self):
        infosec = _instance(name='infosec')
        hachyderm = _instance(name='hachyderm', instance_url='https://hachyderm.io')
        config = MastodonSource(enabled=True, instances=(infosec, hachyderm))
        state = CollectorState()

        first = CollectionResult()
        first.add_checkpoints([CheckpointRecord(
            source_type='mastodon', source_key='infosec', cursor='11',
            observed_at=utc_now(),
        )])
        second = CollectionResult()
        second.add_checkpoints([CheckpointRecord(
            source_type='mastodon', source_key='hachyderm', cursor='22',
            observed_at=utc_now(),
        )])

        with patch.object(mastodon_links, '_run_instance',
                          side_effect=[first, second]) as run_instance:
            result = mastodon_links.run(config, state)

        self.assertEqual(run_instance.call_args_list[0].args,
                         (infosec, state, 3, [], []))
        self.assertEqual(run_instance.call_args_list[1].args,
                         (hachyderm, state, 3, [], []))
        self.assertEqual([cp.source_key for cp in result.checkpoints],
                         ['infosec', 'hachyderm'])


class SyncFollowsTests(unittest.TestCase):
    def test_skips_when_source_disabled(self):
        with patch.object(mastodon_links, 'MastodonAuth') as auth_cls:
            result = mastodon_links.sync_follows(
                MastodonSource(enabled=False, instances=(_instance(),)),
            )

        auth_cls.assert_not_called()
        self.assertEqual(result.mastodon_follows, {})

    def test_skips_disabled_instance(self):
        config = MastodonSource(enabled=True, instances=(_instance(enabled=False),))
        with patch.object(mastodon_links, 'MastodonAuth') as auth_cls:
            result = mastodon_links.sync_follows(config)

        auth_cls.assert_not_called()
        self.assertEqual(result.mastodon_follows, {})

    def test_resolves_account_then_captures_a_scoped_snapshot(self):
        instance_config = _instance(name='infosec',
                                    instance_url='https://infosec.exchange/')
        config = MastodonSource(enabled=True, instances=(instance_config,))
        auth_client = Mock()
        auth_client.headers = {}
        accounts = [
            {'id': '1', 'acct': 'abe', 'display_name': 'Abe',
             'url': 'https://infosec.exchange/@abe'},
            {'id': '2', 'acct': 'cleo', 'display_name': '',
             'url': 'https://infosec.exchange/@cleo'},
        ]

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_verify_credentials_account_id',
                          return_value='99') as verify, \
             patch.object(mastodon_links, '_fetch_following_pages',
                          return_value=accounts) as fetch_following:
            result = mastodon_links.sync_follows(config)

        verify.assert_called_once()
        self.assertEqual(fetch_following.call_args.args[2], '99')
        # Each instance is its own snapshot scope.
        self.assertEqual(list(result.mastodon_follows), ['infosec'])
        snapshot = result.mastodon_follows['infosec']
        self.assertEqual(
            [(record.account_id, record.acct, record.display_name, record.url)
             for record in snapshot.records],
            [
                ('1', 'abe', 'Abe', 'https://infosec.exchange/@abe'),
                ('2', 'cleo', '', 'https://infosec.exchange/@cleo'),
            ],
        )

    def test_skips_instance_when_account_id_unresolved(self):
        config = MastodonSource(enabled=True, instances=(_instance(),))
        auth_client = Mock()
        auth_client.headers = {}

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_verify_credentials_account_id',
                          return_value=None), \
             patch.object(mastodon_links, '_fetch_following_pages') as fetch_following:
            result = mastodon_links.sync_follows(config)

        fetch_following.assert_not_called()
        self.assertEqual(result.mastodon_follows, {})

    def test_failure_for_one_instance_does_not_abort_others(self):
        good = _instance(name='infosec')
        bad = _instance(name='hachyderm', instance_url='https://hachyderm.io')
        config = MastodonSource(enabled=True, instances=(bad, good))
        auth_client = Mock()
        auth_client.headers = {}

        def verify(session, instance_url):
            if 'hachyderm' in instance_url:
                raise RuntimeError('boom')
            return '5'

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links, '_verify_credentials_account_id',
                          side_effect=verify), \
             patch.object(mastodon_links, '_fetch_following_pages', return_value=[]):
            result = mastodon_links.sync_follows(config)

        # infosec still produced a snapshot; the failed instance produced none,
        # so the publisher leaves its existing list untouched.
        self.assertEqual(list(result.mastodon_follows), ['infosec'])


class DestinationIndependenceTests(unittest.TestCase):
    def test_module_holds_no_database_imports(self):
        source = Path(mastodon_links.__file__).read_text(encoding='utf-8')
        for forbidden in ('db_utils', 'db_setup', 'sqlite3', 'psycopg'):
            self.assertNotIn(forbidden, source,
                             f'{forbidden} must not appear in mastodon_links.py')


if __name__ == '__main__':
    unittest.main()

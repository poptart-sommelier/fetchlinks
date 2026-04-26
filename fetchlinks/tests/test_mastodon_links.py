import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests

import mastodon_links


def _status(
    status_id='11',
    url='https://example.com/article',
    card_url='https://card.example/story',
    created_at='2026-04-26T12:00:00.000Z',
):
    return {
        'id': status_id,
        'created_at': created_at,
        'url': f'https://infosec.exchange/@alice/{status_id}',
        'content': f'<p>Read <a href="{url}">the article</a></p>',
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
        instance_config = {
            'name': 'infosec',
            'instance_url': 'https://infosec.exchange/',
            'timeline_limit': 999,
        }

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
        instance_config = {'name': 'infosec', 'instance_url': 'https://infosec.exchange'}

        mastodon_links._fetch_timeline_page(session, instance_config, '10', '8')

        session.get.assert_called_once_with(
            'https://infosec.exchange/api/v1/timelines/home',
            params={'limit': mastodon_links.DEFAULT_TIMELINE_LIMIT, 'since_id': '10', 'max_id': '8'},
            timeout=mastodon_links.REQUEST_TIMEOUT_SECONDS,
        )

    def test_returns_empty_on_request_error(self):
        session = MagicMock(spec=requests.Session)
        session.get.side_effect = requests.ConnectionError('boom')
        instance_config = {'name': 'infosec', 'instance_url': 'https://infosec.exchange'}

        self.assertEqual(mastodon_links._fetch_timeline_page(session, instance_config, None), ([], None))

    def test_returns_empty_on_unexpected_payload_shape(self):
        session = MagicMock(spec=requests.Session)
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'not': 'a list'}
        response.headers = {}
        session.get.return_value = response
        instance_config = {'name': 'infosec', 'instance_url': 'https://infosec.exchange'}

        self.assertEqual(mastodon_links._fetch_timeline_page(session, instance_config, None), ([], None))


class FetchTimelinePagesTests(unittest.TestCase):
    def test_follows_next_max_id_until_no_more_pages(self):
        session = MagicMock(spec=requests.Session)
        instance_config = {'name': 'infosec', 'instance_url': 'https://infosec.exchange'}

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
        instance_config = {'name': 'infosec', 'instance_url': 'https://infosec.exchange'}

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
    def test_run_instance_fetches_inserts_and_persists_state(self):
        instance_config = {
            'name': 'infosec',
            'instance_url': 'https://infosec.exchange/',
            'credential_location': '/tmp/mastodon.json',
            'timeline_limit': 40,
        }
        auth_client = Mock()
        auth_client.headers = {'Authorization': 'Bearer tok'}
        statuses = [_status('11', 'https://example.com/one'), _status('12', 'https://example.com/two')]
        db_path = Path('/tmp/db/fetchlinks.db')

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client) as auth_cls, \
             patch.object(mastodon_links.db_utils, 'db_get_mastodon_last_seen_id', return_value='10') as get_state, \
               patch.object(mastodon_links, '_fetch_timeline_pages', return_value=statuses) as fetch_pages, \
             patch.object(mastodon_links.db_utils, 'db_insert', return_value=2) as db_insert, \
             patch.object(mastodon_links.db_utils, 'db_set_mastodon_last_seen_id') as set_state:
            inserted = mastodon_links._run_instance(instance_config, db_path)

        self.assertEqual(inserted, 2)
        auth_cls.assert_called_once_with('/tmp/mastodon.json')
        get_state.assert_called_once_with('infosec', db_path)
        fetch_pages.assert_called_once()
        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(len(inserted_posts), 2)
        self.assertEqual(inserted_posts[0].urls[0], 'https://example.com/one')
        db_insert.assert_called_once_with(inserted_posts, db_path)
        set_state.assert_called_once_with('infosec', 'https://infosec.exchange', '12', db_path)

    def test_run_instance_skips_disabled_instance(self):
        with patch.object(mastodon_links, 'MastodonAuth') as auth_cls:
            inserted = mastodon_links._run_instance({'enabled': False, 'name': 'infosec'}, Path('/tmp/db'))

        self.assertEqual(inserted, 0)
        auth_cls.assert_not_called()

    def test_run_instance_filters_old_posts_before_insert_but_persists_state(self):
        instance_config = {
            'name': 'infosec',
            'instance_url': 'https://infosec.exchange/',
            'credential_location': '/tmp/mastodon.json',
        }
        auth_client = Mock()
        auth_client.headers = {'Authorization': 'Bearer tok'}
        statuses = [
            _status('11', 'https://example.com/old', created_at='2000-01-01T00:00:00.000Z'),
            _status('12', 'https://example.com/recent', created_at='2999-01-01T00:00:00.000Z'),
        ]
        db_path = Path('/tmp/db/fetchlinks.db')

        with patch.object(mastodon_links, 'MastodonAuth', return_value=auth_client), \
             patch.object(mastodon_links.db_utils, 'db_get_mastodon_last_seen_id', return_value='10'), \
             patch.object(mastodon_links, '_fetch_timeline_pages', return_value=statuses), \
             patch.object(mastodon_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(mastodon_links.db_utils, 'db_set_mastodon_last_seen_id') as set_state:
            inserted = mastodon_links._run_instance(instance_config, db_path, max_post_age_months=3)

        self.assertEqual(inserted, 1)
        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(len(inserted_posts), 1)
        self.assertEqual(inserted_posts[0].urls[0], 'https://example.com/recent')
        set_state.assert_called_once_with('infosec', 'https://infosec.exchange', '12', db_path)


class RunTests(unittest.TestCase):
    def test_run_skips_when_disabled(self):
        with patch.object(mastodon_links, '_run_instance') as run_instance:
            mastodon_links.run({'enabled': False}, {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'})

        run_instance.assert_not_called()

    def test_run_processes_each_instance(self):
        config = {
            'enabled': True,
            'instances': [
                {'name': 'infosec'},
                {'name': 'hachyderm'},
            ],
        }
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}

        with patch.object(mastodon_links, '_run_instance', side_effect=[1, 2]) as run_instance:
            mastodon_links.run(config, db_info)

        db_path = Path('/tmp/db') / 'fetchlinks.db'
        self.assertEqual(run_instance.call_args_list[0].args, ({'name': 'infosec'}, db_path, 3))
        self.assertEqual(run_instance.call_args_list[1].args, ({'name': 'hachyderm'}, db_path, 3))


if __name__ == '__main__':
    unittest.main()
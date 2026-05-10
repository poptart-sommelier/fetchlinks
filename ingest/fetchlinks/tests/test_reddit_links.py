import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import reddit_links
from utils import Post
from utils import RedditPost


def _make_reddit_post(url, name='t3_abc', post_id='abc', created_utc=4102444800):
    return {
        'data': {
            'id': post_id,
            'name': name,
            'subreddit_name_prefixed': 'r/netsec',
            'author': 'someone',
            'title': 'a post',
            'permalink': f'/r/netsec/comments/{post_id}/a_post/',
            'created_utc': created_utc,
            'url': url,
        }
    }


def _make_response(children, after=None):
    mock_response = Mock()
    mock_response.headers = {}
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        'data': {
            'children': children,
            'after': after,
        }
    }
    return mock_response


class RedditLinksTests(unittest.TestCase):
    def test_get_subreddit_returns_empty_on_request_error(self):
        session = Mock()
        session.get.side_effect = reddit_links.requests.exceptions.Timeout('timeout')

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', None)

        self.assertEqual(posts, [])
        self.assertIsNone(newest_fullname)

    def test_get_subreddit_returns_children_on_success(self):
        session = Mock()
        session.get.return_value = _make_response([_make_reddit_post('https://example.com', name='t3_new')])

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', None)

        self.assertEqual(len(posts), 1)
        self.assertEqual(newest_fullname, 't3_new')
        session.get.assert_called_once_with(
            url='https://oauth.reddit.com/r/netsec/new/.json',
            params={'show': 'all', 'limit': 100, 'raw_json': 1},
            timeout=reddit_links.REQUEST_TIMEOUT_SECONDS,
        )

    def test_get_subreddit_stops_at_previous_fullname(self):
        session = Mock()
        session.get.return_value = _make_response([
            _make_reddit_post('https://example.com/new', name='t3_new', post_id='new'),
            _make_reddit_post('https://example.com/seen', name='t3_seen', post_id='seen'),
            _make_reddit_post('https://example.com/old', name='t3_old', post_id='old'),
        ], after='t3_next')

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', 't3_seen')

        self.assertEqual([post['data']['name'] for post in posts], ['t3_new'])
        self.assertEqual(newest_fullname, 't3_new')
        session.get.assert_called_once()

    def test_get_subreddit_paginates_until_previous_fullname(self):
        session = Mock()
        session.get.side_effect = [
            _make_response([
                _make_reddit_post('https://example.com/new1', name='t3_new1', post_id='new1'),
                _make_reddit_post('https://example.com/new2', name='t3_new2', post_id='new2'),
            ], after='t3_page2'),
            _make_response([
                _make_reddit_post('https://example.com/new3', name='t3_new3', post_id='new3'),
                _make_reddit_post('https://example.com/seen', name='t3_seen', post_id='seen'),
            ]),
        ]

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', 't3_seen')

        self.assertEqual([post['data']['name'] for post in posts], ['t3_new1', 't3_new2', 't3_new3'])
        self.assertEqual(newest_fullname, 't3_new1')
        self.assertEqual(session.get.call_args_list[1].kwargs['params']['after'], 't3_page2')

    def test_get_subreddit_returns_empty_on_invalid_json(self):
        session = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError('not json')
        session.get.return_value = mock_response

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', None)

        self.assertEqual(posts, [])
        self.assertIsNone(newest_fullname)

    def test_get_subreddit_returns_empty_on_http_error(self):
        session = Mock()
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = reddit_links.requests.exceptions.HTTPError('bad')
        session.get.return_value = mock_response

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', None)

        self.assertEqual(posts, [])
        self.assertIsNone(newest_fullname)

    def test_get_subreddit_returns_empty_when_children_not_list(self):
        session = Mock()
        mock_response = Mock()
        mock_response.headers = {}
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'data': {'children': {'unexpected': 'shape'}}}
        session.get.return_value = mock_response

        posts, newest_fullname = reddit_links.get_subreddit(session, 'netsec', None)

        self.assertEqual(posts, [])
        self.assertIsNone(newest_fullname)

    def test_get_subreddits_uses_stored_state_and_session_headers(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['Netsec']}
        db_path = Path('/tmp/db/fetchlinks.db')

        with patch.object(reddit_links.db_utils, 'db_get_reddit_states', return_value={'netsec': 't3_seen'}), \
             patch.object(reddit_links, 'RedditAuth') as auth_class, \
             patch.object(reddit_links.requests, 'Session') as session_class, \
             patch.object(
                 reddit_links,
                 'get_subreddit',
                 return_value=([_make_reddit_post('https://example.com')], 't3_new'),
             ) as get_subreddit:
            auth_class.return_value.get_auth.return_value = 'token'
            auth_class.return_value.user_agent = 'test-ua/0.1'
            session = session_class.return_value.__enter__.return_value

            posts, state_updates = reddit_links.get_subreddits(reddit_config, db_path)

        self.assertEqual(len(posts), 1)
        self.assertEqual(state_updates, [('netsec', 't3_new')])
        session.headers.update.assert_called_once_with({'Authorization': 'Bearer token', 'User-Agent': 'test-ua/0.1'})
        get_subreddit.assert_called_once_with(session, 'netsec', 't3_seen', limit=100, max_pages=5)


class RedditPostExtractUrlsTests(unittest.TestCase):
    def test_skips_relative_subreddit_path(self):
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
    def test_run_does_not_insert_when_no_posts_parse_but_persists_state(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        state_updates = [('netsec', 't3_new')]

        with patch.object(
            reddit_links,
            'get_subreddits',
            return_value=([_make_reddit_post('https://example.com')], state_updates),
        ) as get_subreddits, \
             patch.object(reddit_links, 'parse_posts', return_value=[]) as parse_posts, \
             patch.object(reddit_links.db_utils, 'db_insert') as db_insert, \
             patch.object(reddit_links.db_utils, 'db_set_reddit_states') as set_states:
            reddit_links.run(reddit_config, db_info)

        db_path = reddit_links.Path('/tmp/db') / 'fetchlinks.db'
        get_subreddits.assert_called_once_with(reddit_config, db_path)
        parse_posts.assert_called_once()
        db_insert.assert_not_called()
        set_states.assert_called_once_with(state_updates, db_path)

    def test_run_inserts_parsed_posts_and_persists_state(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        parsed_posts = [RedditPost(_make_reddit_post('https://example.com/article'))]
        state_updates = [('netsec', 't3_new')]

        with patch.object(
            reddit_links,
            'get_subreddits',
            return_value=([_make_reddit_post('https://example.com/article')], state_updates),
        ), \
             patch.object(reddit_links, 'parse_posts', return_value=parsed_posts), \
             patch.object(reddit_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(reddit_links.db_utils, 'db_set_reddit_states') as set_states:
            reddit_links.run(reddit_config, db_info)

        db_path = reddit_links.Path('/tmp/db') / 'fetchlinks.db'
        db_insert.assert_called_once_with(parsed_posts, db_path)
        set_states.assert_called_once_with(state_updates, db_path)

    def test_run_filters_old_posts_before_insert_but_persists_state(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        old_post = RedditPost(_make_reddit_post('https://example.com/old', created_utc=946684800))
        recent_post = RedditPost(_make_reddit_post('https://example.com/recent', created_utc=4102444800))
        state_updates = [('netsec', 't3_new')]

        with patch.object(
            reddit_links,
            'get_subreddits',
            return_value=([_make_reddit_post('https://example.com/recent')], state_updates),
        ), \
             patch.object(reddit_links, 'parse_posts', return_value=[old_post, recent_post]), \
             patch.object(reddit_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(reddit_links.db_utils, 'db_set_reddit_states') as set_states:
            reddit_links.run(reddit_config, db_info, max_post_age_months=3)

        db_path = reddit_links.Path('/tmp/db') / 'fetchlinks.db'
        db_insert.assert_called_once_with([recent_post], db_path)
        set_states.assert_called_once_with(state_updates, db_path)

    def test_run_filters_denied_host_keywords_before_insert(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        state_updates = [('netsec', 't3_new')]
        post = Post()
        post.date_created = '2999-01-01 00:00:00'
        post.add_url('https://www.businessinsider.com/story')
        post.add_url('https://example.com/allowed')
        post._generate_unique_url_string()

        with patch.object(reddit_links, 'get_subreddits', return_value=([], state_updates)), \
             patch.object(reddit_links, 'parse_posts', return_value=[post]), \
             patch.object(reddit_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(reddit_links.db_utils, 'db_set_reddit_states'):
            reddit_links.run(reddit_config, db_info, excluded_url_host_keywords=['insider'])

        inserted_posts = db_insert.call_args.args[0]
        self.assertEqual(inserted_posts[0].urls, ['https://example.com/allowed'])

    def test_run_filters_denied_url_or_description_keywords_before_insert(self):
        reddit_config = {'credential_location': '/tmp/reddit.json', 'subreddits': ['netsec']}
        db_info = {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'}
        state_updates = [('netsec', 't3_new')]
        blocked = Post()
        blocked.date_created = '2999-01-01 00:00:00'
        blocked.description = 'Politics story'
        blocked.add_url('https://example.com/story')
        blocked._generate_unique_url_string()
        allowed = Post()
        allowed.date_created = '2999-01-01 00:00:00'
        allowed.description = 'Technology story'
        allowed.add_url('https://example.com/allowed')
        allowed._generate_unique_url_string()

        with patch.object(reddit_links, 'get_subreddits', return_value=([], state_updates)), \
             patch.object(reddit_links, 'parse_posts', return_value=[blocked, allowed]), \
             patch.object(reddit_links.db_utils, 'db_insert', return_value=1) as db_insert, \
             patch.object(reddit_links.db_utils, 'db_set_reddit_states'):
            reddit_links.run(
                reddit_config,
                db_info,
                excluded_url_or_description_keywords=['politics'],
            )

        db_insert.assert_called_once_with([allowed], reddit_links.Path('/tmp/db') / 'fetchlinks.db')


if __name__ == '__main__':
    unittest.main()

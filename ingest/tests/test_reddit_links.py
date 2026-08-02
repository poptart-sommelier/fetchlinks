import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import reddit_links
from config import RedditSource
from pipeline.catalog import build_catalog
from pipeline.state import CollectorState
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

    def test_get_subreddits_uses_catalog_and_state(self):
        reddit_config = RedditSource(
            enabled=True,
            credential_location=Path('/tmp/reddit.json'),
            subreddits=('Netsec',),
        )
        catalog = build_catalog(subreddit_pairs=[('netsec', 'Netsec')])
        state = CollectorState()
        state.set_checkpoint('reddit', 'netsec', cursor='t3_seen')

        with patch.object(reddit_links, 'RedditAuth') as auth_class, \
             patch.object(reddit_links.requests, 'Session') as session_class, \
             patch.object(
                 reddit_links,
                 'get_subreddit',
                 return_value=([_make_reddit_post('https://example.com')], 't3_new'),
             ) as get_subreddit:
            auth_class.return_value.get_auth.return_value = 'token'
            auth_class.return_value.user_agent = 'test-ua/0.1'
            session = session_class.return_value.__enter__.return_value

            posts, state_updates = reddit_links.get_subreddits(
                reddit_config, catalog, state,
            )

        self.assertEqual(len(posts), 1)
        self.assertEqual(state_updates, [('netsec', 't3_new')])
        session.headers.update.assert_called_once()
        get_subreddit.assert_called_once_with(
            session, 'netsec', 't3_seen', limit=100, max_pages=5,
        )

    def test_get_subreddits_reads_only_the_catalog(self):
        """A subreddit configured but not catalogued must not be fetched."""
        reddit_config = RedditSource(
            enabled=True,
            credential_location=Path('/tmp/reddit.json'),
            subreddits=('netsec', 'notcatalogued'),
        )
        catalog = build_catalog(subreddit_pairs=[('netsec', 'netsec')])

        with patch.object(reddit_links, 'RedditAuth') as auth_class, \
             patch.object(reddit_links.requests, 'Session'), \
             patch.object(reddit_links, 'get_subreddit',
                          return_value=([], None)) as get_subreddit:
            auth_class.return_value.get_auth.return_value = 'token'
            auth_class.return_value.user_agent = 'test-ua/0.1'
            reddit_links.get_subreddits(reddit_config, catalog, CollectorState())

        self.assertEqual(get_subreddit.call_count, 1)
        self.assertEqual(get_subreddit.call_args.args[1], 'netsec')



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
    def setUp(self):
        self.reddit_config = RedditSource(
            enabled=True,
            credential_location=Path('/tmp/reddit.json'),
            subreddits=('netsec',),
        )
        self.catalog = build_catalog(subreddit_pairs=[('netsec', 'netsec')])
        self.state = CollectorState()

    @staticmethod
    def _post(url, description=''):
        post = Post()
        post.source = 'r/netsec'
        post.source_type = 'reddit'
        post.date_created = '2999-01-01 00:00:00'
        post.description = description
        post.add_url(url)
        post._generate_unique_url_string()
        return post

    def test_run_checkpoints_even_when_no_posts_parse(self):
        state_updates = [('netsec', 't3_new')]

        with patch.object(
            reddit_links,
            'get_subreddits',
            return_value=([_make_reddit_post('https://example.com')], state_updates),
        ) as get_subreddits, \
             patch.object(reddit_links, 'parse_posts', return_value=[]) as parse_posts:
            result = reddit_links.run(self.reddit_config, self.catalog, self.state)

        get_subreddits.assert_called_once_with(
            self.reddit_config, self.catalog, self.state,
        )
        parse_posts.assert_called_once()
        self.assertEqual(result.posts, [])
        self.assertEqual(len(result.checkpoints), 1)
        checkpoint = result.checkpoints[0]
        self.assertEqual(checkpoint.source_type, 'reddit')
        self.assertEqual(checkpoint.source_key, 'netsec')
        self.assertEqual(checkpoint.cursor, 't3_new')
        self.assertEqual(checkpoint.source_url, 'https://www.reddit.com/r/netsec')

    def test_run_returns_parsed_posts_and_checkpoints(self):
        parsed = [RedditPost(_make_reddit_post('https://example.com/article'))]

        with patch.object(
            reddit_links,
            'get_subreddits',
            return_value=([], [('netsec', 't3_new')]),
        ), \
             patch.object(reddit_links, 'parse_posts', return_value=parsed):
            result = reddit_links.run(self.reddit_config, self.catalog, self.state)

        self.assertEqual([record.unique_id for record in result.posts],
                         [parsed[0].unique_id_string])
        self.assertEqual(len(result.checkpoints), 1)

    def test_run_checkpoints_the_newest_post_seen_not_the_newest_kept(self):
        """Filtered-out posts must not be re-read on the next cycle."""
        old_post = RedditPost(
            _make_reddit_post('https://example.com/old', created_utc=946684800),
        )
        recent_post = RedditPost(
            _make_reddit_post('https://example.com/recent', created_utc=4102444800),
        )

        with patch.object(
            reddit_links,
            'get_subreddits',
            return_value=([], [('netsec', 't3_new')]),
        ), \
             patch.object(reddit_links, 'parse_posts',
                          return_value=[old_post, recent_post]):
            result = reddit_links.run(self.reddit_config, self.catalog, self.state,
                                      max_post_age_months=3)

        self.assertEqual([record.unique_id for record in result.posts],
                         [recent_post.unique_id_string])
        self.assertEqual(result.checkpoints[0].cursor, 't3_new')

    def test_run_filters_denied_host_keywords(self):
        post = Post()
        post.date_created = '2999-01-01 00:00:00'
        post.add_url('https://www.businessinsider.com/story')
        post.add_url('https://example.com/allowed')
        post._generate_unique_url_string()

        with patch.object(reddit_links, 'get_subreddits', return_value=([], [])), \
             patch.object(reddit_links, 'parse_posts', return_value=[post]):
            result = reddit_links.run(self.reddit_config, self.catalog, self.state,
                                      excluded_url_host_keywords=['insider'])

        self.assertEqual(result.posts[0].urls, ['https://example.com/allowed'])

    def test_run_filters_denied_url_or_description_keywords(self):
        blocked = self._post('https://example.com/story', description='Politics story')
        allowed = self._post('https://example.com/allowed', description='Technology story')

        with patch.object(reddit_links, 'get_subreddits', return_value=([], [])), \
             patch.object(reddit_links, 'parse_posts',
                          return_value=[blocked, allowed]):
            result = reddit_links.run(
                self.reddit_config, self.catalog, self.state,
                excluded_url_or_description_keywords=['politics'],
            )

        self.assertEqual([record.unique_id for record in result.posts],
                         [allowed.unique_id_string])

    def test_run_produces_no_rss_observations_or_follows(self):
        with patch.object(reddit_links, 'get_subreddits', return_value=([], [])), \
             patch.object(reddit_links, 'parse_posts', return_value=[]):
            result = reddit_links.run(self.reddit_config, self.catalog, self.state)

        self.assertEqual(result.rss_observations, [])
        self.assertIsNone(result.bluesky_follows)
        self.assertEqual(result.mastodon_follows, {})
        self.assertTrue(result.is_empty)


class DestinationIndependenceTests(unittest.TestCase):
    def test_module_holds_no_database_imports(self):
        source = Path(reddit_links.__file__).read_text(encoding='utf-8')
        for forbidden in ('db_utils', 'db_setup', 'sqlite3', 'psycopg'):
            self.assertNotIn(forbidden, source,
                             f'{forbidden} must not appear in reddit_links.py')


if __name__ == '__main__':
    unittest.main()

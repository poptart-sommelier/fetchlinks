import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import auth


class AuthBaseTests(unittest.TestCase):
    def test_no_file_means_no_contents(self):
        a = auth.Auth('')
        self.assertEqual(a.file_contents, {})

    def test_missing_file_raises_runtime_error(self):
        with self.assertRaises(RuntimeError):
            auth.Auth('/no/such/file.json')

    def test_invalid_json_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'bad.json'
            p.write_text('not json', encoding='utf-8')
            with self.assertRaises(ValueError):
                auth.Auth(str(p))

    def test_valid_json_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'good.json'
            p.write_text(json.dumps({'k': 'v'}), encoding='utf-8')
            a = auth.Auth(str(p))
            self.assertEqual(a.file_contents, {'k': 'v'})


def _write_reddit_creds(tmp, contents):
    p = Path(tmp) / 'reddit.json'
    p.write_text(json.dumps(contents), encoding='utf-8')
    return str(p)


class RedditAuthTests(unittest.TestCase):
    def test_missing_keys_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_reddit_creds(tmp, {'reddit': {}})
            with self.assertRaises(ValueError):
                auth.RedditAuth(path)

    def test_sets_secrets_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_reddit_creds(tmp, {
                'reddit': {
                    'APP_CLIENT_ID': 'cid',
                    'APP_CLIENT_SECRET': 'csec',
                    'USERNAME': 'me',
                }
            })
            a = auth.RedditAuth(path)
            self.assertEqual(a.app_client_id, 'cid')
            self.assertEqual(a.app_client_secret, 'csec')
            self.assertEqual(a.username, 'me')

    def test_user_agent_with_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_reddit_creds(tmp, {
                'reddit': {'APP_CLIENT_ID': 'c', 'APP_CLIENT_SECRET': 's', 'USERNAME': 'me'}
            })
            self.assertIn('/u/me', auth.RedditAuth(path).user_agent)

    def test_user_agent_without_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_reddit_creds(tmp, {
                'reddit': {'APP_CLIENT_ID': 'c', 'APP_CLIENT_SECRET': 's'}
            })
            self.assertNotIn('/u/', auth.RedditAuth(path).user_agent)

    def test_get_auth_returns_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_reddit_creds(tmp, {
                'reddit': {'APP_CLIENT_ID': 'c', 'APP_CLIENT_SECRET': 's'}
            })
            a = auth.RedditAuth(path)

            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {'access_token': 'tok'}

            with patch('auth.requests.post', return_value=mock_resp) as post:
                self.assertEqual(a.get_auth(), 'tok')

            post.assert_called_once_with(
                a.reddit_auth_api_url,
                headers={'User-Agent': a.user_agent},
                data={'grant_type': 'client_credentials'},
                auth=('c', 's'),
                timeout=20,
            )
            self.assertEqual(a.access_token, 'tok')

    def test_get_auth_missing_token_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_reddit_creds(tmp, {
                'reddit': {'APP_CLIENT_ID': 'c', 'APP_CLIENT_SECRET': 's'}
            })
            a = auth.RedditAuth(path)

            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {}

            with patch('auth.requests.post', return_value=mock_resp):
                with self.assertRaises(ValueError):
                    a.get_auth()


def _write_bluesky_creds(tmp, contents):
    p = Path(tmp) / 'bluesky.json'
    p.write_text(json.dumps(contents), encoding='utf-8')
    return str(p)


class BlueskyAuthTests(unittest.TestCase):
    def test_missing_keys_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_bluesky_creds(tmp, {'bluesky': {}})
            with self.assertRaises(ValueError):
                auth.BlueskyAuth(path)

    def test_get_client_raises_when_atproto_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_bluesky_creds(tmp, {
                'bluesky': {'IDENTIFIER': 'me', 'APP_PASSWORD': 'pw'}
            })
            a = auth.BlueskyAuth(path)
            with patch('auth.AtprotoClient', None):
                with self.assertRaises(RuntimeError):
                    a.get_client()

    def test_get_client_logs_in_with_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_bluesky_creds(tmp, {
                'bluesky': {'IDENTIFIER': 'me', 'APP_PASSWORD': 'pw'}
            })
            a = auth.BlueskyAuth(path)

            fake_client = MagicMock()
            with patch('auth.AtprotoClient', return_value=fake_client) as ctor:
                returned = a.get_client()

            ctor.assert_called_once_with()
            fake_client.login.assert_called_once_with('me', 'pw')
            self.assertIs(returned, fake_client)


def _write_mastodon_creds(tmp, contents):
    p = Path(tmp) / 'mastodon.json'
    p.write_text(json.dumps(contents), encoding='utf-8')
    return str(p)


class MastodonAuthTests(unittest.TestCase):
    def test_missing_keys_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_mastodon_creds(tmp, {'mastodon': {}})
            with self.assertRaises(ValueError):
                auth.MastodonAuth(path)

    def test_empty_token_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_mastodon_creds(tmp, {'mastodon': {'ACCESS_TOKEN': ''}})
            with self.assertRaises(ValueError):
                auth.MastodonAuth(path)

    def test_headers_include_bearer_token_and_user_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_mastodon_creds(tmp, {'mastodon': {'ACCESS_TOKEN': 'tok'}})
            a = auth.MastodonAuth(path)

        self.assertEqual(a.headers['Authorization'], 'Bearer tok')
        self.assertIn('fetchlinks-mastodon', a.headers['User-Agent'])


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest import mock

import unshorten_links


class IsShortenedTests(unittest.TestCase):
    def test_known_shortener_matches(self):
        self.assertTrue(unshorten_links.is_shortened('https://bit.ly/abc'))
        self.assertTrue(unshorten_links.is_shortened('https://t.co/xyz'))
        self.assertTrue(unshorten_links.is_shortened('https://tinyurl.com/foo'))

    def test_subdomain_of_shortener_matches(self):
        self.assertTrue(unshorten_links.is_shortened('https://foo.bit.ly/abc'))

    def test_unknown_domain_does_not_match(self):
        self.assertFalse(unshorten_links.is_shortened('https://example.com/page'))
        self.assertFalse(unshorten_links.is_shortened('https://en.wikipedia.org/wiki/X'))

    def test_invalid_url_returns_false(self):
        self.assertFalse(unshorten_links.is_shortened(''))
        self.assertFalse(unshorten_links.is_shortened('not a url'))


class IsSafeTargetTests(unittest.TestCase):
    def _patch_resolve(self, ip_str):
        # getaddrinfo returns list of 5-tuples; index [4][0] is the IP string.
        return mock.patch.object(
            unshorten_links.socket,
            'getaddrinfo',
            return_value=[(None, None, None, '', (ip_str, 0))],
        )

    def test_rejects_non_http_scheme(self):
        self.assertFalse(unshorten_links._is_safe_target('ftp://example.com/x'))
        self.assertFalse(unshorten_links._is_safe_target('file:///etc/passwd'))
        self.assertFalse(unshorten_links._is_safe_target('javascript:alert(1)'))

    def test_rejects_loopback(self):
        with self._patch_resolve('127.0.0.1'):
            self.assertFalse(unshorten_links._is_safe_target('http://localhost/x'))

    def test_rejects_private_ranges(self):
        for ip in ('10.0.0.1', '192.168.1.1', '172.16.0.1'):
            with self._patch_resolve(ip):
                self.assertFalse(unshorten_links._is_safe_target('http://internal/x'))

    def test_rejects_link_local_metadata(self):
        with self._patch_resolve('169.254.169.254'):
            self.assertFalse(unshorten_links._is_safe_target('http://metadata/x'))

    def test_rejects_unresolvable_host(self):
        with mock.patch.object(
            unshorten_links.socket,
            'getaddrinfo',
            side_effect=unshorten_links.socket.gaierror(),
        ):
            self.assertFalse(unshorten_links._is_safe_target('http://nope.invalid/x'))

    def test_accepts_public_ip(self):
        with self._patch_resolve('8.8.8.8'):
            self.assertTrue(unshorten_links._is_safe_target('https://example.com/x'))


class UnshortenUrlTests(unittest.TestCase):
    def test_follows_redirect_chain(self):
        responses = [
            mock.Mock(status_code=301, headers={'Location': 'https://final.example.com/page'}),
            mock.Mock(status_code=200, headers={}),
        ]

        def fake_head(self_session, url, **kwargs):
            return responses.pop(0)

        with mock.patch.object(unshorten_links, '_is_safe_target', return_value=True), \
             mock.patch('requests.Session.head', autospec=True, side_effect=fake_head):
            result = unshorten_links.unshorten_url('https://bit.ly/abc')

        self.assertEqual(result, 'https://final.example.com/page')

    def test_returns_none_when_redirect_lands_on_unsafe_target(self):
        responses = [
            mock.Mock(status_code=301, headers={'Location': 'http://169.254.169.254/'}),
        ]

        # Initial URL is safe, redirect target is not.
        def fake_safe(url):
            return '169.254' not in url

        def fake_head(self_session, url, **kwargs):
            return responses.pop(0)

        with mock.patch.object(unshorten_links, '_is_safe_target', side_effect=fake_safe), \
             mock.patch('requests.Session.head', autospec=True, side_effect=fake_head):
            result = unshorten_links.unshorten_url('https://bit.ly/abc')

        self.assertIsNone(result)

    def test_returns_none_when_exceeds_redirect_limit(self):
        # Always redirects, never resolves.
        infinite = mock.Mock(status_code=301, headers={'Location': 'https://bit.ly/loop'})

        with mock.patch.object(unshorten_links, '_is_safe_target', return_value=True), \
             mock.patch('requests.Session.head', autospec=True, return_value=infinite):
            result = unshorten_links.unshorten_url('https://bit.ly/loop')

        self.assertIsNone(result)


class UnshortenUrlsTests(unittest.TestCase):
    def test_skips_non_shortened(self):
        # No shortened URLs in input → no work done, empty dict.
        result = unshorten_links.unshorten_urls(['https://example.com/a', 'https://wikipedia.org/x'])
        self.assertEqual(result, {})

    def test_collects_resolved_urls(self):
        with mock.patch.object(
            unshorten_links,
            'unshorten_url',
            side_effect=lambda url, session=None: 'https://final.example.com/' + url[-3:],
        ):
            result = unshorten_links.unshorten_urls(['https://bit.ly/abc', 'https://t.co/xyz'])

        self.assertEqual(result, {
            'https://bit.ly/abc': 'https://final.example.com/abc',
            'https://t.co/xyz': 'https://final.example.com/xyz',
        })

    def test_omits_failed_resolutions(self):
        with mock.patch.object(unshorten_links, 'unshorten_url', return_value=None):
            result = unshorten_links.unshorten_urls(['https://bit.ly/abc'])
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()

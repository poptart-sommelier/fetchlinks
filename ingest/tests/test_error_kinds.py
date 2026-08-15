import socket
import unittest

import requests

import error_kinds


class FromStatusTests(unittest.TestCase):
    def test_no_status_at_all_is_a_network_fault(self):
        """Never getting a reply is not a status of zero."""
        self.assertEqual(error_kinds.from_status(0), 'network')
        self.assertEqual(error_kinds.from_status(None), 'network')

    def test_a_good_status_is_not_an_error(self):
        self.assertEqual(error_kinds.from_status(200), '')
        self.assertEqual(error_kinds.from_status(304), '')

    def test_refusals_are_told_apart_from_ordinary_failures(self):
        self.assertEqual(error_kinds.from_status(401), 'authentication')
        self.assertEqual(error_kinds.from_status(403), 'authentication')
        self.assertEqual(error_kinds.from_status(429), 'rate_limit')
        self.assertEqual(error_kinds.from_status(500), 'http')


class FromExceptionTests(unittest.TestCase):
    def test_timeouts_are_not_folded_into_network(self):
        self.assertEqual(
            error_kinds.from_exception(requests.exceptions.Timeout('slow')),
            'timeout')
        self.assertEqual(error_kinds.from_exception(socket.timeout('slow')),
                         'timeout')

    def test_an_http_error_is_categorized_by_its_status(self):
        response = requests.Response()
        response.status_code = 429
        exc = requests.exceptions.HTTPError('too many')
        exc.response = response

        self.assertEqual(error_kinds.from_exception(exc), 'rate_limit')

    def test_a_reply_that_was_not_what_it_claimed_is_its_own_category(self):
        self.assertEqual(error_kinds.from_exception(ValueError('not json')),
                         'invalid_response')

    def test_anything_unfamiliar_still_gets_a_name(self):
        self.assertEqual(error_kinds.from_exception(RuntimeError('odd')),
                         'unknown')


class DescribeTests(unittest.TestCase):
    def test_an_empty_message_still_names_the_failure(self):
        """Several connection errors carry no text at all."""
        self.assertEqual(error_kinds.describe(ConnectionError()),
                         'ConnectionError')

    def test_the_message_is_kept_when_there_is_one(self):
        self.assertEqual(error_kinds.describe(ValueError('not json')),
                         'ValueError: not json')

    def test_a_very_long_message_is_trimmed(self):
        described = error_kinds.describe(ValueError('x' * 900))
        self.assertLessEqual(len(described), 500)


class FromRssErrorTests(unittest.TestCase):
    def test_no_error_text_means_no_error(self):
        self.assertEqual(error_kinds.from_rss_error(200, None), '')

    def test_a_parse_failure_is_not_a_network_failure(self):
        self.assertEqual(
            error_kinds.from_rss_error(200, 'parse error with no entries'),
            'parse')

    def test_a_status_is_used_when_one_was_received(self):
        self.assertEqual(error_kinds.from_rss_error(500, 'HTTP 500'), 'http')

    def test_no_status_means_the_request_never_arrived(self):
        self.assertEqual(error_kinds.from_rss_error(0, 'ConnectionError'),
                         'network')


if __name__ == '__main__':
    unittest.main()

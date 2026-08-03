"""Tests for resolving and redacting the publisher's database URL.

No server required: this is the part of the Publisher that decides *whether*
to connect at all, and the important behaviour is that it refuses to guess.
"""

from __future__ import annotations

import unittest

from publisher.connection import (
    PublisherConfigError,
    redact,
    resolve_database_url,
)


class ResolveDatabaseUrlTests(unittest.TestCase):
    def test_explicit_argument_wins(self):
        self.assertEqual(
            resolve_database_url('postgresql://explicit/db',
                                 env={'DATABASE_URL': 'postgresql://env/db'}),
            'postgresql://explicit/db',
        )

    def test_prefers_the_fetchlinks_specific_variable(self):
        self.assertEqual(
            resolve_database_url(env={
                'FETCHLINKS_DATABASE_URL': 'postgresql://ours/db',
                'DATABASE_URL': 'postgresql://someone-elses/db',
            }),
            'postgresql://ours/db',
        )

    def test_falls_back_to_database_url(self):
        self.assertEqual(
            resolve_database_url(env={'DATABASE_URL': 'postgresql://env/db'}),
            'postgresql://env/db',
        )

    def test_blank_values_are_treated_as_absent(self):
        with self.assertRaises(PublisherConfigError):
            resolve_database_url(env={'DATABASE_URL': '   '})

    def test_missing_url_raises_rather_than_defaulting_to_localhost(self):
        # A silent default would let a misconfigured Pi publish into a
        # database nobody reads, and look successful doing it.
        with self.assertRaises(PublisherConfigError) as ctx:
            resolve_database_url(env={})
        self.assertIn('FETCHLINKS_DATABASE_URL', str(ctx.exception))


class RedactTests(unittest.TestCase):
    def test_removes_the_password_but_keeps_the_target(self):
        redacted = redact('postgresql://user:secret@db.example:5432/fetchlinks')
        self.assertNotIn('secret', redacted)
        self.assertNotIn('user', redacted)
        self.assertIn('db.example:5432/fetchlinks', redacted)

    def test_url_without_credentials_is_unchanged(self):
        url = 'postgresql:///fetchlinks'
        self.assertEqual(redact(url), url)


if __name__ == '__main__':
    unittest.main()

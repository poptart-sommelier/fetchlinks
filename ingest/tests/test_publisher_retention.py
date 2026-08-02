"""Tests for PostgreSQL retention."""

from __future__ import annotations

import unittest

from tests.pg_support import PostgresTestCase, available

if available():
    from publisher.retention import run_retention


class RetentionTests(PostgresTestCase):
    def add_post(self, unique_id: str, months_ago: int, urls=('https://x.example/1',)):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO content.posts '
                '(unique_id, source, source_type, posted_at) '
                "VALUES (%s, 'src', 'rss', now() - make_interval(months => %s)) "
                'RETURNING post_id',
                (unique_id, months_ago),
            )
            post_id = cur.fetchone()[0]
            for position, url in enumerate(urls):
                cur.execute(
                    'INSERT INTO content.post_urls '
                    '(post_id, position, url, url_hash) VALUES (%s, %s, %s, %s)',
                    (post_id, position, url, f'hash-{unique_id}-{position}'),
                )
        self.conn.commit()

    def test_deletes_posts_past_the_age_limit(self):
        self.add_post('old', months_ago=4)
        self.add_post('recent', months_ago=1)

        report = run_retention(self.conn, 3)

        self.assertEqual(report.posts_deleted, 1)
        self.assertEqual(
            [r[0] for r in self.rows('SELECT unique_id FROM content.posts')],
            ['recent'],
        )

    def test_deleted_posts_take_their_urls_with_them(self):
        self.add_post('old', months_ago=6, urls=('https://a/1', 'https://a/2'))
        run_retention(self.conn, 3)
        self.assertEqual(self.count('content.post_urls'), 0)

    def test_nothing_to_delete_is_not_an_error(self):
        self.add_post('recent', months_ago=1)
        report = run_retention(self.conn, 3)
        self.assertEqual(report.posts_deleted, 0)
        self.assertEqual(self.count('content.posts'), 1)

    def test_forgets_batch_ids_older_than_the_ledger_window(self):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO content.published_batches '
                '(batch_id, contract_version, batch_created_at, published_at) '
                "VALUES ('20200101T000000000000Z-aaaaaaaa', 1, now(), "
                "        now() - interval '200 days'), "
                "       ('20260101T000000000000Z-bbbbbbbb', 1, now(), now())"
            )
        self.conn.commit()

        report = run_retention(self.conn, 3, ledger_retention_days=90)

        self.assertEqual(report.batches_forgotten, 1)
        self.assertEqual(self.count('content.published_batches'), 1)

    def test_ledger_pruning_can_be_switched_off(self):
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO content.published_batches '
                '(batch_id, contract_version, batch_created_at, published_at) '
                "VALUES ('20200101T000000000000Z-aaaaaaaa', 1, now(), "
                "        now() - interval '900 days')"
            )
        self.conn.commit()

        report = run_retention(self.conn, 3, ledger_retention_days=0)

        self.assertEqual(report.batches_forgotten, 0)
        self.assertEqual(self.count('content.published_batches'), 1)

    def test_a_nonsense_age_limit_is_rejected_rather_than_applied(self):
        # A zero or negative cutoff would delete everything.
        self.add_post('recent', months_ago=1)
        for value in (0, -1):
            with self.assertRaises(ValueError):
                run_retention(self.conn, value)
        self.assertEqual(self.count('content.posts'), 1)


if __name__ == '__main__':
    unittest.main()

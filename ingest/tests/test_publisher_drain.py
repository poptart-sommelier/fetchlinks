"""Tests for draining the spool into PostgreSQL.

The interesting behaviour is not "batches get published" but what happens when
one cannot be: a permanently broken batch must not block the queue, and a
transient database problem must not lose one.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.pg_support import PostgresTestCase, available
from tests.test_publisher_apply import BatchBuilderMixin, post

from pipeline.spool import STAGE_FAILED, STAGE_PROCESSING, STAGE_PUBLISHED, STAGE_READY

if available():
    import psycopg

    from publisher.drain import drain_ready


class DrainTests(BatchBuilderMixin, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def test_drains_every_ready_batch_oldest_first(self):
        first = self.queue(posts=[post(unique_id='a')])
        second = self.queue(posts=[post(unique_id='b')])

        report = drain_ready(self.conn, self.spool)

        self.assertEqual([o.batch_id for o in report.published], [first, second])
        self.assertEqual(self.count('content.posts'), 2)
        self.assertEqual(self.spool.batch_ids(STAGE_READY), [])
        self.assertEqual(len(self.spool.batch_ids(STAGE_PUBLISHED)), 2)

    def test_max_batches_stops_early_and_leaves_the_rest_queued(self):
        self.queue(posts=[post(unique_id='a')])
        self.queue(posts=[post(unique_id='b')])

        report = drain_ready(self.conn, self.spool, max_batches=1)

        self.assertEqual(report.published_count, 1)
        self.assertEqual(len(self.spool.batch_ids(STAGE_READY)), 1)

    def test_empty_queue_is_not_an_error(self):
        report = drain_ready(self.conn, self.spool)
        self.assertEqual(report.published_count, 0)
        self.assertIsNone(report.stopped_on)

    def test_a_corrupt_batch_is_quarantined_and_the_queue_keeps_moving(self):
        broken = self.queue(posts=[post(unique_id='a')])
        good = self.queue(posts=[post(unique_id='b')])

        # Truncate a declared file so its checksum no longer matches.
        (self.spool.batch_path(STAGE_READY, broken) / 'posts.ndjson').write_text(
            '', encoding='utf-8'
        )

        report = drain_ready(self.conn, self.spool)

        self.assertEqual([b for b, _ in report.failed], [broken])
        self.assertEqual([o.batch_id for o in report.published], [good])
        self.assertEqual(self.spool.batch_ids(STAGE_FAILED), [broken])
        self.assertEqual(self.count('content.posts'), 1)

    def test_a_database_failure_stops_the_drain_and_keeps_the_batch(self):
        first = self.queue(posts=[post(unique_id='a')])
        self.queue(posts=[post(unique_id='b')])

        with patch('publisher.drain.apply_batch',
                   side_effect=psycopg.OperationalError('server closed')):
            report = drain_ready(self.conn, self.spool)

        self.assertEqual(report.published_count, 0)
        self.assertIsNotNone(report.stopped_on)
        self.assertEqual(report.stopped_on[0], first)
        # Claimed but unresolved, so the next run recovers it rather than
        # skipping past it and publishing out of order.
        self.assertEqual(self.spool.batch_ids(STAGE_PROCESSING), [first])
        self.assertEqual(len(self.spool.batch_ids(STAGE_READY)), 1)

    def test_a_recovered_batch_is_archived_without_being_reapplied(self):
        self.queue(posts=[post(unique_id='a')])

        with patch('publisher.drain.apply_batch',
                   side_effect=psycopg.OperationalError('server closed')):
            drain_ready(self.conn, self.spool)

        report = drain_ready(self.conn, self.spool)

        self.assertEqual(report.published_count, 1)
        self.assertEqual(self.count('content.posts'), 1)
        self.assertEqual(len(self.spool.batch_ids(STAGE_PUBLISHED)), 1)

    def test_report_summarises_what_happened(self):
        self.queue(posts=[post(unique_id='a', urls=('https://x.example/1',))])
        report = drain_ready(self.conn, self.spool)
        self.assertIn('1 batches published', report.summary())
        self.assertIn('1 new posts', report.summary())


if __name__ == '__main__':
    unittest.main()

"""Tests for the operation run records.

The properties that matter here are not "does it insert a row". They are: a run
that dies leaves evidence, a replayed batch records its collection once, and a
failure to record anything never becomes a failure to publish.
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from tests.pg_support import PostgresTestCase

try:
    import psycopg

    import publish_tool
    from pipeline import contract
    from publisher import operations
    from publisher.apply import PublishOutcome
    from publisher.drain import DrainReport
except ImportError:  # pragma: no cover - psycopg is a hard requirement in prod
    psycopg = None
    contract = None
    operations = None


class OperationRunTests(PostgresTestCase):
    def runs(self):
        return self.rows(
            'SELECT job, run_key, result, elapsed_ms, error_kind, error_message, '
            '       details, finished_at '
            '  FROM content.operation_runs ORDER BY run_id'
        )

    def test_a_started_run_is_visible_before_it_finishes(self):
        # The whole point of the `running` result: a publisher killed mid-drain
        # must not be indistinguishable from one that never ran.
        operations.start_run(self.conn, operations.JOB_PUBLISH,
                             details={'queue_before': {'counts': {'ready': 2}}})

        other = psycopg.connect(self.database_url)
        self.addCleanup(other.close)
        with other.cursor() as cur:
            cur.execute(
                'SELECT job, result, finished_at, details '
                '  FROM content.operation_runs'
            )
            job, result, finished_at, details = cur.fetchone()

        self.assertEqual(job, 'publish')
        self.assertEqual(result, 'running')
        self.assertIsNone(finished_at)
        self.assertEqual(details['queue_before']['counts']['ready'], 2)

    def test_finishing_records_the_outcome_and_the_accumulated_details(self):
        run = operations.start_run(self.conn, operations.JOB_PUBLISH)
        run.note(batches_published=3)
        run.note(posts_inserted=17)
        run.finish()

        (job, _key, result, elapsed_ms, kind, message, details, finished), = self.runs()
        self.assertEqual(job, 'publish')
        self.assertEqual(result, 'ok')
        self.assertEqual(kind, '')
        self.assertEqual(message, '')
        self.assertIsNotNone(finished)
        self.assertGreaterEqual(elapsed_ms, 0)
        self.assertEqual(details, {'batches_published': 3, 'posts_inserted': 17})

    def test_a_job_that_raises_still_leaves_a_failed_row(self):
        with self.assertRaises(ZeroDivisionError):
            with operations.record_run(self.conn, operations.JOB_RETENTION) as run:
                run.note(posts_deleted=4)
                raise ZeroDivisionError('boom')

        (_job, _key, result, _ms, kind, message, details, _finished), = self.runs()
        self.assertEqual(result, 'failed')
        self.assertEqual(kind, 'unknown')
        self.assertEqual(message, 'ZeroDivisionError: boom')
        # What it managed before it failed is kept: often the whole answer.
        self.assertEqual(details, {'posts_deleted': 4})

    def test_a_database_failure_is_categorized_as_one(self):
        with self.assertRaises(psycopg.Error):
            with operations.record_run(self.conn, operations.JOB_CATALOG_SYNC):
                with self.conn.cursor() as cur:
                    cur.execute('SELECT * FROM content.no_such_table')

        (_job, _key, result, _ms, kind, _message, _details, _f), = self.runs()
        self.assertEqual(result, 'failed')
        self.assertEqual(kind, 'database')

    def test_an_enormous_error_message_is_cut_to_fit(self):
        with self.assertRaises(RuntimeError):
            with operations.record_run(self.conn, operations.JOB_PUBLISH):
                raise RuntimeError('x' * 5000)

        (_job, _key, _result, _ms, _kind, message, _details, _f), = self.runs()
        self.assertLessEqual(len(message), 500)

    def test_a_completed_run_survives_a_rolled_back_transaction(self):
        # The recorder rolls the connection back before writing, because the
        # caller may hand it over in any state at all.
        run = operations.start_run(self.conn, operations.JOB_PUBLISH)
        with self.assertRaises(psycopg.Error):
            with self.conn.cursor() as cur:
                cur.execute('SELECT * FROM content.no_such_table')
        run.finish()

        (_job, _key, result, _ms, _kind, _message, _details, _f), = self.runs()
        self.assertEqual(result, 'ok')

    def test_details_must_be_an_object(self):
        with self.assertRaises(TypeError):
            operations._details(['not', 'an', 'object'])

    def test_a_run_that_could_not_be_started_finishes_harmlessly(self):
        # Instrumentation must never be the reason a publish fails, so a
        # recorder with no row behind it is a working recorder that does
        # nothing.
        run = operations.RunRecorder(self.conn, operations.JOB_PUBLISH, None)
        run.note(anything=1)
        run.finish()
        self.assertEqual(self.count('content.operation_runs'), 0)


class CollectionRunRecordTests(PostgresTestCase):
    def record(self, **overrides) -> dict:
        base = {
            'started_at': '2026-01-01T10:00:00Z',
            'finished_at': '2026-01-01T10:00:12Z',
            'elapsed_ms': 12000,
            'result': contract.RESULT_OK,
            'posts_collected': 5,
            'error_kind': '',
            'error_message': '',
            'sources': [{
                'source_type': 'rss',
                'result': contract.RESULT_OK,
                'channels_attempted': 3,
                'channels_succeeded': 3,
                'channels_failed': 0,
                'items_collected': 5,
                'elapsed_ms': 900,
                'faults': {},
                'error_kind': '',
                'error_message': '',
            }],
            'subtasks': [],
        }
        base.update(overrides)
        return base

    def apply(self, batch_id: str, record: dict) -> bool:
        with self.conn.cursor() as cur:
            inserted = operations.record_collection_run(cur, batch_id, record)
        self.conn.commit()
        return inserted

    def test_a_collection_run_arrives_with_its_batch(self):
        self.assertTrue(self.apply('20260101T100000000000Z-aaaaaaaa', self.record()))

        (job, run_key, result, elapsed_ms, details) = self.rows(
            'SELECT job, run_key, result, elapsed_ms, details '
            '  FROM content.operation_runs'
        )[0]
        self.assertEqual(job, 'collect')
        self.assertEqual(run_key, '20260101T100000000000Z-aaaaaaaa')
        self.assertEqual(result, 'ok')
        self.assertEqual(elapsed_ms, 12000)
        self.assertEqual(details['posts_collected'], 5)
        self.assertEqual(details['sources'][0]['source_type'], 'rss')

    def test_replaying_a_batch_records_its_collection_once(self):
        batch_id = '20260101T100000000000Z-aaaaaaaa'
        self.assertTrue(self.apply(batch_id, self.record()))
        self.assertFalse(self.apply(batch_id, self.record(posts_collected=99)))

        self.assertEqual(self.count('content.operation_runs'), 1)
        self.assertEqual(
            self.scalar("SELECT details->>'posts_collected' "
                        'FROM content.operation_runs'),
            '5',
        )

    def test_two_batches_record_two_runs(self):
        self.apply('20260101T100000000000Z-aaaaaaaa', self.record())
        self.apply('20260101T103000000000Z-bbbbbbbb', self.record())
        self.assertEqual(self.count('content.operation_runs'), 2)

    def test_a_publisher_run_does_not_collide_with_a_collection_run(self):
        # Publisher rows carry an empty run key, and the unique index is
        # partial, so any number of them coexist.
        operations.start_run(self.conn, operations.JOB_PUBLISH).finish()
        operations.start_run(self.conn, operations.JOB_PUBLISH).finish()
        self.apply('20260101T100000000000Z-aaaaaaaa', self.record())
        self.assertEqual(self.count('content.operation_runs'), 3)

    def test_a_failed_collection_keeps_its_reason(self):
        self.apply('20260101T100000000000Z-aaaaaaaa', self.record(
            result=contract.RESULT_FAILED,
            posts_collected=0,
            error_kind='network',
            error_message='every source failed',
        ))
        (result, kind, message), = self.rows(
            'SELECT result, error_kind, error_message FROM content.operation_runs'
        )
        self.assertEqual(result, 'failed')
        self.assertEqual(kind, 'network')
        self.assertEqual(message, 'every source failed')

    def test_an_unrecognized_error_kind_becomes_unknown_rather_than_failing(self):
        self.apply('20260101T100000000000Z-aaaaaaaa', self.record(
            result=contract.RESULT_FAILED, error_kind='banana',
        ))
        self.assertEqual(
            self.scalar('SELECT error_kind FROM content.operation_runs'), 'unknown'
        )


class PublishHeartbeatTests(PostgresTestCase):
    """`publish` writes a row every hour whether or not there was work.

    This is the heartbeat the status page reads. A quiet hour and a dead
    publisher must not look the same.
    """

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime_dir = tmp.name

    def args(self, **overrides):
        fields = {
            'config': None,
            'runtime_dir': self.runtime_dir,
            'database_url': None,
            'max_batches': None,
            'prune_days': 0,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    @contextmanager
    def _connection(self, *_args, **_kwargs):
        conn = psycopg.connect(self.database_url)
        try:
            yield conn
        finally:
            conn.close()

    def publish(self, **overrides) -> int:
        with patch('publish_tool.connection.connect', self._connection):
            return publish_tool.cmd_publish(self.args(**overrides))

    def run_row(self):
        return self.rows(
            'SELECT job, result, error_kind, error_message, details, finished_at '
            '  FROM content.operation_runs ORDER BY run_id'
        )

    def test_an_empty_queue_still_leaves_a_heartbeat(self):
        self.assertEqual(self.publish(), 0)

        (job, result, kind, _message, details, finished), = self.run_row()
        self.assertEqual(job, 'publish')
        self.assertEqual(result, 'ok')
        self.assertEqual(kind, '')
        self.assertIsNotNone(finished)
        self.assertEqual(details['batches_published'], 0)
        self.assertEqual(details['queue_before']['counts']['ready'], 0)
        self.assertIn('queue_after', details)
        self.assertGreater(details['database_bytes'], 0)
        self.assertGreater(details['disk_free_bytes'], 0)

    def test_a_drain_that_blows_up_leaves_a_failed_row(self):
        with patch('publish_tool.drain_ready',
                   side_effect=psycopg.OperationalError('connection lost')):
            with self.assertRaises(psycopg.OperationalError):
                self.publish()

        (_job, result, kind, message, _details, finished), = self.run_row()
        self.assertEqual(result, 'failed')
        self.assertEqual(kind, 'database')
        self.assertIn('connection lost', message)
        self.assertIsNotNone(finished)

    def test_a_quarantined_batch_with_nothing_published_reads_as_failed(self):
        report = DrainReport(failed=[('20260101T000000000000Z-aaaaaaaa', 'bad hash')])
        with patch('publish_tool.drain_ready', return_value=report):
            self.assertEqual(self.publish(), 1)

        (_job, result, kind, message, _details, _f), = self.run_row()
        self.assertEqual(result, 'failed')
        self.assertEqual(kind, 'invalid_batch')
        self.assertIn('bad hash', message)

    def test_a_partly_successful_drain_says_so(self):
        # One poisoned batch while everything else published is a very
        # different morning from a database that refused every connection.
        report = DrainReport(
            published=[PublishOutcome(batch_id='20260101T000000000000Z-bbbbbbbb')],
            failed=[('20260101T000000000000Z-aaaaaaaa', 'bad hash')],
        )
        with patch('publish_tool.drain_ready', return_value=report):
            self.assertEqual(self.publish(), 1)

        self.assertEqual(self.scalar('SELECT result FROM content.operation_runs'),
                         'partial')

    def test_a_stalled_queue_is_recorded_as_a_database_problem(self):
        report = DrainReport(
            stopped_on=('20260101T000000000000Z-aaaaaaaa', 'connection lost')
        )
        with patch('publish_tool.drain_ready', return_value=report):
            self.assertEqual(self.publish(), 1)

        (_job, result, kind, message, _details, _f), = self.run_row()
        self.assertEqual(result, 'failed')
        self.assertEqual(kind, 'database')
        self.assertIn('connection lost', message)


@unittest.skipUnless(operations is not None, 'psycopg is not installed')
class HostFactsTests(unittest.TestCase):
    def test_free_space_is_reported_for_a_real_path(self):
        facts = operations.host_facts('.')
        self.assertGreater(facts['disk_free_bytes'], 0)

    def test_a_missing_path_is_not_an_error(self):
        facts = operations.host_facts('/no/such/place/at/all')
        self.assertNotIn('disk_free_bytes', facts)

    def test_no_path_means_no_disk_reading(self):
        self.assertNotIn('disk_free_bytes', operations.host_facts())


if __name__ == '__main__':
    unittest.main()

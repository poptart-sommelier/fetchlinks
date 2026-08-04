import datetime
import json
import logging
import shutil
import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from pipeline import contract, spool as spool_module
from pipeline.contract import (
    BlueskyFollowRecord,
    CheckpointRecord,
    ContractError,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
)
from pipeline.spool import (
    STAGE_FAILED,
    STAGE_PROCESSING,
    STAGE_PUBLISHED,
    STAGE_READY,
    STAGE_STAGING,
    BatchValidationError,
    Spool,
    SpoolError,
)

COLLECTOR_VERSION = '1.0.0-test'


def setUpModule():
    # Recovery, quarantine, and release all log by design; the tests assert on
    # the resulting state rather than the noise.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def make_post(suffix, source_type='rss'):
    return PostRecord(
        unique_id=f'uid-{suffix}',
        source='https://example.com',
        source_type=source_type,
        posted_at='2026-01-02T03:04:05Z',
        urls=(f'https://example.com/{suffix}',),
        author='Someone',
        description=f'Post {suffix}',
    )


def make_observation(suffix, success=True):
    return RssObservationRecord(
        normalized_url=f'https://example.com/{suffix}/feed',
        feed_url=f'https://example.com/{suffix}/feed',
        observed_at='2026-01-02T03:04:05Z',
        success=success,
        status=200 if success else 500,
        error=None if success else 'boom',
    )


def make_checkpoint(source_type='reddit', source_key='netsec', cursor='t3_abc'):
    return CheckpointRecord(
        source_type=source_type,
        source_key=source_key,
        cursor=cursor,
        observed_at='2026-01-02T03:04:05Z',
    )


class _SpoolCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / 'outbox'
        self.spool = Spool(self.root).initialize()

    def tearDown(self):
        self._tmp.cleanup()

    def write_batch(self, posts=(), observations=(), checkpoints=(), **kwargs):
        with self.spool.new_batch(collector_version=COLLECTOR_VERSION, **kwargs) as batch:
            if posts:
                batch.add_posts(posts)
            if observations:
                batch.add_rss_observations(observations)
            if checkpoints:
                batch.add_checkpoints(checkpoints)
        return batch

    def ready_ids(self):
        return self.spool.batch_ids(STAGE_READY)


class SpoolLayoutTests(_SpoolCase):
    def test_initialize_creates_every_stage(self):
        for stage in spool_module.STAGES:
            self.assertTrue((self.root / stage).is_dir(), stage)

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(SpoolError):
            self.spool.stage_dir('elsewhere')

    def test_hostile_batch_id_cannot_escape_the_spool(self):
        with self.assertRaises(ContractError):
            self.spool.batch_path(STAGE_READY, '../../etc/passwd')

    def test_foreign_directories_are_ignored(self):
        (self.root / STAGE_READY / 'not-a-batch').mkdir()
        self.assertEqual(self.ready_ids(), [])


class BatchWritingTests(_SpoolCase):
    def test_batch_lands_in_ready_with_a_complete_manifest(self):
        batch = self.write_batch(
            posts=[make_post('a'), make_post('b')],
            observations=[make_observation('one')],
            checkpoints=[make_checkpoint()],
            catalog_revision='rev-7',
        )

        self.assertEqual(self.ready_ids(), [batch.batch_id])
        manifest = json.loads(
            (self.root / STAGE_READY / batch.batch_id / 'manifest.json').read_text('utf-8')
        )
        self.assertEqual(manifest['contract_version'], contract.CONTRACT_VERSION)
        self.assertEqual(manifest['batch_id'], batch.batch_id)
        self.assertEqual(manifest['collector_version'], COLLECTOR_VERSION)
        self.assertEqual(manifest['catalog_revision'], 'rev-7')
        by_kind = {entry['kind']: entry for entry in manifest['files']}
        self.assertEqual(by_kind[contract.KIND_POSTS]['record_count'], 2)
        self.assertEqual(by_kind[contract.KIND_RSS_OBSERVATIONS]['record_count'], 1)
        self.assertEqual(by_kind[contract.KIND_CHECKPOINTS]['record_count'], 1)

    def test_staging_is_left_clean(self):
        self.write_batch(posts=[make_post('a')])
        self.assertEqual(list((self.root / STAGE_STAGING).iterdir()), [])

    def test_records_are_written_one_per_line_in_order(self):
        batch = self.write_batch(posts=[make_post('a'), make_post('b'), make_post('c')])
        lines = (
            (self.root / STAGE_READY / batch.batch_id / 'posts.ndjson')
            .read_text('utf-8').splitlines()
        )
        self.assertEqual([json.loads(line)['unique_id'] for line in lines],
                         ['uid-a', 'uid-b', 'uid-c'])

    def test_multiple_appends_accumulate_into_one_file(self):
        with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
            batch.add_posts([make_post('a')])
            batch.add_posts([make_post('b')])
        entry = json.loads(
            (self.root / STAGE_READY / batch.batch_id / 'manifest.json').read_text('utf-8')
        )['files'][0]
        self.assertEqual(entry['record_count'], 2)

    def test_serialization_is_byte_for_byte_reproducible(self):
        def checksum(batch_id):
            with self.spool.new_batch(
                collector_version=COLLECTOR_VERSION, batch_id=batch_id
            ) as batch:
                batch.add_posts([make_post('a'), make_post('b')])
            manifest = json.loads(
                (self.root / STAGE_READY / batch_id / 'manifest.json').read_text('utf-8')
            )
            return manifest['files'][0]['sha256']

        first = checksum(contract.new_batch_id(datetime.datetime(2026, 1, 1, tzinfo=UTC)))
        second = checksum(contract.new_batch_id(datetime.datetime(2026, 1, 2, tzinfo=UTC)))
        self.assertEqual(first, second)

    def test_invalid_record_is_refused_at_the_boundary(self):
        with self.assertRaises(ContractError):
            with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
                batch.add_posts([PostRecord(
                    unique_id='uid', source='https://example.com', source_type='rss',
                    posted_at='2026-01-02T03:04:05Z', urls=(),
                )])
        self.assertEqual(self.ready_ids(), [])

    def test_empty_batch_is_discarded(self):
        with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
            pass
        self.assertTrue(batch.discarded)
        self.assertEqual(self.ready_ids(), [])

    def test_empty_batch_can_be_kept_when_asked(self):
        with self.spool.new_batch(
            collector_version=COLLECTOR_VERSION, discard_if_empty=False
        ) as batch:
            pass
        self.assertEqual(self.ready_ids(), [batch.batch_id])

    def test_empty_snapshot_is_content_not_emptiness(self):
        # "This account now follows nobody" is a real observation the
        # publisher has to apply, so it must survive the empty-batch check.
        with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
            batch.set_bluesky_follows([])
        self.assertFalse(batch.discarded)
        self.assertEqual(self.ready_ids(), [batch.batch_id])

    def test_snapshot_cannot_be_recorded_twice(self):
        with self.assertRaises(SpoolError):
            with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
                batch.set_bluesky_follows([BlueskyFollowRecord('did:plc:a', 'a.example')])
                batch.set_bluesky_follows([BlueskyFollowRecord('did:plc:b', 'b.example')])

    def test_each_mastodon_instance_gets_its_own_snapshot(self):
        with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
            batch.set_mastodon_follows('infosec', [MastodonFollowRecord('1', 'a@infosec')])
            batch.set_mastodon_follows('fosstodon', [MastodonFollowRecord('2', 'b@foss')])
        names = sorted(p.name for p in (self.root / STAGE_READY / batch.batch_id).iterdir())
        self.assertEqual(names, [
            'manifest.json',
            'mastodon-follows-fosstodon.ndjson',
            'mastodon-follows-infosec.ndjson',
        ])

    def test_writing_after_commit_is_refused(self):
        batch = self.write_batch(posts=[make_post('a')])
        with self.assertRaises(SpoolError):
            batch.add_posts([make_post('b')])


class CrashDuringCollectionTests(_SpoolCase):
    def test_exception_discards_the_partial_batch(self):
        with self.assertRaises(RuntimeError):
            with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as batch:
                batch.add_posts([make_post('a')])
                raise RuntimeError('network died mid-collection')

        self.assertEqual(self.ready_ids(), [])
        self.assertEqual(list((self.root / STAGE_STAGING).iterdir()), [])

    def test_an_abandoned_staging_directory_is_never_published(self):
        # Simulate a hard kill: staging content exists but was never promoted.
        orphan = self.root / STAGE_STAGING / contract.new_batch_id()
        orphan.mkdir(parents=True)
        (orphan / 'posts.ndjson').write_text('{"partial": true}\n', encoding='utf-8')

        self.assertIsNone(self.spool.claim_next())


class ClaimOrderingTests(_SpoolCase):
    def test_batches_are_claimed_oldest_first(self):
        ids = [self.write_batch(posts=[make_post(str(n))]).batch_id for n in range(3)]

        claimed = []
        for _ in range(3):
            batch = self.spool.claim_next()
            claimed.append(batch.batch_id)
            batch.mark_published()

        self.assertEqual(claimed, sorted(ids))

    def test_claiming_moves_the_batch_out_of_ready(self):
        batch_id = self.write_batch(posts=[make_post('a')]).batch_id
        claimed = self.spool.claim_next()
        self.assertEqual(claimed.batch_id, batch_id)
        self.assertEqual(self.ready_ids(), [])
        self.assertEqual(self.spool.batch_ids(STAGE_PROCESSING), [batch_id])

    def test_empty_queue_claims_nothing(self):
        self.assertIsNone(self.spool.claim_next())

    def test_claim_all_drains_the_queue_in_order(self):
        ids = [self.write_batch(posts=[make_post(str(n))]).batch_id for n in range(4)]
        drained = []
        for batch in self.spool.claim_all():
            drained.append(batch.batch_id)
            batch.mark_published()
        self.assertEqual(drained, sorted(ids))

    def test_a_batch_abandoned_mid_publish_is_reclaimed_first(self):
        first = self.write_batch(posts=[make_post('a')]).batch_id
        self.spool.claim_next()  # publisher dies here, batch stays in processing
        second = self.write_batch(posts=[make_post('b')]).batch_id

        reclaimed = self.spool.claim_next()

        self.assertEqual(reclaimed.batch_id, first)
        self.assertEqual(self.ready_ids(), [second])


class VerificationTests(_SpoolCase):
    def _claim(self, **kwargs):
        self.write_batch(**kwargs)
        return self.spool.claim_next()

    def test_a_well_formed_batch_verifies(self):
        batch = self._claim(
            posts=[make_post('a')],
            observations=[make_observation('one')],
            checkpoints=[make_checkpoint()],
        )
        manifest = batch.verify()
        self.assertEqual(manifest.total_records, 3)

    def test_records_can_be_read_back(self):
        batch = self._claim(posts=[make_post('a'), make_post('b')])
        self.assertEqual(
            [record['unique_id'] for record in batch.records(contract.KIND_POSTS)],
            ['uid-a', 'uid-b'],
        )

    def test_absent_kind_reads_as_nothing(self):
        batch = self._claim(posts=[make_post('a')])
        self.assertEqual(list(batch.records(contract.KIND_CHECKPOINTS)), [])

    def test_snapshots_are_yielded_with_their_scope(self):
        with self.spool.new_batch(collector_version=COLLECTOR_VERSION) as writer:
            writer.set_mastodon_follows('infosec', [MastodonFollowRecord('1', 'a@infosec')])
            writer.set_mastodon_follows('fosstodon', [])
        batch = self.spool.claim_next()

        found = {
            entry.scope: [record['acct'] for record in records]
            for entry, records in batch.snapshots(contract.KIND_MASTODON_FOLLOWS)
        }
        self.assertEqual(found, {'infosec': ['a@infosec'], 'fosstodon': []})

    def test_tampered_file_fails_its_checksum(self):
        batch = self._claim(posts=[make_post('a')])
        target = batch.path / 'posts.ndjson'
        record = json.loads(target.read_text('utf-8').strip())
        record['urls'] = ['https://evil.example/payload']
        target.write_text(contract.dumps_line(record) + '\n', encoding='utf-8', newline='')

        with self.assertRaises(BatchValidationError) as caught:
            batch.verify()
        self.assertIn('checksum', str(caught.exception))

    def test_truncated_file_fails(self):
        batch = self._claim(posts=[make_post('a'), make_post('b')])
        target = batch.path / 'posts.ndjson'
        first = target.read_text('utf-8').splitlines()[0]
        target.write_text(first + '\n', encoding='utf-8', newline='')

        with self.assertRaises(BatchValidationError):
            batch.verify()

    def test_record_count_mismatch_fails(self):
        batch = self._claim(posts=[make_post('a')])
        manifest_path = batch.path / 'manifest.json'
        document = json.loads(manifest_path.read_text('utf-8'))
        document['files'][0]['record_count'] = 5
        manifest_path.write_text(json.dumps(document), encoding='utf-8')

        with self.assertRaises(BatchValidationError) as caught:
            batch.verify()
        self.assertIn('declares 5', str(caught.exception))

    def test_malformed_record_fails_with_a_line_number(self):
        batch = self._claim(posts=[make_post('a')])
        target = batch.path / 'posts.ndjson'
        target.write_text('{ not json\n', encoding='utf-8', newline='')
        self._resync_checksum(batch, 'posts.ndjson')

        with self.assertRaises(BatchValidationError) as caught:
            batch.verify()
        self.assertIn('line 1', str(caught.exception))

    def test_schema_violating_record_fails(self):
        batch = self._claim(posts=[make_post('a')])
        target = batch.path / 'posts.ndjson'
        target.write_text(
            contract.dumps_line({'unique_id': 'uid', 'urls': []}) + '\n',
            encoding='utf-8', newline='',
        )
        self._resync_checksum(batch, 'posts.ndjson')

        with self.assertRaises(BatchValidationError):
            batch.verify()

    def test_missing_declared_file_fails(self):
        batch = self._claim(posts=[make_post('a')])
        (batch.path / 'posts.ndjson').unlink()

        with self.assertRaises(BatchValidationError) as caught:
            batch.verify()
        self.assertIn('missing', str(caught.exception))

    def test_undeclared_file_fails(self):
        batch = self._claim(posts=[make_post('a')])
        (batch.path / 'checkpoints.ndjson').write_text('{}\n', encoding='utf-8')

        with self.assertRaises(BatchValidationError) as caught:
            batch.verify()
        self.assertIn('undeclared', str(caught.exception))

    def test_missing_manifest_fails(self):
        batch = self._claim(posts=[make_post('a')])
        (batch.path / 'manifest.json').unlink()

        with self.assertRaises(BatchValidationError):
            batch.verify()

    def test_manifest_for_another_batch_fails(self):
        batch = self._claim(posts=[make_post('a')])
        manifest_path = batch.path / 'manifest.json'
        document = json.loads(manifest_path.read_text('utf-8'))
        document['batch_id'] = contract.new_batch_id()
        manifest_path.write_text(json.dumps(document), encoding='utf-8')

        with self.assertRaises(BatchValidationError):
            batch.verify()

    def test_unsupported_contract_version_fails(self):
        batch = self._claim(posts=[make_post('a')])
        manifest_path = batch.path / 'manifest.json'
        document = json.loads(manifest_path.read_text('utf-8'))
        document['contract_version'] = 99
        manifest_path.write_text(json.dumps(document), encoding='utf-8')

        with self.assertRaises(BatchValidationError):
            batch.verify()

    @staticmethod
    def _resync_checksum(batch, name):
        """Rewrite the manifest checksum so content checks, not hashes, fail."""
        import hashlib

        manifest_path = batch.path / 'manifest.json'
        document = json.loads(manifest_path.read_text('utf-8'))
        digest = hashlib.sha256((batch.path / name).read_bytes()).hexdigest()
        for entry in document['files']:
            if entry['name'] == name:
                entry['sha256'] = digest
                entry['record_count'] = 1
        manifest_path.write_text(json.dumps(document), encoding='utf-8')


class ResolutionTests(_SpoolCase):
    def test_published_batch_is_archived(self):
        batch_id = self.write_batch(posts=[make_post('a')]).batch_id
        batch = self.spool.claim_next()
        batch.mark_published()

        self.assertEqual(self.spool.batch_ids(STAGE_PUBLISHED), [batch_id])
        self.assertEqual(self.spool.batch_ids(STAGE_PROCESSING), [])

    def test_failed_batch_is_quarantined_with_a_reason(self):
        self.write_batch(posts=[make_post('a')])
        batch = self.spool.claim_next()
        batch.mark_failed('manifest checksum mismatch')

        failed = self.spool.batch_ids(STAGE_FAILED)
        self.assertEqual(failed, [batch.batch_id])
        note = json.loads(
            (self.root / STAGE_FAILED / failed[0] / 'failure.json').read_text('utf-8')
        )
        self.assertEqual(note['reason'], 'manifest checksum mismatch')
        self.assertEqual(note['batch_id'], batch.batch_id)

    def test_released_batch_returns_to_the_queue(self):
        batch_id = self.write_batch(posts=[make_post('a')]).batch_id
        batch = self.spool.claim_next()
        batch.release()

        self.assertEqual(self.ready_ids(), [batch_id])
        self.assertEqual(self.spool.batch_ids(STAGE_PROCESSING), [])

    def test_a_released_batch_can_be_claimed_and_published_again(self):
        self.write_batch(posts=[make_post('a')])
        self.spool.claim_next().release()

        retried = self.spool.claim_next()
        retried.verify()
        retried.mark_published()

        self.assertEqual(len(self.spool.batch_ids(STAGE_PUBLISHED)), 1)

    def test_a_batch_cannot_be_resolved_twice(self):
        self.write_batch(posts=[make_post('a')])
        batch = self.spool.claim_next()
        batch.mark_published()
        with self.assertRaises(SpoolError):
            batch.mark_published()

    def test_a_transient_failure_leaves_the_batch_claimable(self):
        # Nothing is moved when the destination is merely unreachable, so the
        # next run picks the same batch up rather than losing its records.
        batch_id = self.write_batch(posts=[make_post('a')]).batch_id
        self.spool.claim_next()

        self.assertEqual(self.spool.claim_next().batch_id, batch_id)

    def test_published_batch_content_is_still_readable(self):
        self.write_batch(posts=[make_post('a')])
        batch = self.spool.claim_next()
        batch.mark_published()
        self.assertTrue((batch.path / 'posts.ndjson').is_file())

    def test_archiving_a_batch_the_archive_already_holds_completes_quietly(self):
        # A publisher that commits, archives, and then dies before clearing its
        # working copy leaves the same id in both `processing` and `published`.
        # Refusing to archive would strand that batch and stop the drain on
        # every later run, so the redundant working copy is discarded instead.
        batch_id = self.write_batch(posts=[make_post('a')]).batch_id
        self.spool.claim_next().mark_published()
        shutil.copytree(
            self.spool.batch_path(STAGE_PUBLISHED, batch_id),
            self.spool.batch_path(STAGE_PROCESSING, batch_id),
        )

        stranded = self.spool.claim_next()
        self.assertEqual(stranded.batch_id, batch_id)
        stranded.mark_published()

        self.assertEqual(self.spool.batch_ids(STAGE_PROCESSING), [])
        self.assertEqual(self.spool.batch_ids(STAGE_PUBLISHED), [batch_id])
        self.assertTrue((stranded.path / 'posts.ndjson').is_file())


class ReplayTests(_SpoolCase):
    def test_a_reclaimed_batch_yields_identical_records(self):
        # A publisher that commits and then dies re-reads the same batch. The
        # spool must hand back byte-identical content so the destination's
        # batch ledger can recognise and skip it.
        self.write_batch(
            posts=[make_post('a'), make_post('b')],
            checkpoints=[make_checkpoint()],
        )
        first = self.spool.claim_next()
        first_records = list(first.records(contract.KIND_POSTS))

        second = self.spool.claim_next()
        self.assertEqual(second.batch_id, first.batch_id)
        self.assertEqual(list(second.records(contract.KIND_POSTS)), first_records)

    def test_batch_ids_are_stable_across_the_whole_lifecycle(self):
        batch_id = self.write_batch(posts=[make_post('a')]).batch_id
        batch = self.spool.claim_next()
        self.assertEqual(batch.batch_id, batch_id)
        batch.mark_published()
        self.assertEqual(self.spool.batch_ids(STAGE_PUBLISHED), [batch_id])


class RetentionTests(_SpoolCase):
    def _publish_at(self, moment):
        batch_id = contract.new_batch_id(moment)
        with self.spool.new_batch(
            collector_version=COLLECTOR_VERSION, batch_id=batch_id
        ) as writer:
            writer.add_posts([make_post('a')])
        batch = self.spool.claim_next()
        batch.mark_published()
        return batch_id

    def test_old_published_batches_are_pruned(self):
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        old = self._publish_at(now - datetime.timedelta(days=30))
        recent = self._publish_at(now - datetime.timedelta(days=2))

        removed = self.spool.prune_published(14, now=now)

        self.assertEqual(removed, 1)
        self.assertEqual(self.spool.batch_ids(STAGE_PUBLISHED), [recent])
        self.assertNotIn(old, self.spool.batch_ids(STAGE_PUBLISHED))

    def test_failed_batches_are_never_pruned(self):
        now = datetime.datetime(2026, 6, 1, tzinfo=UTC)
        batch_id = contract.new_batch_id(now - datetime.timedelta(days=365))
        with self.spool.new_batch(
            collector_version=COLLECTOR_VERSION, batch_id=batch_id
        ) as writer:
            writer.add_posts([make_post('a')])
        self.spool.claim_next().mark_failed('bad batch')

        self.spool.prune_published(1, now=now)

        self.assertEqual(self.spool.batch_ids(STAGE_FAILED), [batch_id])

    def test_retention_can_be_disabled(self):
        self._publish_at(datetime.datetime(2020, 1, 1, tzinfo=UTC))
        self.assertEqual(self.spool.prune_published(0), 0)
        self.assertEqual(len(self.spool.batch_ids(STAGE_PUBLISHED)), 1)


class MonitoringTests(_SpoolCase):
    def test_queue_stats_report_the_oldest_waiting_batch(self):
        now = datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        oldest = contract.new_batch_id(now - datetime.timedelta(hours=3))
        with self.spool.new_batch(
            collector_version=COLLECTOR_VERSION, batch_id=oldest
        ) as writer:
            writer.add_posts([make_post('a')])
        with self.spool.new_batch(
            collector_version=COLLECTOR_VERSION,
            batch_id=contract.new_batch_id(now - datetime.timedelta(minutes=5)),
        ) as writer:
            writer.add_posts([make_post('b')])

        stats = self.spool.queue_stats(now=now)

        self.assertEqual(stats['counts'][STAGE_READY], 2)
        self.assertEqual(stats['oldest_outstanding_batch_id'], oldest)
        self.assertEqual(stats['oldest_outstanding_age_seconds'], 3 * 3600)
        self.assertGreater(stats['disk_bytes'], 0)

    def test_an_empty_queue_reports_no_age(self):
        stats = self.spool.queue_stats()
        self.assertIsNone(stats['oldest_outstanding_batch_id'])
        self.assertIsNone(stats['oldest_outstanding_age_seconds'])

    def test_batch_creation_time_is_recoverable_from_its_id(self):
        moment = datetime.datetime(2026, 3, 4, 5, 6, 7, 891011, tzinfo=UTC)
        batch_id = contract.new_batch_id(moment)
        self.assertEqual(spool_module.batch_id_created_at(batch_id), moment)


if __name__ == '__main__':
    unittest.main()

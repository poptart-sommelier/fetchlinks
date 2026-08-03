"""Tests for the in-memory result a collection cycle produces."""

import tempfile
import unittest
from pathlib import Path

from pipeline.collection import CollectionResult, FollowsSnapshot
from pipeline.contract import (
    BlueskyFollowRecord,
    CheckpointRecord,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
    utc_now,
)
from pipeline.spool import Spool


def _post(unique_id='u1'):
    return PostRecord(
        unique_id=unique_id,
        source='https://feed.example',
        source_type='rss',
        description='',
        posted_at='2026-01-01T00:00:00Z',
        urls=['https://example.com/story'],
    )


def _observation(normalized_url='https://feed.example/rss.xml', success=True):
    return RssObservationRecord(
        normalized_url=normalized_url,
        feed_url=normalized_url,
        success=success,
        status=200 if success else None,
        observed_at=utc_now(),
    )


def _checkpoint(source_type='reddit', source_key='netsec', cursor='t3_1'):
    return CheckpointRecord(
        source_type=source_type,
        source_key=source_key,
        cursor=cursor,
        observed_at=utc_now(),
    )


class AccumulationTests(unittest.TestCase):
    def test_a_new_result_is_empty(self):
        result = CollectionResult()

        self.assertTrue(result.is_empty)
        self.assertEqual(result.posts, [])
        self.assertEqual(result.rss_observations, [])
        self.assertEqual(result.checkpoints, [])
        self.assertIsNone(result.bluesky_follows)
        self.assertEqual(result.mastodon_follows, {})

    def test_adding_any_kind_makes_it_non_empty(self):
        for populate in (
            lambda r: r.add_posts([_post()]),
            lambda r: r.add_rss_observations([_observation()]),
            lambda r: r.add_checkpoints([_checkpoint()]),
            lambda r: r.set_bluesky_follows([]),
            lambda r: r.set_mastodon_follows('infosec', []),
        ):
            with self.subTest(populate=populate):
                result = CollectionResult()
                populate(result)
                self.assertFalse(result.is_empty)

    def test_add_posts_accepts_a_generator(self):
        result = CollectionResult()
        result.add_posts(_post(f'u{index}') for index in range(3))

        self.assertEqual([record.unique_id for record in result.posts],
                         ['u0', 'u1', 'u2'])

    def test_summary_counts_every_kind(self):
        result = CollectionResult()
        result.add_posts([_post('u1'), _post('u2')])
        result.add_rss_observations([_observation()])
        result.add_checkpoints([_checkpoint()])
        result.set_bluesky_follows([
            BlueskyFollowRecord(did='did:a', handle='a.bsky.social'),
        ])
        result.set_mastodon_follows('infosec', [])

        self.assertEqual(result.summary(), {
            'posts': 2,
            'rss_observations': 1,
            'checkpoints': 1,
            'bluesky_follows': 1,
            'mastodon_follows[infosec]': 0,
        })

    def test_summary_omits_snapshots_that_were_not_collected(self):
        self.assertEqual(sorted(CollectionResult().summary()),
                         ['checkpoints', 'posts', 'rss_observations'])


class SnapshotAbsenceTests(unittest.TestCase):
    """Absent means "leave it alone"; empty means "the account follows nobody"."""

    def test_an_absent_bluesky_snapshot_is_distinct_from_an_empty_one(self):
        absent = CollectionResult()
        empty = CollectionResult()
        empty.set_bluesky_follows([])

        self.assertIsNone(absent.bluesky_follows)
        self.assertIsNotNone(empty.bluesky_follows)
        self.assertEqual(empty.bluesky_follows.records, ())
        self.assertTrue(absent.is_empty)
        self.assertFalse(empty.is_empty)

    def test_mastodon_snapshots_are_scoped_per_instance(self):
        result = CollectionResult()
        result.set_mastodon_follows('infosec', [
            MastodonFollowRecord(account_id='1', acct='abe'),
        ])

        self.assertEqual(list(result.mastodon_follows), ['infosec'])
        self.assertEqual(result.mastodon_follows['infosec'].scope, 'infosec')

    def test_snapshots_record_when_they_were_observed(self):
        result = CollectionResult()
        result.set_bluesky_follows([], observed_at='2026-01-01T00:00:00Z')

        self.assertEqual(result.bluesky_follows.observed_at, '2026-01-01T00:00:00Z')

    def test_snapshot_records_are_immutable(self):
        snapshot = FollowsSnapshot.create([_post()])

        self.assertIsInstance(snapshot.records, tuple)


class ExtendTests(unittest.TestCase):
    def test_extend_concatenates_records_in_order(self):
        first = CollectionResult()
        first.add_posts([_post('u1')])
        first.add_checkpoints([_checkpoint(source_key='netsec')])
        second = CollectionResult()
        second.add_posts([_post('u2')])
        second.add_checkpoints([_checkpoint(source_key='blueteamsec')])

        merged = first.extend(second)

        self.assertIs(merged, first)
        self.assertEqual([record.unique_id for record in first.posts], ['u1', 'u2'])
        self.assertEqual([cp.source_key for cp in first.checkpoints],
                         ['netsec', 'blueteamsec'])

    def test_extend_does_not_erase_a_snapshot_with_an_absent_one(self):
        """A later source that skipped follows must not clear an earlier one."""
        first = CollectionResult()
        first.set_bluesky_follows([BlueskyFollowRecord(did='did:a', handle='a')])

        first.extend(CollectionResult())

        self.assertIsNotNone(first.bluesky_follows)
        self.assertEqual(len(first.bluesky_follows.records), 1)

    def test_extend_replaces_a_snapshot_with_a_newer_one(self):
        first = CollectionResult()
        first.set_bluesky_follows([BlueskyFollowRecord(did='did:a', handle='a')])
        second = CollectionResult()
        second.set_bluesky_follows([])

        first.extend(second)

        self.assertEqual(first.bluesky_follows.records, ())

    def test_extend_merges_mastodon_scopes_without_dropping_others(self):
        first = CollectionResult()
        first.set_mastodon_follows('infosec', [])
        second = CollectionResult()
        second.set_mastodon_follows('hachyderm', [])

        first.extend(second)

        self.assertEqual(sorted(first.mastodon_follows), ['hachyderm', 'infosec'])


class WriteToTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.spool = Spool(Path(self._tmp.name)).initialize()

    def _commit(self, result):
        with self.spool.new_batch(collector_version='test/1', catalog_revision='rev-1') as batch:
            result.write_to(batch)
        return batch

    def test_an_empty_result_writes_nothing(self):
        batch = self._commit(CollectionResult())

        self.assertTrue(batch.is_empty)
        self.assertEqual(self.spool.batch_ids('ready'), [])

    def test_only_kinds_with_content_produce_files(self):
        result = CollectionResult()
        result.add_posts([_post()])

        self._commit(result)

        batch_id = self.spool.batch_ids('ready')[0]
        names = sorted(entry.name
                       for entry in self.spool.batch_path('ready', batch_id).iterdir())
        self.assertEqual(names, ['manifest.json', 'posts.ndjson'])

    def test_every_kind_reaches_the_batch(self):
        result = CollectionResult()
        result.add_posts([_post()])
        result.add_rss_observations([_observation()])
        result.add_checkpoints([_checkpoint()])
        result.set_bluesky_follows([
            BlueskyFollowRecord(did='did:a', handle='a.bsky.social'),
        ])
        result.set_mastodon_follows('infosec', [
            MastodonFollowRecord(account_id='1', acct='abe'),
        ])

        self._commit(result)

        batch_id = self.spool.batch_ids('ready')[0]
        claimed = self.spool.claim_next()
        self.assertEqual(claimed.batch_id, batch_id)
        claimed.verify()
        self.assertEqual(len(list(claimed.records('posts'))), 1)
        self.assertEqual(len(list(claimed.records('rss_observations'))), 1)
        self.assertEqual(len(list(claimed.records('checkpoints'))), 1)
        self.assertEqual(len(list(claimed.records('bluesky_follows'))), 1)
        self.assertEqual(len(list(claimed.records('mastodon_follows', 'infosec'))), 1)

    def test_an_empty_snapshot_still_produces_a_file(self):
        """The publisher needs the file to know it should clear the list."""
        result = CollectionResult()
        result.set_bluesky_follows([])

        self._commit(result)

        claimed = self.spool.claim_next()
        self.assertIsNotNone(claimed)
        claimed.verify()
        self.assertEqual(list(claimed.records('bluesky_follows')), [])

    def test_snapshot_observation_time_is_carried_into_the_batch(self):
        result = CollectionResult()
        result.set_bluesky_follows([], observed_at='2026-01-01T00:00:00Z')

        self._commit(result)

        claimed = self.spool.claim_next()
        entries = [entry for entry in claimed.manifest.files
                   if entry.kind == 'bluesky_follows']
        self.assertEqual(entries[0].observed_at, '2026-01-01T00:00:00Z')

    def test_mastodon_scopes_are_written_in_a_stable_order(self):
        result = CollectionResult()
        result.set_mastodon_follows('infosec', [])
        result.set_mastodon_follows('hachyderm', [])

        self._commit(result)

        claimed = self.spool.claim_next()
        scopes = [entry.scope for entry in claimed.manifest.files
                  if entry.kind == 'mastodon_follows']
        self.assertEqual(scopes, ['hachyderm', 'infosec'])


if __name__ == '__main__':
    unittest.main()

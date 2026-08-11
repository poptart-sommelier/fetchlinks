"""Integration tests for applying batches to PostgreSQL.

Everything here runs against a real server; see ``pg_support`` for how to point
the suite at one.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.pg_support import PostgresTestCase, available

from pipeline.contract import (
    BlueskyFollowRecord,
    CheckpointRecord,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
)
from pipeline.spool import Spool
from utils import build_hash

if available():
    import psycopg

    from publisher.apply import apply_batch


def post(unique_id='uid-1', urls=('https://example.com/a',), **kwargs):
    fields = dict(
        unique_id=unique_id,
        source='https://feed.example/rss',
        source_type='rss',
        posted_at='2026-01-02T03:04:05Z',
        urls=tuple(urls),
        author='Anna',
        description='A post',
        direct_link='https://feed.example/post',
    )
    fields.update(kwargs)
    return PostRecord(**fields)


def observation(success=True, **kwargs):
    fields = dict(
        normalized_url='https://feed.example/rss',
        feed_url='https://feed.example/rss',
        observed_at='2026-01-02T03:00:00Z',
        success=success,
        status=200 if success else 500,
        latest_entry_at='2026-01-02T02:00:00Z',
        site_link='https://feed.example',
        etag='"abc"',
        last_modified='Wed, 01 Jan 2026 00:00:00 GMT',
    )
    fields.update(kwargs)
    return RssObservationRecord(**fields)


def checkpoint(**kwargs):
    fields = dict(
        source_type='reddit',
        source_key='netsec',
        cursor='t3_aaa',
        observed_at='2026-01-02T03:00:00Z',
        source_url='https://www.reddit.com/r/netsec',
    )
    fields.update(kwargs)
    return CheckpointRecord(**fields)


def _downgrade_batch_to_v1(path: Path) -> None:
    """Rewrite a ready batch as the previous contract version.

    The collector can only write the newest version, so a batch left over from
    before an upgrade has to be manufactured. Doing it by editing a real batch
    rather than hand-writing files means the checksums, counts and manifest
    rules are all still the genuine ones.
    """
    from pipeline import contract

    posts = path / 'posts.ndjson'
    lines = [
        contract.dumps_line({
            key: value for key, value in contract.loads_line(line).items()
            if key not in ('channel_key', 'channel_label',
                           'actor_key', 'actor_label')
        })
        for line in posts.read_text(encoding='utf-8').splitlines()
    ]
    body = ''.join(f'{line}\n' for line in lines)
    posts.write_text(body, encoding='utf-8', newline='')

    manifest_path = path / contract.MANIFEST_FILENAME
    document = json.loads(manifest_path.read_text(encoding='utf-8'))
    document['contract_version'] = 1
    for entry in document['files']:
        if entry['name'] == 'posts.ndjson':
            entry['sha256'] = hashlib.sha256(body.encode('utf-8')).hexdigest()
    manifest_path.write_text(
        contract.dumps_document(document), encoding='utf-8', newline=''
    )


class BatchBuilderMixin:
    """Writes real batches into a throwaway spool."""

    def setUpSpool(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.spool = Spool(Path(tmp.name) / 'outbox').initialize()

    def queue(self, *, posts=(), observations=(), checkpoints=(),
              bluesky_follows=None, mastodon_follows=None,
              batch_id=None, catalog_revision='rev-1',
              follows_observed_at=None):
        with self.spool.new_batch(
            collector_version='test/1',
            catalog_revision=catalog_revision,
            batch_id=batch_id,
        ) as batch:
            batch.add_posts(posts)
            batch.add_rss_observations(observations)
            batch.add_checkpoints(checkpoints)
            if bluesky_follows is not None:
                batch.set_bluesky_follows(
                    bluesky_follows, observed_at=follows_observed_at
                )
            for scope, records in (mastodon_follows or {}).items():
                batch.set_mastodon_follows(
                    scope, records, observed_at=follows_observed_at
                )
        return batch.batch_id

    def publish_next(self):
        claimed = self.spool.claim_next()
        self.assertIsNotNone(claimed, 'expected a batch to publish')
        outcome = apply_batch(self.conn, claimed)
        claimed.mark_published()
        return outcome


class PostPublishTests(BatchBuilderMixin, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def test_inserts_post_and_urls(self):
        self.queue(posts=[post(urls=('https://a.example/1', 'https://b.example/2'))])
        outcome = self.publish_next()

        self.assertEqual(outcome.posts_inserted, 1)
        self.assertEqual(outcome.urls_inserted, 2)
        self.assertEqual(self.count('content.posts'), 1)

        rows = self.rows(
            'SELECT position, url, url_hash FROM content.post_urls ORDER BY position'
        )
        self.assertEqual([row[0] for row in rows], [0, 1])
        self.assertEqual(rows[0][1], 'https://a.example/1')
        self.assertEqual(rows[0][2], build_hash('https://a.example/1'))

    def test_post_fields_land_in_the_contract_named_columns(self):
        self.queue(posts=[post()])
        self.publish_next()
        row = self.rows(
            'SELECT unique_id, source, source_type, author, description, '
            'direct_link, posted_at FROM content.posts'
        )[0]
        self.assertEqual(row[0], 'uid-1')
        self.assertEqual(row[2], 'rss')
        self.assertEqual(row[3], 'Anna')
        self.assertEqual(row[6].isoformat(), '2026-01-02T03:04:05+00:00')

    def test_duplicate_unique_id_across_batches_inserts_once(self):
        self.queue(posts=[post(urls=('https://a.example/1',))])
        self.publish_next()
        self.queue(posts=[post(urls=('https://a.example/1',))])
        outcome = self.publish_next()

        self.assertEqual(outcome.posts_inserted, 0)
        self.assertEqual(outcome.posts_skipped, 1)
        self.assertEqual(self.count('content.posts'), 1)
        self.assertEqual(self.count('content.post_urls'), 1)

    def test_deleting_a_post_cascades_to_its_urls(self):
        self.queue(posts=[post(urls=('https://a.example/1', 'https://b.example/2'))])
        self.publish_next()
        with self.conn.cursor() as cur:
            cur.execute('DELETE FROM content.posts')
        self.conn.commit()
        self.assertEqual(self.count('content.post_urls'), 0)

    def test_repeated_url_in_one_post_is_rejected_by_the_contract(self):
        # Belt and braces: the schema refuses duplicate URLs, and the
        # (post_id, url_hash) constraint refuses them again if it ever doesn't.
        from pipeline.contract import ContractError

        with self.assertRaises(ContractError):
            self.queue(posts=[post(urls=('https://a.example/1',
                                         'https://a.example/1'))])

    def test_url_host_is_derived_and_normalized(self):
        self.queue(posts=[post(urls=(
            'https://WWW.Example.com:8443/a?x=1#f',
            'https://blog.example.com/b',
        ))])
        self.publish_next()
        hosts = [row[0] for row in self.rows(
            'SELECT url_host FROM content.post_urls ORDER BY position'
        )]
        # `www.` goes, the port and path go, and nothing else does: collapsing
        # other subdomains would merge distinct publishers into one target.
        self.assertEqual(hosts, ['example.com', 'blog.example.com'])

    def test_url_host_follows_an_unshortened_url_once_resolved(self):
        self.queue(posts=[post(urls=('https://bit.ly/abc',))])
        self.publish_next()
        self.assertEqual(
            self.scalar('SELECT url_host FROM content.post_urls'), 'bit.ly'
        )
        with self.conn.cursor() as cur:
            cur.execute("UPDATE content.post_urls "
                        "SET unshortened_url = 'https://www.arstechnica.com/x'")
        self.conn.commit()
        self.assertEqual(
            self.scalar('SELECT url_host FROM content.post_urls'), 'arstechnica.com'
        )


class PostOccurrenceTests(BatchBuilderMixin, PostgresTestCase):
    """Every arrival of a link is recorded, even when the post already exists.

    A post's identity is a digest of its URL set, so the same article really
    does arrive more than once. Keeping only the first arrival is what made the
    question "which sources are worth keeping" unanswerable.
    """

    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def occurrences(self):
        return self.rows(
            'SELECT source_type, channel_key, actor_key, actor_label, direct_link '
            'FROM content.post_occurrences ORDER BY source_type, actor_key'
        )

    def test_first_arrival_gets_an_occurrence(self):
        self.queue(posts=[post(channel_key='https://feed.example/rss',
                               channel_label='The Feed')])
        outcome = self.publish_next()
        self.assertEqual(outcome.occurrences_applied, 1)
        self.assertEqual(
            self.occurrences(),
            [('rss', 'https://feed.example/rss', '', '', 'https://feed.example/post')],
        )

    def test_a_second_source_for_the_same_link_is_kept(self):
        urls = ('https://arstechnica.com/article',)
        self.queue(posts=[post(unique_id='uid-x', urls=urls,
                               channel_key='https://feed.example/rss')])
        self.publish_next()
        self.queue(posts=[post(
            unique_id='uid-x', urls=urls, source_type='reddit',
            source='https://www.reddit.com/r/apple',
            direct_link='https://www.reddit.com/r/apple/comments/1',
            channel_key='apple', channel_label='r/apple',
            actor_key='someuser', actor_label='someuser',
        )])
        outcome = self.publish_next()

        self.assertEqual(outcome.posts_inserted, 0,
                         'the article is the same article')
        self.assertEqual(outcome.posts_skipped, 1)
        self.assertEqual(self.count('content.posts'), 1)
        self.assertEqual(self.count('content.post_occurrences'), 2)
        self.assertEqual(
            [(row[0], row[1], row[2]) for row in self.occurrences()],
            [('reddit', 'apple', 'someuser'), ('rss', 'https://feed.example/rss', '')],
        )

    def test_two_accounts_in_one_channel_are_separate_occurrences(self):
        urls = ('https://arstechnica.com/article',)
        for actor in ('alice', 'bob'):
            self.queue(posts=[post(
                unique_id='uid-x', urls=urls, source_type='reddit',
                channel_key='apple', actor_key=actor, actor_label=actor,
            )])
            self.publish_next()
        self.assertEqual(self.count('content.posts'), 1)
        self.assertEqual(
            [row[2] for row in self.occurrences()], ['alice', 'bob']
        )

    def test_republishing_the_same_origin_adds_nothing(self):
        for _ in range(2):
            self.queue(posts=[post(unique_id='uid-x', source_type='reddit',
                                   channel_key='apple', actor_key='alice',
                                   actor_label='alice')])
            self.publish_next()
        self.assertEqual(self.count('content.post_occurrences'), 1)

    def test_a_renamed_account_keeps_its_occurrence_and_refreshes_the_label(self):
        """The key is the identity; the label is only ever display text."""
        self.queue(posts=[post(unique_id='uid-x', source_type='bluesky',
                               actor_key='did:plc:abc', actor_label='old.handle')])
        self.publish_next()
        self.queue(posts=[post(unique_id='uid-x', source_type='bluesky',
                               actor_key='did:plc:abc', actor_label='new.handle')])
        self.publish_next()

        self.assertEqual(self.count('content.post_occurrences'), 1)
        self.assertEqual(self.occurrences()[0][3], 'new.handle')

    def test_a_version_1_batch_still_publishes_with_an_unidentified_origin(self):
        """An upgrade must not strand batches already sitting in the spool."""
        batch_id = self.queue(posts=[post()])
        path = self.spool.batch_path('ready', batch_id)
        _downgrade_batch_to_v1(path)

        outcome = self.publish_next()
        self.assertEqual(outcome.posts_inserted, 1)
        self.assertEqual(outcome.occurrences_applied, 1)
        self.assertEqual(self.occurrences(), [('rss', '', '', '', 'https://feed.example/post')])

    def test_deleting_a_post_cascades_to_its_occurrences(self):
        self.queue(posts=[post(channel_key='https://feed.example/rss')])
        self.publish_next()
        with self.conn.cursor() as cur:
            cur.execute('DELETE FROM content.posts')
        self.conn.commit()
        self.assertEqual(self.count('content.post_occurrences'), 0)


class BatchReplayTests(BatchBuilderMixin, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def test_same_batch_id_applies_only_once(self):
        self.queue(posts=[post()], observations=[observation(success=False)])

        # Publish, but leave the batch in `processing` — exactly the state a
        # publisher that committed and then died before archiving leaves behind.
        claimed = self.spool.claim_next()
        first = apply_batch(self.conn, claimed)
        self.assertFalse(first.already_published)
        self.assertEqual(
            self.scalar('SELECT consecutive_failures FROM content.rss_feed_health'), 1
        )

        # The next run recovers it from `processing` and retries.
        recovered = self.spool.claim_next()
        self.assertEqual(recovered.batch_id, claimed.batch_id)
        second = apply_batch(self.conn, recovered)
        recovered.mark_published()

        self.assertTrue(second.already_published)
        self.assertEqual(second.posts_inserted, 0)
        self.assertEqual(self.count('content.posts'), 1)
        self.assertEqual(
            self.scalar('SELECT consecutive_failures FROM content.rss_feed_health'), 1,
            'a replayed batch must not inflate the failure counter',
        )
        self.assertEqual(self.count('content.published_batches'), 1)

    def test_ledger_records_the_manifest_metadata(self):
        self.queue(posts=[post()], catalog_revision='rev-xyz')
        self.publish_next()
        row = self.rows(
            'SELECT contract_version, collector_version, catalog_revision, '
            'record_count, posts_inserted, urls_inserted '
            'FROM content.published_batches'
        )[0]
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], 'test/1')
        self.assertEqual(row[2], 'rev-xyz')
        self.assertEqual(row[3], 1)
        self.assertEqual(row[4], 1)
        self.assertEqual(row[5], 1)

    def test_failure_mid_batch_leaves_nothing_behind(self):
        # Posts are applied before checkpoints, so a failure at the checkpoint
        # step is the sharpest test of the transaction boundary: the posts are
        # already written when it happens.
        self.queue(posts=[post(unique_id='a'), post(unique_id='b')],
                   checkpoints=[checkpoint()])
        claimed = self.spool.claim_next()

        with patch('publisher.apply._apply_checkpoints',
                   side_effect=psycopg.OperationalError('connection lost')):
            with self.assertRaises(psycopg.OperationalError):
                apply_batch(self.conn, claimed)

        self.assertEqual(self.count('content.posts'), 0)
        self.assertEqual(self.count('content.post_urls'), 0)
        self.assertEqual(self.count('content.published_batches'), 0,
                         'the ledger claim must roll back with the content')

        # And the batch is still claimable, so nothing was lost.
        self.assertIsNotNone(self.spool.claim_next())


class RssHealthTests(BatchBuilderMixin, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def health(self):
        return self.rows(
            'SELECT last_status, last_error, consecutive_failures, etag, '
            'last_modified, latest_entry_at, site_link, last_success_at '
            'FROM content.rss_feed_health'
        )[0]

    def test_success_stores_validators_and_clears_failures(self):
        self.queue(observations=[observation()])
        self.publish_next()
        row = self.health()
        self.assertEqual(row[0], 200)
        self.assertIsNone(row[1])
        self.assertEqual(row[2], 0)
        self.assertEqual(row[3], '"abc"')
        self.assertEqual(row[6], 'https://feed.example')
        self.assertIsNotNone(row[7])

    def test_consecutive_failures_accumulate_across_batches(self):
        for index in range(3):
            self.queue(observations=[observation(
                success=False, error='boom',
                observed_at=f'2026-01-0{index + 2}T03:00:00Z',
            )])
            self.publish_next()
        row = self.health()
        self.assertEqual(row[2], 3)
        self.assertEqual(row[1], 'boom')
        self.assertIsNone(row[7], 'a run of failures never records a success')

    def test_success_after_failures_resets_the_counter(self):
        self.queue(observations=[observation(success=False, error='boom')])
        self.publish_next()
        self.queue(observations=[observation(observed_at='2026-01-03T03:00:00Z')])
        self.publish_next()
        row = self.health()
        self.assertEqual(row[2], 0)
        self.assertIsNone(row[1])

    def test_not_modified_keeps_the_last_known_entry_and_site_link(self):
        self.queue(observations=[observation()])
        self.publish_next()
        # A 304 is a success that carries no feed body, so it has neither.
        self.queue(observations=[observation(
            status=304, latest_entry_at=None, site_link=None,
            observed_at='2026-01-03T03:00:00Z',
        )])
        self.publish_next()
        row = self.health()
        self.assertEqual(row[0], 304)
        self.assertIsNotNone(row[5])
        self.assertEqual(row[6], 'https://feed.example')

    def test_older_observation_does_not_overwrite_newer_health(self):
        self.queue(observations=[observation(observed_at='2026-02-01T00:00:00Z')])
        self.publish_next()
        self.queue(observations=[observation(
            success=False, error='stale', observed_at='2026-01-01T00:00:00Z',
        )])
        self.publish_next()
        row = self.health()
        self.assertEqual(row[2], 0)
        self.assertIsNone(row[1])


class CheckpointTests(BatchBuilderMixin, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def test_reddit_checkpoint_is_stored(self):
        self.queue(checkpoints=[checkpoint()])
        outcome = self.publish_next()
        self.assertEqual(outcome.checkpoints_applied, 1)
        row = self.rows(
            'SELECT subreddit, last_seen_fullname, source_url '
            'FROM content.reddit_state'
        )[0]
        self.assertEqual(row, ('netsec', 't3_aaa', 'https://www.reddit.com/r/netsec'))

    def test_newer_checkpoint_advances_the_cursor(self):
        self.queue(checkpoints=[checkpoint()])
        self.publish_next()
        self.queue(checkpoints=[checkpoint(
            cursor='t3_bbb', observed_at='2026-01-03T03:00:00Z'
        )])
        self.publish_next()
        self.assertEqual(
            self.scalar('SELECT last_seen_fullname FROM content.reddit_state'),
            't3_bbb',
        )

    def test_older_checkpoint_cannot_rewind_the_cursor(self):
        self.queue(checkpoints=[checkpoint(
            cursor='t3_new', observed_at='2026-02-01T00:00:00Z'
        )])
        self.publish_next()
        self.queue(checkpoints=[checkpoint(
            cursor='t3_old', observed_at='2026-01-01T00:00:00Z'
        )])
        outcome = self.publish_next()
        self.assertEqual(outcome.checkpoints_skipped, 1)
        self.assertEqual(
            self.scalar('SELECT last_seen_fullname FROM content.reddit_state'),
            't3_new',
        )

    def test_bluesky_and_mastodon_checkpoints_reach_their_own_tables(self):
        self.queue(checkpoints=[
            checkpoint(source_type='bluesky', source_key='timeline',
                       cursor='cur-1', source_url='https://bsky.app'),
            checkpoint(source_type='mastodon', source_key='infosec',
                       cursor='1234', source_url='https://infosec.exchange'),
        ])
        outcome = self.publish_next()
        self.assertEqual(outcome.checkpoints_applied, 2)
        self.assertEqual(
            self.rows('SELECT source_key, cursor, source_url FROM content.bluesky_state')[0],
            ('timeline', 'cur-1', 'https://bsky.app'),
        )
        self.assertEqual(
            self.rows('SELECT source_name, last_seen_id, instance_url '
                      'FROM content.mastodon_state')[0],
            ('infosec', '1234', 'https://infosec.exchange'),
        )

    def test_unknown_source_type_is_skipped_not_fatal(self):
        self.queue(
            posts=[post()],
            checkpoints=[checkpoint(source_type='lemmy', source_key='tech')],
        )
        outcome = self.publish_next()
        self.assertEqual(outcome.checkpoints_skipped, 1)
        self.assertEqual(outcome.posts_inserted, 1,
                         'one unknown checkpoint must not strand a batch of posts')


class FollowsSnapshotTests(BatchBuilderMixin, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.setUpSpool()

    def test_snapshot_replaces_rather_than_merges(self):
        self.queue(bluesky_follows=[
            BlueskyFollowRecord('did:a', 'a.example'),
            BlueskyFollowRecord('did:b', 'b.example'),
        ], follows_observed_at='2026-01-01T00:00:00Z')
        self.publish_next()
        self.assertEqual(self.count('content.bluesky_follows'), 2)

        self.queue(bluesky_follows=[BlueskyFollowRecord('did:b', 'b.example')],
                   follows_observed_at='2026-01-02T00:00:00Z')
        self.publish_next()
        self.assertEqual(
            [row[0] for row in self.rows('SELECT did FROM content.bluesky_follows')],
            ['did:b'],
        )

    def test_empty_snapshot_clears_the_list(self):
        self.queue(bluesky_follows=[BlueskyFollowRecord('did:a', 'a.example')],
                   follows_observed_at='2026-01-01T00:00:00Z')
        self.publish_next()
        # "follows nobody" is a real observation, not a missing one.
        self.queue(bluesky_follows=[], follows_observed_at='2026-01-02T00:00:00Z')
        self.publish_next()
        self.assertEqual(self.count('content.bluesky_follows'), 0)

    def test_absent_snapshot_leaves_the_list_alone(self):
        self.queue(bluesky_follows=[BlueskyFollowRecord('did:a', 'a.example')],
                   follows_observed_at='2026-01-01T00:00:00Z')
        self.publish_next()
        self.queue(posts=[post()])
        self.publish_next()
        self.assertEqual(self.count('content.bluesky_follows'), 1)

    def test_stale_snapshot_cannot_reinstate_an_old_list(self):
        self.queue(bluesky_follows=[BlueskyFollowRecord('did:new', 'new.example')],
                   follows_observed_at='2026-02-01T00:00:00Z')
        self.publish_next()
        self.queue(bluesky_follows=[BlueskyFollowRecord('did:old', 'old.example')],
                   follows_observed_at='2026-01-01T00:00:00Z')
        outcome = self.publish_next()

        self.assertEqual(outcome.follows_stale, ['bluesky'])
        self.assertEqual(
            [row[0] for row in self.rows('SELECT did FROM content.bluesky_follows')],
            ['did:new'],
        )

    def test_mastodon_scopes_are_replaced_independently(self):
        self.queue(mastodon_follows={
            'infosec': [MastodonFollowRecord('1', 'a@infosec')],
            'hachyderm': [MastodonFollowRecord('2', 'b@hachyderm')],
        }, follows_observed_at='2026-01-01T00:00:00Z')
        self.publish_next()
        self.assertEqual(self.count('content.mastodon_follows'), 2)

        self.queue(mastodon_follows={'infosec': []},
                   follows_observed_at='2026-01-02T00:00:00Z')
        self.publish_next()

        rows = self.rows('SELECT instance_name FROM content.mastodon_follows')
        self.assertEqual([row[0] for row in rows], ['hachyderm'],
                         'clearing one instance must not clear another')


if __name__ == '__main__':
    unittest.main()

import datetime
import unittest
from datetime import UTC
from pathlib import Path

from pipeline import contract
from pipeline.contract import (
    BlueskyFollowRecord,
    CheckpointRecord,
    ContractError,
    FileEntry,
    Manifest,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
)


class TimestampTests(unittest.TestCase):
    def test_naive_datetime_is_read_as_utc(self):
        moment = datetime.datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(contract.to_timestamp(moment), '2026-01-02T03:04:05Z')

    def test_aware_datetime_is_converted_to_utc(self):
        tokyo = datetime.timezone(datetime.timedelta(hours=9))
        moment = datetime.datetime(2026, 5, 22, 20, 0, 0, tzinfo=tokyo)
        self.assertEqual(contract.to_timestamp(moment), '2026-05-22T11:00:00Z')

    def test_legacy_sqlite_string_is_accepted(self):
        self.assertEqual(contract.to_timestamp('2026-01-02 03:04:05'), '2026-01-02T03:04:05Z')

    def test_sub_second_precision_is_truncated_for_determinism(self):
        moment = datetime.datetime(2026, 1, 2, 3, 4, 5, 987654, tzinfo=UTC)
        self.assertEqual(contract.to_timestamp(moment), '2026-01-02T03:04:05Z')

    def test_rfc3339_input_round_trips(self):
        self.assertEqual(contract.to_timestamp('2026-01-02T03:04:05Z'), '2026-01-02T03:04:05Z')

    def test_unparseable_value_is_rejected(self):
        with self.assertRaises(ContractError):
            contract.to_timestamp('last thursday')

    def test_empty_value_is_rejected(self):
        with self.assertRaises(ContractError):
            contract.to_timestamp('   ')


class SerializationTests(unittest.TestCase):
    def test_key_order_does_not_change_the_bytes(self):
        # The manifest checksums are only meaningful if the same record always
        # serializes identically regardless of how the dict was built.
        first = contract.dumps_line({'b': 1, 'a': 2})
        second = contract.dumps_line({'a': 2, 'b': 1})
        self.assertEqual(first, second)
        self.assertEqual(first, '{"a":2,"b":1}')

    def test_non_ascii_is_written_literally(self):
        self.assertEqual(contract.dumps_line({'a': 'café'}), '{"a":"café"}')

    def test_malformed_line_is_rejected_with_context(self):
        with self.assertRaises(ContractError) as caught:
            contract.loads_line('{not json', context='posts.ndjson line 3')
        self.assertIn('posts.ndjson line 3', str(caught.exception))

    def test_non_object_line_is_rejected(self):
        with self.assertRaises(ContractError):
            contract.loads_line('[1, 2, 3]')


class PostRecordTests(unittest.TestCase):
    def _record(self, **overrides):
        values = {
            'unique_id': 'a' * 64,
            'source': 'https://example.com',
            'source_type': 'rss',
            'posted_at': '2026-01-02 03:04:05',
            'urls': ('https://example.com/a', 'https://example.com/b'),
            'author': 'Someone',
            'description': 'A title',
            'direct_link': '',
        }
        values.update(overrides)
        return PostRecord(**values)

    def test_valid_record_passes_its_schema(self):
        document = self._record().to_dict()
        contract.validate_record(contract.KIND_POSTS, document)
        self.assertEqual(document['posted_at'], '2026-01-02T03:04:05Z')
        self.assertEqual(document['urls'], ['https://example.com/a', 'https://example.com/b'])

    def test_url_order_is_preserved(self):
        document = self._record(urls=('https://example.com/z', 'https://example.com/a')).to_dict()
        self.assertEqual(document['urls'][0], 'https://example.com/z')

    def test_record_carries_no_database_identifiers(self):
        self.assertNotIn('idx', self._record().to_dict())
        self.assertNotIn('post_id', self._record().to_dict())

    def test_post_without_urls_is_rejected(self):
        with self.assertRaises(ContractError):
            contract.validate_record(contract.KIND_POSTS, self._record(urls=()).to_dict())

    def test_non_http_url_is_rejected(self):
        document = self._record(urls=('javascript:alert(1)',)).to_dict()
        with self.assertRaises(ContractError):
            contract.validate_record(contract.KIND_POSTS, document)

    def test_duplicate_urls_are_rejected(self):
        document = self._record(urls=('https://example.com/a', 'https://example.com/a')).to_dict()
        with self.assertRaises(ContractError):
            contract.validate_record(contract.KIND_POSTS, document)

    def test_unknown_field_is_rejected(self):
        document = self._record().to_dict()
        document['sneaky'] = True
        with self.assertRaises(ContractError):
            contract.validate_record(contract.KIND_POSTS, document)

    def test_source_type_must_be_a_lowercase_token(self):
        document = self._record(source_type='RSS Feed').to_dict()
        with self.assertRaises(ContractError):
            contract.validate_record(contract.KIND_POSTS, document)

    def test_a_new_source_type_needs_no_contract_change(self):
        document = self._record(source_type='lemmy').to_dict()
        contract.validate_record(contract.KIND_POSTS, document)

    def test_round_trip_through_a_dict(self):
        original = self._record()
        restored = PostRecord.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())


class RssObservationTests(unittest.TestCase):
    def _record(self, **overrides):
        values = {
            'normalized_url': 'https://example.com/feed',
            'feed_url': 'https://example.com/feed',
            'observed_at': '2026-01-02T03:04:05Z',
            'success': True,
            'status': 200,
            'etag': 'W/"abc"',
            'last_modified': 'Wed, 21 Oct 2026 07:28:00 GMT',
            'latest_entry_at': '2026-01-01 00:00:00',
            'site_link': 'https://example.com',
        }
        values.update(overrides)
        return RssObservationRecord(**values)

    def test_valid_observation_passes(self):
        document = self._record().to_dict()
        contract.validate_record(contract.KIND_RSS_OBSERVATIONS, document)
        self.assertEqual(document['latest_entry_at'], '2026-01-01T00:00:00Z')

    def test_failure_observation_allows_null_status(self):
        document = self._record(
            success=False, status=None, error='connection reset',
            etag=None, last_modified=None, latest_entry_at=None, site_link=None,
        ).to_dict()
        contract.validate_record(contract.KIND_RSS_OBSERVATIONS, document)

    def test_observation_carries_no_failure_counter(self):
        # Counters must be derived by the publisher, otherwise replaying a
        # batch would inflate a feed's failure history.
        self.assertNotIn('consecutive_failures', self._record().to_dict())

    def test_empty_strings_become_nulls(self):
        document = self._record(etag='', site_link='').to_dict()
        self.assertIsNone(document['etag'])
        self.assertIsNone(document['site_link'])

    def test_impossible_status_is_rejected(self):
        with self.assertRaises(ContractError):
            contract.validate_record(
                contract.KIND_RSS_OBSERVATIONS, self._record(status=9000).to_dict()
            )


class CheckpointTests(unittest.TestCase):
    def test_numeric_cursor_is_coerced_to_a_string(self):
        document = CheckpointRecord(
            source_type='mastodon',
            source_key='infosec',
            cursor=1234567890,
            observed_at='2026-01-02T03:04:05Z',
            source_url='https://infosec.exchange',
        ).to_dict()
        self.assertEqual(document['cursor'], '1234567890')
        contract.validate_record(contract.KIND_CHECKPOINTS, document)

    def test_source_url_is_optional(self):
        document = CheckpointRecord(
            source_type='reddit',
            source_key='netsec',
            cursor='t3_abc',
            observed_at='2026-01-02T03:04:05Z',
        ).to_dict()
        self.assertIsNone(document['source_url'])
        contract.validate_record(contract.KIND_CHECKPOINTS, document)

    def test_empty_cursor_is_rejected(self):
        document = CheckpointRecord(
            source_type='reddit', source_key='netsec', cursor='',
            observed_at='2026-01-02T03:04:05Z',
        ).to_dict()
        with self.assertRaises(ContractError):
            contract.validate_record(contract.KIND_CHECKPOINTS, document)


class FollowRecordTests(unittest.TestCase):
    def test_bluesky_follow_passes(self):
        document = BlueskyFollowRecord(did='did:plc:abc', handle='a.bsky.social').to_dict()
        contract.validate_record(contract.KIND_BLUESKY_FOLLOWS, document)
        self.assertIsNone(document['display_name'])

    def test_mastodon_follow_id_is_a_string(self):
        document = MastodonFollowRecord(account_id=42, acct='someone@example.social').to_dict()
        self.assertEqual(document['account_id'], '42')
        contract.validate_record(contract.KIND_MASTODON_FOLLOWS, document)

    def test_mastodon_follow_has_no_instance_field(self):
        # The instance is the file's manifest scope, so a snapshot file cannot
        # accidentally mix accounts from two instances.
        self.assertNotIn(
            'instance_name',
            MastodonFollowRecord(account_id='1', acct='a@b.c').to_dict(),
        )


class NamingTests(unittest.TestCase):
    def test_unscoped_kinds_have_fixed_names(self):
        self.assertEqual(contract.file_name_for(contract.KIND_POSTS), 'posts.ndjson')
        self.assertEqual(
            contract.file_name_for(contract.KIND_BLUESKY_FOLLOWS), 'bluesky-follows.ndjson'
        )

    def test_mastodon_follows_are_named_per_instance(self):
        self.assertEqual(
            contract.file_name_for(contract.KIND_MASTODON_FOLLOWS, 'InfoSec'),
            'mastodon-follows-infosec.ndjson',
        )

    def test_mastodon_follows_require_a_scope(self):
        with self.assertRaises(ContractError):
            contract.file_name_for(contract.KIND_MASTODON_FOLLOWS)

    def test_scope_cannot_escape_the_batch_directory(self):
        for hostile in ('../evil', 'a/b', 'a\\b', '', 'x' * 100):
            with self.subTest(scope=hostile), self.assertRaises(ContractError):
                contract.normalize_scope(hostile)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ContractError):
            contract.file_name_for('not_a_kind')


class BatchIdTests(unittest.TestCase):
    def test_ids_sort_in_creation_order(self):
        earlier = contract.new_batch_id(datetime.datetime(2026, 1, 1, tzinfo=UTC))
        later = contract.new_batch_id(datetime.datetime(2026, 1, 2, tzinfo=UTC))
        self.assertLess(earlier, later)

    def test_sub_second_ids_still_sort_correctly(self):
        base = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        first = contract.new_batch_id(base)
        second = contract.new_batch_id(base.replace(microsecond=1))
        self.assertLess(first, second)

    def test_ids_are_unique_within_the_same_instant(self):
        moment = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        ids = {contract.new_batch_id(moment) for _ in range(200)}
        self.assertEqual(len(ids), 200)

    def test_hostile_ids_are_rejected(self):
        for hostile in ('../../etc', '', 'not-a-batch', '20260101T000000000000Z-ZZZZZZZZ', None):
            with self.subTest(batch_id=hostile), self.assertRaises(ContractError):
                contract.validate_batch_id(hostile)

    def test_generated_ids_validate(self):
        contract.validate_batch_id(contract.new_batch_id())


class ManifestTests(unittest.TestCase):
    def _manifest(self, **overrides):
        values = {
            'batch_id': contract.new_batch_id(datetime.datetime(2026, 1, 1, tzinfo=UTC)),
            'created_at': '2026-01-01T00:00:00Z',
            'collector_version': '1.0.0',
            'collector_commit': 'deadbeef',
            'catalog_revision': 'rev-1',
            'files': (
                FileEntry('posts.ndjson', contract.KIND_POSTS, 2, 'a' * 64),
                FileEntry(
                    'mastodon-follows-infosec.ndjson',
                    contract.KIND_MASTODON_FOLLOWS,
                    3,
                    'b' * 64,
                    scope='infosec',
                    observed_at='2026-01-01T00:00:00Z',
                ),
            ),
        }
        values.update(overrides)
        return Manifest(**values)

    def test_round_trip(self):
        original = self._manifest()
        restored = Manifest.from_dict(original.to_dict())
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertEqual(restored.total_records, 5)

    def test_entry_lookup_by_kind_and_scope(self):
        manifest = self._manifest()
        self.assertIsNotNone(manifest.entry_for(contract.KIND_POSTS))
        self.assertIsNotNone(
            manifest.entry_for(contract.KIND_MASTODON_FOLLOWS, 'infosec')
        )
        self.assertIsNone(manifest.entry_for(contract.KIND_CHECKPOINTS))

    def test_future_contract_version_is_rejected_explicitly(self):
        document = self._manifest().to_dict()
        document['contract_version'] = 2
        with self.assertRaises(ContractError):
            Manifest.from_dict(document)

    def test_duplicate_file_entries_are_rejected(self):
        entry = FileEntry('posts.ndjson', contract.KIND_POSTS, 1, 'c' * 64)
        with self.assertRaises(ContractError):
            Manifest.from_dict(self._manifest(files=(entry, entry)).to_dict())

    def test_file_name_must_match_its_kind(self):
        mislabelled = FileEntry('posts.ndjson', contract.KIND_CHECKPOINTS, 1, 'c' * 64)
        with self.assertRaises(ContractError):
            Manifest.from_dict(self._manifest(files=(mislabelled,)).to_dict())

    def test_mastodon_snapshot_without_a_scope_is_rejected(self):
        unscoped = FileEntry(
            'mastodon-follows-infosec.ndjson', contract.KIND_MASTODON_FOLLOWS, 1, 'c' * 64
        )
        with self.assertRaises(ContractError):
            Manifest.from_dict(self._manifest(files=(unscoped,)).to_dict())

    def test_snapshot_without_an_observation_time_is_rejected(self):
        undated = FileEntry(
            'bluesky-follows.ndjson', contract.KIND_BLUESKY_FOLLOWS, 1, 'c' * 64
        )
        with self.assertRaises(ContractError):
            Manifest.from_dict(self._manifest(files=(undated,)).to_dict())

    def test_bad_checksum_shape_is_rejected(self):
        bad = FileEntry('posts.ndjson', contract.KIND_POSTS, 1, 'nope')
        with self.assertRaises(ContractError):
            Manifest.from_dict(self._manifest(files=(bad,)).to_dict())

    def test_manifest_may_declare_no_files(self):
        Manifest.from_dict(self._manifest(files=()).to_dict())


class DestinationIndependenceTests(unittest.TestCase):
    """The pipeline must stay ignorant of wherever the data ends up.

    This is the constraint the whole split rests on, and it is the easiest one
    to break by accident, so it is checked mechanically rather than by review.
    """

    FORBIDDEN = (
        'sqlite3',
        'psycopg',
        'db_utils',
        'db_setup',
        'DATABASE_URL',
        'CREATE TABLE',
        'ON CONFLICT',
        'SELECT ',
        'INSERT ',
    )

    def test_no_module_knows_about_a_database(self):
        directory = Path(contract.__file__).resolve().parent
        modules = sorted(directory.glob('*.py'))
        self.assertGreater(len(modules), 1)
        for module in modules:
            source = module.read_text(encoding='utf-8')
            for token in self.FORBIDDEN:
                with self.subTest(module=module.name, token=token):
                    self.assertNotIn(token, source)

    def test_schemas_describe_records_not_tables(self):
        for schema in sorted(contract.SCHEMA_DIR.glob('*.json')):
            text = schema.read_text(encoding='utf-8')
            for token in ('CREATE TABLE', 'INTEGER PRIMARY KEY', 'sqlite'):
                with self.subTest(schema=schema.name, token=token):
                    self.assertNotIn(token, text)


if __name__ == '__main__':
    unittest.main()

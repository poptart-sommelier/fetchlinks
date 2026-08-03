import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import contract
from pipeline.contract import CheckpointRecord, ContractError
from pipeline.layout import RUNTIME_DIR_ENV, RuntimeLayout
from pipeline.state import CollectorState, StateError


def setUpModule():
    # These tests deliberately trigger the error paths that log; keep the
    # expected noise out of the test output.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class _StateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / 'state' / 'collector-state.v1.json'

    def tearDown(self):
        self._tmp.cleanup()


class PersistenceTests(_StateCase):
    def test_missing_state_starts_empty(self):
        state = CollectorState.load(self.path)
        self.assertEqual(state.rss_cache, {})
        self.assertEqual(state.checkpoints, {})

    def test_round_trip(self):
        state = CollectorState()
        state.set_rss_headers('https://example.com/feed', 'W/"abc"', 'Wed, 21 Oct 2026 07:28:00 GMT')
        state.set_checkpoint('reddit', 'netsec', 't3_abc')
        state.set_checkpoint(
            'mastodon', 'infosec', '12345', source_url='https://infosec.exchange'
        )
        state.save(self.path)

        restored = CollectorState.load(self.path)

        self.assertEqual(
            restored.rss_headers('https://example.com/feed'),
            ('W/"abc"', 'Wed, 21 Oct 2026 07:28:00 GMT'),
        )
        self.assertEqual(restored.checkpoint('reddit', 'netsec'), 't3_abc')
        self.assertEqual(
            restored.checkpoint_entry('mastodon', 'infosec')['source_url'],
            'https://infosec.exchange',
        )

    def test_saved_state_matches_its_schema(self):
        state = CollectorState()
        state.set_checkpoint('bluesky', 'timeline', 'cursor-1')
        state.save(self.path)
        document = json.loads(self.path.read_text('utf-8'))
        contract.validate_against(contract.COLLECTOR_STATE_SCHEMA_FILE, document)

    def test_corrupt_state_is_never_silently_discarded(self):
        # Starting over would re-fetch every source from scratch, so a broken
        # file must stop the run rather than quietly reset it.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{"state_version": 1,', encoding='utf-8')

        with self.assertRaises(StateError):
            CollectorState.load(self.path)

    def test_schema_violating_state_is_rejected(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({'state_version': 1, 'updated_at': 'yesterday',
                        'rss_cache': {}, 'checkpoints': {}}),
            encoding='utf-8',
        )
        with self.assertRaises(StateError):
            CollectorState.load(self.path)

    def test_future_state_version_is_rejected(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({'state_version': 2, 'updated_at': '2026-01-01T00:00:00Z',
                        'rss_cache': {}, 'checkpoints': {}}),
            encoding='utf-8',
        )
        with self.assertRaises(StateError):
            CollectorState.load(self.path)

    def test_saving_leaves_no_temporary_files(self):
        CollectorState().save(self.path)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])

    def test_a_failed_save_leaves_the_previous_state_intact(self):
        state = CollectorState()
        state.set_checkpoint('reddit', 'netsec', 'original')
        state.save(self.path)

        broken = CollectorState()
        broken.set_checkpoint('reddit', 'netsec', 'replacement')
        broken.checkpoints['reddit']['netsec']['cursor'] = object()
        with self.assertRaises(TypeError):
            broken.save(self.path)

        self.assertEqual(CollectorState.load(self.path).checkpoint('reddit', 'netsec'), 'original')
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])

    def test_an_interrupted_write_leaves_no_debris(self):
        # The rename is the commit point. If it never happens, the old file
        # must survive untouched and the temp file must not linger.
        state = CollectorState()
        state.set_checkpoint('reddit', 'netsec', 'original')
        state.save(self.path)

        state.set_checkpoint('reddit', 'netsec', 'replacement')
        with patch('pipeline.atomic.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                state.save(self.path)

        self.assertEqual(CollectorState.load(self.path).checkpoint('reddit', 'netsec'), 'original')
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])


class RssCacheTests(_StateCase):
    def test_unknown_feed_has_no_headers(self):
        self.assertEqual(CollectorState().rss_headers('https://example.com/feed'), (None, None))

    def test_clearing_both_headers_drops_the_entry(self):
        state = CollectorState()
        state.set_rss_headers('https://example.com/feed', 'W/"abc"', None)
        state.set_rss_headers('https://example.com/feed', None, None)
        self.assertEqual(state.rss_cache, {})

    def test_empty_strings_are_stored_as_nulls(self):
        state = CollectorState()
        state.set_rss_headers('https://example.com/feed', '', 'Wed, 21 Oct 2026 07:28:00 GMT')
        self.assertEqual(
            state.rss_headers('https://example.com/feed'),
            (None, 'Wed, 21 Oct 2026 07:28:00 GMT'),
        )

    def test_a_feed_url_is_required(self):
        with self.assertRaises(StateError):
            CollectorState().set_rss_headers('', 'W/"abc"', None)

    def test_removed_feeds_are_forgotten(self):
        state = CollectorState()
        state.set_rss_headers('https://a.example/feed', 'W/"a"', None)
        state.set_rss_headers('https://b.example/feed', 'W/"b"', None)

        dropped = state.retain_feeds(['https://a.example/feed'])

        self.assertEqual(dropped, 1)
        self.assertEqual(list(state.rss_cache), ['https://a.example/feed'])


class CheckpointTests(_StateCase):
    def test_unread_source_has_no_checkpoint(self):
        self.assertIsNone(CollectorState().checkpoint('reddit', 'netsec'))

    def test_an_empty_cursor_is_refused(self):
        # Storing '' would look like "never read" and silently replay the
        # entire history of the source on the next run.
        state = CollectorState()
        for empty in ('', None):
            with self.subTest(cursor=empty), self.assertRaises(StateError):
                state.set_checkpoint('reddit', 'netsec', empty)

    def test_source_type_and_key_are_both_required(self):
        state = CollectorState()
        with self.assertRaises(StateError):
            state.set_checkpoint('', 'netsec', 'abc')
        with self.assertRaises(StateError):
            state.set_checkpoint('reddit', '', 'abc')

    def test_keys_containing_separators_cannot_collide(self):
        state = CollectorState()
        state.set_checkpoint('mastodon', 'https://a.example', 'one')
        state.set_checkpoint('mastodon', 'https://b.example', 'two')
        self.assertEqual(state.checkpoint('mastodon', 'https://a.example'), 'one')
        self.assertEqual(state.checkpoint('mastodon', 'https://b.example'), 'two')

    def test_the_same_key_under_two_sources_stays_separate(self):
        state = CollectorState()
        state.set_checkpoint('reddit', 'timeline', 'r')
        state.set_checkpoint('bluesky', 'timeline', 'b')
        self.assertEqual(state.checkpoint('reddit', 'timeline'), 'r')
        self.assertEqual(state.checkpoint('bluesky', 'timeline'), 'b')

    def test_cursors_are_stored_as_strings(self):
        state = CollectorState()
        state.set_checkpoint('mastodon', 'infosec', 1234567890)
        self.assertEqual(state.checkpoint('mastodon', 'infosec'), '1234567890')

    def test_state_advances_from_the_batch_checkpoint_records(self):
        state = CollectorState()
        applied = state.apply_checkpoints([
            CheckpointRecord('reddit', 'netsec', 't3_abc', '2026-01-02T03:04:05Z'),
            CheckpointRecord('bluesky', 'timeline', 'cur-1', '2026-01-02T03:04:05Z'),
            CheckpointRecord(
                'mastodon', 'infosec', '99', '2026-01-02T03:04:05Z',
                source_url='https://infosec.exchange',
            ),
        ])

        self.assertEqual(applied, 3)
        self.assertEqual(state.checkpoint('reddit', 'netsec'), 't3_abc')
        self.assertEqual(
            state.checkpoint_entry('reddit', 'netsec')['observed_at'], '2026-01-02T03:04:05Z'
        )
        self.assertEqual(
            state.checkpoint_entry('mastodon', 'infosec')['source_url'],
            'https://infosec.exchange',
        )

    def test_removed_streams_are_forgotten(self):
        state = CollectorState()
        state.set_checkpoint('reddit', 'netsec', 'a')
        state.set_checkpoint('reddit', 'sysadmin', 'b')

        dropped = state.retain_streams('reddit', ['netsec'])

        self.assertEqual(dropped, 1)
        self.assertEqual(list(state.checkpoints['reddit']), ['netsec'])

    def test_forgetting_every_stream_removes_the_source(self):
        state = CollectorState()
        state.set_checkpoint('reddit', 'netsec', 'a')
        state.retain_streams('reddit', [])
        self.assertNotIn('reddit', state.checkpoints)

    def test_retaining_an_unknown_source_is_a_no_op(self):
        self.assertEqual(CollectorState().retain_streams('reddit', ['netsec']), 0)


class RuntimeLayoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / 'runtime'

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop(RUNTIME_DIR_ENV, None)

    def test_paths_follow_the_documented_layout(self):
        layout = RuntimeLayout(self.root)
        self.assertEqual(layout.catalog_path.name, 'catalog.v1.json')
        self.assertEqual(layout.catalog_path.parent, layout.root / 'catalog')
        self.assertEqual(layout.state_path.parent, layout.root / 'state')
        self.assertEqual(layout.outbox_dir, layout.root / 'outbox')

    def test_initialize_creates_everything(self):
        layout = RuntimeLayout(self.root).initialize()
        self.assertTrue(layout.catalog_dir.is_dir())
        self.assertTrue(layout.state_dir.is_dir())
        self.assertTrue((layout.outbox_dir / 'ready').is_dir())

    def test_state_round_trips_through_the_layout(self):
        layout = RuntimeLayout(self.root).initialize()
        state = layout.load_state()
        state.set_checkpoint('bluesky', 'timeline', 'cur-1')
        layout.save_state(state)
        self.assertEqual(layout.load_state().checkpoint('bluesky', 'timeline'), 'cur-1')

    def test_an_explicit_root_wins_over_the_environment(self):
        os.environ[RUNTIME_DIR_ENV] = str(self.root / 'from-env')
        self.assertEqual(
            RuntimeLayout.resolve(self.root / 'explicit').root,
            (self.root / 'explicit').resolve(),
        )

    def test_the_environment_overrides_the_default(self):
        os.environ[RUNTIME_DIR_ENV] = str(self.root / 'from-env')
        self.assertEqual(RuntimeLayout.resolve().root, (self.root / 'from-env').resolve())


if __name__ == '__main__':
    unittest.main()

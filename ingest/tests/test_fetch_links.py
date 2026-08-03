import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import fetch_links
import ingest_limits
from config import (
    AppConfig,
    BlueskySource,
    IngestPolicy,
    MastodonInstance,
    MastodonSource,
    PathsConfig,
    RedditSource,
    RssSource,
    Sources,
)
from pipeline.catalog import Catalog, CatalogError, build_catalog
from pipeline.collection import CollectionResult
from pipeline.contract import CheckpointRecord, PostRecord, RssObservationRecord
from pipeline.layout import RuntimeLayout
from pipeline.state import CollectorState


def _paths(tmp: Path, runtime_dir: Path | None = None) -> PathsConfig:
    return PathsConfig(log_file=tmp / 'fetchlinks.log',
                       log_level='INFO', runtime_dir=runtime_dir)


def _cfg(
    tmp: Path,
    *,
    ingest: IngestPolicy | None = None,
    rss: RssSource | None = None,
    reddit: RedditSource | None = None,
    bluesky: BlueskySource | None = None,
    mastodon: MastodonSource | None = None,
    runtime_dir: Path | None = None,
) -> AppConfig:
    return AppConfig(
        paths=_paths(tmp, runtime_dir),
        ingest=ingest or IngestPolicy(),
        sources=Sources(rss=rss, reddit=reddit, bluesky=bluesky, mastodon=mastodon),
        source_path=tmp / 'fetchlinks.toml',
    )


def _empty(*_args, **_kwargs) -> CollectionResult:
    return CollectionResult()


def _post(unique_id: str = 'a') -> PostRecord:
    return PostRecord(unique_id=unique_id, source='s', source_type='rss',
                      posted_at='2026-01-01T00:00:00Z',
                      urls=(f'https://example.com/{unique_id}',))


class CollectRoutingTests(unittest.TestCase):
    def test_runs_default_rss_reddit_and_enabled_bluesky_and_mastodon(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=True)
        reddit = RedditSource(enabled=True, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        bluesky = BlueskySource(enabled=True, credential_location=tmp / 'bsky.json')
        mastodon = MastodonSource(enabled=True, instances=(
            MastodonInstance(name='infosec', instance_url='https://infosec.exchange',
                             credential_location=tmp / 'mastodon.json'),
        ))
        cfg = _cfg(tmp, rss=rss, reddit=reddit, bluesky=bluesky, mastodon=mastodon)
        default_age = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS
        catalog = build_catalog()
        state = CollectorState()

        with patch.object(fetch_links.rss_links, 'run', side_effect=_empty) as rss_run, \
             patch.object(fetch_links.reddit_links, 'run', side_effect=_empty) as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run', side_effect=_empty) as bluesky_run, \
             patch.object(fetch_links.bluesky_links, 'sync_follows', side_effect=_empty) as bluesky_sync, \
             patch.object(fetch_links.mastodon_links, 'run', side_effect=_empty) as mastodon_run, \
             patch.object(fetch_links.mastodon_links, 'sync_follows', side_effect=_empty) as mastodon_sync:
            fetch_links.collect(cfg, catalog, state)

        rss_run.assert_called_once_with(rss, catalog, state, default_age, [], [])
        reddit_run.assert_called_once_with(reddit, catalog, state, default_age, [], [])
        bluesky_run.assert_called_once_with(bluesky, state, default_age, [], [])
        mastodon_run.assert_called_once_with(mastodon, state, default_age, [], [])
        bluesky_sync.assert_called_once_with(bluesky)
        mastodon_sync.assert_called_once_with(mastodon)

    def test_no_source_receives_a_database_path(self):
        """The collector must be able to run with no database at all."""
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=True)
        cfg = _cfg(tmp, rss=rss)

        with patch.object(fetch_links.rss_links, 'run', side_effect=_empty) as rss_run:
            fetch_links.collect(cfg, build_catalog(), CollectorState())

        passed = list(rss_run.call_args.args) + list(rss_run.call_args.kwargs.values())
        # PathsConfig no longer carries a database location at all, so the
        # check is that nothing database-shaped reaches a source module.
        for value in passed:
            text = str(value).lower()
            self.assertNotIn('.db', text)
            self.assertNotIn('postgres', text)

    def test_passes_configured_ingest_age_limit_to_sources(self):
        tmp = Path('/tmp/fl-test')
        reddit = RedditSource(enabled=True, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        cfg = _cfg(tmp, ingest=IngestPolicy(max_post_age_months=6), reddit=reddit)
        catalog = build_catalog()
        state = CollectorState()

        with patch.object(fetch_links.reddit_links, 'run', side_effect=_empty) as reddit_run:
            fetch_links.collect(cfg, catalog, state)

        reddit_run.assert_called_once_with(reddit, catalog, state, 6, [], [])

    def test_passes_keyword_filters_to_sources(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=True)
        cfg = _cfg(tmp, ingest=IngestPolicy(
            excluded_url_host_keywords=('insider',),
            excluded_url_or_description_keywords=('politics',),
        ), rss=rss)
        catalog = build_catalog()
        state = CollectorState()

        with patch.object(fetch_links.rss_links, 'run', side_effect=_empty) as rss_run:
            fetch_links.collect(cfg, catalog, state)

        rss_run.assert_called_once_with(
            rss,
            catalog,
            state,
            ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
            ['insider'],
            ['politics'],
        )

    def test_merges_results_from_every_source(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=True)
        reddit = RedditSource(enabled=True, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        cfg = _cfg(tmp, rss=rss, reddit=reddit)

        rss_result = CollectionResult()
        rss_result.add_posts([_post('a')])
        reddit_result = CollectionResult()
        reddit_result.add_posts([_post('b')])
        reddit_result.add_checkpoints([CheckpointRecord(
            source_type='reddit', source_key='netsec', cursor='t3_1',
            observed_at='2026-01-01T00:00:00Z')])

        with patch.object(fetch_links.rss_links, 'run', return_value=rss_result), \
             patch.object(fetch_links.reddit_links, 'run', return_value=reddit_result):
            merged = fetch_links.collect(cfg, build_catalog(), CollectorState())

        self.assertEqual([post.unique_id for post in merged.posts], ['a', 'b'])
        self.assertEqual(len(merged.checkpoints), 1)

    def test_skips_disabled_sources(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=False)
        reddit = RedditSource(enabled=False, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        bluesky = BlueskySource(enabled=False, credential_location=tmp / 'bsky.json')
        mastodon = MastodonSource(enabled=False, instances=())
        cfg = _cfg(tmp, rss=rss, reddit=reddit, bluesky=bluesky, mastodon=mastodon)

        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.collect(cfg, build_catalog(), CollectorState())

        rss_run.assert_not_called()
        reddit_run.assert_not_called()
        bluesky_run.assert_not_called()
        mastodon_run.assert_not_called()

    def test_missing_source_sections_are_skipped(self):
        tmp = Path('/tmp/fl-test')
        cfg = _cfg(tmp)

        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.collect(cfg, build_catalog(), CollectorState())

        rss_run.assert_not_called()
        reddit_run.assert_not_called()
        bluesky_run.assert_not_called()
        mastodon_run.assert_not_called()


class AdvanceStateTests(unittest.TestCase):
    def test_successful_observation_updates_cache_headers(self):
        state = CollectorState()
        result = CollectionResult()
        result.add_rss_observations([RssObservationRecord(
            normalized_url='https://example.com/feed', feed_url='https://example.com/feed',
            observed_at='2026-01-01T00:00:00Z', success=True, status=200,
            etag='"abc"', last_modified='Thu, 01 Jan 2026 00:00:00 GMT')])

        catalog = build_catalog([('https://example.com/feed', 'https://example.com/feed')])
        fetch_links.advance_state(state, catalog, result)

        self.assertEqual(state.rss_headers('https://example.com/feed'),
                         ('"abc"', 'Thu, 01 Jan 2026 00:00:00 GMT'))

    def test_failed_observation_leaves_cached_headers_alone(self):
        state = CollectorState()
        state.set_rss_headers('https://example.com/feed', '"old"', None)
        result = CollectionResult()
        result.add_rss_observations([RssObservationRecord(
            normalized_url='https://example.com/feed', feed_url='https://example.com/feed',
            observed_at='2026-01-01T00:00:00Z', success=False, status=500,
            error='server error')])

        catalog = build_catalog([('https://example.com/feed', 'https://example.com/feed')])
        fetch_links.advance_state(state, catalog, result)

        self.assertEqual(state.rss_headers('https://example.com/feed'), ('"old"', None))

    def test_checkpoints_are_applied(self):
        state = CollectorState()
        result = CollectionResult()
        result.add_checkpoints([CheckpointRecord(
            source_type='reddit', source_key='netsec', cursor='t3_9',
            observed_at='2026-01-01T00:00:00Z')])

        catalog = build_catalog(subreddit_pairs=[('netsec', 'netsec')])
        fetch_links.advance_state(state, catalog, result)

        self.assertEqual(state.checkpoint('reddit', 'netsec'), 't3_9')

    def test_state_for_sources_removed_from_the_catalog_is_dropped(self):
        state = CollectorState()
        state.set_rss_headers('https://gone.example/feed', '"x"', None)
        state.set_checkpoint('reddit', 'gone', 't3_1')

        fetch_links.advance_state(state, build_catalog(), CollectionResult())

        self.assertEqual(state.rss_cache, {})
        self.assertIsNone(state.checkpoint('reddit', 'gone'))

    def test_checkpoints_for_other_sources_survive_a_catalog_prune(self):
        """Bluesky and Mastodon streams are configured, not catalogued."""
        state = CollectorState()
        state.set_checkpoint('bluesky', 'timeline', 'cursor-1')
        state.set_checkpoint('mastodon', 'infosec', '99')

        fetch_links.advance_state(state, build_catalog(), CollectionResult())

        self.assertEqual(state.checkpoint('bluesky', 'timeline'), 'cursor-1')
        self.assertEqual(state.checkpoint('mastodon', 'infosec'), '99')


class CollectOnceTests(unittest.TestCase):
    def _prepare(self, tmp: Path) -> AppConfig:
        layout = RuntimeLayout(tmp / 'runtime')
        layout.initialize()
        build_catalog(
            [('https://example.com/feed', 'https://example.com/feed')],
            [('netsec', 'netsec')],
            source='test',
        ).save(layout.catalog_path)
        return _cfg(tmp, runtime_dir=layout.root)

    def test_queues_a_batch_and_advances_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cfg = self._prepare(tmp)

            result = CollectionResult()
            result.add_posts([_post('a')])
            result.add_checkpoints([CheckpointRecord(
                source_type='reddit', source_key='netsec', cursor='t3_9',
                observed_at='2026-01-01T00:00:00Z')])

            with patch.object(fetch_links, 'collect', return_value=result):
                batch_id = fetch_links.collect_once(cfg)

            self.assertIsNotNone(batch_id)
            layout = RuntimeLayout(cfg.paths.runtime_dir)
            self.assertIn(batch_id, layout.spool().batch_ids('ready'))
            self.assertEqual(layout.load_state().checkpoint('reddit', 'netsec'), 't3_9')

    def test_batch_records_the_catalog_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cfg = self._prepare(tmp)
            layout = RuntimeLayout(cfg.paths.runtime_dir)
            revision = Catalog.load(layout.catalog_path).revision

            result = CollectionResult()
            result.add_posts([_post('a')])

            with patch.object(fetch_links, 'collect', return_value=result):
                batch_id = fetch_links.collect_once(cfg)

            claimed = layout.spool().claim_next()
            self.assertEqual(claimed.batch_id, batch_id)
            self.assertEqual(claimed.manifest.catalog_revision, revision)

    def test_nothing_collected_queues_no_batch_and_leaves_state_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cfg = self._prepare(tmp)
            layout = RuntimeLayout(cfg.paths.runtime_dir)

            with patch.object(fetch_links, 'collect', return_value=CollectionResult()):
                batch_id = fetch_links.collect_once(cfg)

            self.assertIsNone(batch_id)
            self.assertEqual(layout.spool().batch_ids('ready'), [])
            self.assertFalse(layout.state_path.exists())

    def test_missing_catalog_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cfg = _cfg(tmp, runtime_dir=tmp / 'runtime')
            with self.assertRaises(CatalogError) as exc:
                fetch_links.collect_once(cfg)
            self.assertIn('catalog sync', str(exc.exception))

    def test_a_failing_source_queues_nothing_and_advances_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cfg = self._prepare(tmp)
            layout = RuntimeLayout(cfg.paths.runtime_dir)

            with patch.object(fetch_links, 'collect', side_effect=RuntimeError('boom')):
                with self.assertRaises(RuntimeError):
                    fetch_links.collect_once(cfg)

            self.assertEqual(layout.spool().batch_ids('ready'), [])
            self.assertEqual(layout.spool().batch_ids('staging'), [])
            self.assertFalse(layout.state_path.exists())


class MainFlowTests(unittest.TestCase):
    def test_main_runs_startup_steps_in_order(self):
        events = []
        tmp = Path('/tmp/fl-test')
        cfg = _cfg(tmp)

        def record(name, value=None):
            events.append(name)
            return value

        class _Args:
            config = tmp / 'fetchlinks.toml'

        with patch.object(fetch_links.app_config, 'parse_arguments', side_effect=lambda: record('parse_arguments', _Args())), \
             patch.object(fetch_links.app_config, 'load_config', side_effect=lambda path: record('load_config', cfg)), \
             patch.object(fetch_links, 'configure_logging', side_effect=lambda cfg: record('configure_logging')), \
             patch.object(fetch_links, 'collect_once', side_effect=lambda cfg: record('collect_once')):
            fetch_links.main()

        self.assertEqual(events, [
            'parse_arguments',
            'load_config',
            'configure_logging',
            'collect_once',
        ])

    def test_main_exits_one_on_exception(self):
        class _Args:
            config = Path('/tmp/bad.toml')

        with patch.object(fetch_links.app_config, 'parse_arguments', return_value=_Args()), \
             patch.object(fetch_links.app_config, 'load_config', side_effect=ValueError('bad config')), \
             patch.object(fetch_links.logging, 'exception') as log_exception:
            with self.assertRaises(SystemExit) as exc:
                fetch_links.main()

        self.assertEqual(exc.exception.code, 1)
        log_exception.assert_called_once()


class ConfigureLoggingTests(unittest.TestCase):
    def test_unknown_level_falls_back_to_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(Path(tmp))
            cfg = replace(cfg, paths=replace(cfg.paths, log_level='NOPE'))
            with patch.object(fetch_links, 'RotatingFileHandler') as handler, \
                 patch.object(fetch_links.logging, 'basicConfig') as basic_config:
                fetch_links.configure_logging(cfg)

            handler.assert_called_once()
            self.assertEqual(basic_config.call_args.kwargs['level'], fetch_links.logging.INFO)


if __name__ == '__main__':
    unittest.main()

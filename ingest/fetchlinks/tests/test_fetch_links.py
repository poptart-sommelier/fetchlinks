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


def _paths(tmp: Path) -> PathsConfig:
    return PathsConfig(db=tmp / 'fetchlinks.db', log_file=tmp / 'fetchlinks.log', log_level='INFO')


def _cfg(
    tmp: Path,
    *,
    ingest: IngestPolicy | None = None,
    rss: RssSource | None = None,
    reddit: RedditSource | None = None,
    bluesky: BlueskySource | None = None,
    mastodon: MastodonSource | None = None,
) -> AppConfig:
    return AppConfig(
        paths=_paths(tmp),
        ingest=ingest or IngestPolicy(),
        sources=Sources(rss=rss, reddit=reddit, bluesky=bluesky, mastodon=mastodon),
        source_path=tmp / 'fetchlinks.toml',
    )


class FetchLinksRoutingTests(unittest.TestCase):
    def test_runs_default_rss_reddit_and_enabled_bluesky_and_mastodon(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=True, feeds_file=tmp / 'rss_feeds.txt',
                        feeds=('https://feed.example/rss.xml',))
        reddit = RedditSource(enabled=True, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        bluesky = BlueskySource(enabled=True, credential_location=tmp / 'bsky.json')
        mastodon = MastodonSource(enabled=True, instances=(
            MastodonInstance(name='infosec', instance_url='https://infosec.exchange',
                             credential_location=tmp / 'mastodon.json'),
        ))
        cfg = _cfg(tmp, rss=rss, reddit=reddit, bluesky=bluesky, mastodon=mastodon)
        default_age = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS
        db_path = cfg.paths.db

        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.fetch_links(cfg)

        rss_run.assert_called_once_with(['https://feed.example/rss.xml'], db_path, default_age, [], [])
        reddit_run.assert_called_once_with(reddit, db_path, default_age, [], [])
        bluesky_run.assert_called_once_with(bluesky, db_path, default_age, [], [])
        mastodon_run.assert_called_once_with(mastodon, db_path, default_age, [], [])

    def test_passes_configured_ingest_age_limit_to_sources(self):
        tmp = Path('/tmp/fl-test')
        reddit = RedditSource(enabled=True, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        cfg = _cfg(tmp, ingest=IngestPolicy(max_post_age_months=6), reddit=reddit)

        with patch.object(fetch_links.reddit_links, 'run') as reddit_run:
            fetch_links.fetch_links(cfg)

        reddit_run.assert_called_once_with(reddit, cfg.paths.db, 6, [], [])

    def test_passes_keyword_filters_to_sources(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=True, feeds_file=tmp / 'feeds.txt',
                        feeds=('https://feed.example/rss.xml',))
        cfg = _cfg(tmp, ingest=IngestPolicy(
            excluded_url_host_keywords=('insider',),
            excluded_url_or_description_keywords=('politics',),
        ), rss=rss)

        with patch.object(fetch_links.rss_links, 'run') as rss_run:
            fetch_links.fetch_links(cfg)

        rss_run.assert_called_once_with(
            ['https://feed.example/rss.xml'],
            cfg.paths.db,
            ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
            ['insider'],
            ['politics'],
        )

    def test_skips_disabled_sources(self):
        tmp = Path('/tmp/fl-test')
        rss = RssSource(enabled=False, feeds_file=tmp / 'feeds.txt',
                        feeds=('https://feed.example/rss.xml',))
        reddit = RedditSource(enabled=False, credential_location=tmp / 'reddit.json',
                              subreddits=('netsec',))
        bluesky = BlueskySource(enabled=False, credential_location=tmp / 'bsky.json')
        mastodon = MastodonSource(enabled=False, instances=())
        cfg = _cfg(tmp, rss=rss, reddit=reddit, bluesky=bluesky, mastodon=mastodon)

        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.fetch_links(cfg)

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
            fetch_links.fetch_links(cfg)

        rss_run.assert_not_called()
        reddit_run.assert_not_called()
        bluesky_run.assert_not_called()
        mastodon_run.assert_not_called()


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
             patch.object(fetch_links.db_setup, 'db_initial_setup', side_effect=lambda path: record('db_initial_setup')), \
             patch.object(fetch_links, 'fetch_links', side_effect=lambda cfg: record('fetch_links')):
            fetch_links.main()

        self.assertEqual(events, [
            'parse_arguments',
            'load_config',
            'configure_logging',
            'db_initial_setup',
            'fetch_links',
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
        import tempfile
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

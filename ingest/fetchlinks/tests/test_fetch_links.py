import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fetch_links


class FetchLinksRoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = {'db_info': {'db_location': '/tmp', 'db_name': 'fetchlinks.db'}}

    def test_runs_default_rss_reddit_and_enabled_bluesky_and_mastodon(self):
        sources = {
            'rss': {'feeds': ['https://feed.example/rss.xml']},
            'reddit': {'subreddits': ['netsec']},
            'bluesky': {'enabled': True, 'credential_location': '/tmp/bsky.json'},
            'mastodon': {'enabled': True, 'instances': [{'name': 'infosec'}]},
        }
        default_age = fetch_links.ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS

        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.fetch_links(self.config, sources)

        rss_run.assert_called_once_with(['https://feed.example/rss.xml'], self.config['db_info'], default_age, [], [])
        reddit_run.assert_called_once_with(sources['reddit'], self.config['db_info'], default_age, [], [])
        bluesky_run.assert_called_once_with(sources['bluesky'], self.config['db_info'], default_age, [], [])
        mastodon_run.assert_called_once_with(sources['mastodon'], self.config['db_info'], default_age, [], [])

    def test_passes_configured_ingest_age_limit_to_sources(self):
        sources = {
            'ingest': {'max_post_age_months': 6},
            'reddit': {'subreddits': ['netsec']},
        }

        with patch.object(fetch_links.reddit_links, 'run') as reddit_run:
            fetch_links.fetch_links(self.config, sources)

        reddit_run.assert_called_once_with(sources['reddit'], self.config['db_info'], 6, [], [])

    def test_passes_excluded_url_host_keywords_to_sources(self):
        sources = {
            'ingest': {'excluded_url_host_keywords': ['insider', 'BusinessInsider.com']},
            'rss': {'feeds': ['https://feed.example/rss.xml']},
        }

        with patch.object(fetch_links.rss_links, 'run') as rss_run:
            fetch_links.fetch_links(self.config, sources)

        rss_run.assert_called_once_with(
            ['https://feed.example/rss.xml'],
            self.config['db_info'],
            fetch_links.ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
            ['insider', 'businessinsider.com'],
            [],
        )

    def test_passes_excluded_url_or_description_keywords_to_sources(self):
        sources = {
            'ingest': {'excluded_url_or_description_keywords': ['Politics', ' trump ']},
            'rss': {'feeds': ['https://feed.example/rss.xml']},
        }

        with patch.object(fetch_links.rss_links, 'run') as rss_run:
            fetch_links.fetch_links(self.config, sources)

        rss_run.assert_called_once_with(
            ['https://feed.example/rss.xml'],
            self.config['db_info'],
            fetch_links.ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
            [],
            ['politics', 'trump'],
        )

    def test_skips_disabled_sources(self):
        sources = {
            'rss': {'enabled': False, 'feeds': ['https://feed.example/rss.xml']},
            'reddit': {'enabled': False, 'subreddits': ['netsec']},
            'bluesky': {'enabled': False, 'credential_location': '/tmp/bsky.json'},
            'mastodon': {'enabled': False, 'instances': [{'name': 'infosec'}]},
        }

        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.fetch_links(self.config, sources)

        rss_run.assert_not_called()
        reddit_run.assert_not_called()
        bluesky_run.assert_not_called()
        mastodon_run.assert_not_called()

    def test_missing_source_sections_are_skipped(self):
        with patch.object(fetch_links.rss_links, 'run') as rss_run, \
             patch.object(fetch_links.reddit_links, 'run') as reddit_run, \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run, \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.fetch_links(self.config, {})

        rss_run.assert_not_called()
        reddit_run.assert_not_called()
        bluesky_run.assert_not_called()
        mastodon_run.assert_not_called()

    def test_mastodon_is_disabled_by_default(self):
        sources = {
            'rss': {'feeds': ['https://feed.example/rss.xml']},
            'reddit': {'subreddits': ['netsec']},
            'mastodon': {'instances': [{'name': 'infosec'}]},
        }

        with patch.object(fetch_links.rss_links, 'run'), \
             patch.object(fetch_links.reddit_links, 'run'), \
             patch.object(fetch_links.mastodon_links, 'run') as mastodon_run:
            fetch_links.fetch_links(self.config, sources)

        mastodon_run.assert_not_called()

    def test_bluesky_is_disabled_by_default(self):
        sources = {
            'rss': {'feeds': ['https://feed.example/rss.xml']},
            'reddit': {'subreddits': ['netsec']},
            'bluesky': {'credential_location': '/tmp/bsky.json'},
        }

        with patch.object(fetch_links.rss_links, 'run'), \
             patch.object(fetch_links.reddit_links, 'run'), \
             patch.object(fetch_links.bluesky_links, 'run') as bluesky_run:
            fetch_links.fetch_links(self.config, sources)

        bluesky_run.assert_not_called()


class MainFlowTests(unittest.TestCase):
    def test_main_runs_startup_steps_in_order(self):
        events = []
        args = SimpleNamespace(config='config.json', sources='sources.json')
        config = {
            'db_info': {'db_location': '/tmp/db', 'db_name': 'fetchlinks.db'},
            'log_info': {'log_location': '/tmp/fetchlinks.log'},
        }
        sources = {'rss': {'feeds': ['https://feed.example/rss.xml']}}

        def record(name, value=None):
            events.append(name)
            return value

        with patch.object(fetch_links.startup_and_validate, 'parse_arguments', side_effect=lambda: record('parse_arguments', args)), \
             patch.object(fetch_links.startup_and_validate, 'parse_config', side_effect=lambda path: record('parse_config', config)), \
             patch.object(fetch_links, 'configure_logging', side_effect=lambda cfg: record('configure_logging')), \
             patch.object(fetch_links.db_setup, 'db_initial_setup', side_effect=lambda loc, name: record('db_initial_setup')), \
             patch.object(fetch_links.startup_and_validate, 'parse_sources', side_effect=lambda path: record('parse_sources', sources)), \
             patch.object(fetch_links, 'fetch_links', side_effect=lambda cfg, src: record('fetch_links')):
            fetch_links.main()

        self.assertEqual(events, [
            'parse_arguments',
            'parse_config',
            'configure_logging',
            'db_initial_setup',
            'parse_sources',
            'fetch_links',
        ])

    def test_main_exits_one_on_exception(self):
        with patch.object(fetch_links.startup_and_validate, 'parse_arguments', return_value=SimpleNamespace(config='bad.json')), \
             patch.object(fetch_links.startup_and_validate, 'parse_config', side_effect=ValueError('bad config')), \
             patch.object(fetch_links.logging, 'exception') as log_exception:
            with self.assertRaises(SystemExit) as exc:
                fetch_links.main()

        self.assertEqual(exc.exception.code, 1)
        log_exception.assert_called_once()

    def test_configure_logging_uses_default_info_for_unknown_level(self):
        config = {'log_info': {'log_location': '/tmp/fetchlinks-test.log', 'log_level': 'NOPE'}}
        with patch.object(fetch_links, 'RotatingFileHandler') as handler, \
             patch.object(fetch_links.logging, 'basicConfig') as basic_config:
            fetch_links.configure_logging(config)

        handler.assert_called_once()
        self.assertEqual(basic_config.call_args.kwargs['level'], fetch_links.logging.INFO)


if __name__ == '__main__':
    unittest.main()
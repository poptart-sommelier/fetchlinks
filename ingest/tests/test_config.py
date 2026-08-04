import tempfile
import unittest
from pathlib import Path

import config as app_config


def _toml(
    cfg_dir: Path,
    *,
    log_file='fetchlinks.log',
    log_level='INFO',
    extra: str = '',
) -> Path:
    cfg = cfg_dir / 'fetchlinks.toml'
    cfg.write_text(
        '[paths]\n'
        f'log_file = "{log_file}"\n'
        f'log_level = "{log_level}"\n'
        + extra,
        encoding='utf-8',
    )
    return cfg


class LoadConfigTests(unittest.TestCase):
    def test_loads_minimal_config_with_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = _toml(tmp_path)

            cfg = app_config.load_config(cfg_path)

            self.assertEqual(cfg.paths.log_file, (tmp_path / 'fetchlinks.log').resolve())
            self.assertEqual(cfg.paths.log_level, 'INFO')
            self.assertEqual(cfg.sources.rss, None)
            self.assertEqual(cfg.sources.reddit, None)
            self.assertEqual(cfg.sources.bluesky, None)
            self.assertEqual(cfg.sources.mastodon, None)

    def test_missing_log_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / 'fetchlinks.toml'
            cfg_path.write_text('[paths]\nlog_level = "INFO"\n', encoding='utf-8')

            with self.assertRaises(ValueError):
                app_config.load_config(cfg_path)

    def test_credentials_can_be_absent_for_the_publisher(self):
        """The Publisher shares this file but holds none of the Collector's secrets."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _toml(
                Path(tmp),
                extra='\n[sources.bluesky]\nenabled = true\n'
                      'credential_location = "does-not-exist.json"\n',
            )

            with self.assertRaises(FileNotFoundError):
                app_config.load_config(cfg_path)

            cfg = app_config.load_config(cfg_path, require_credentials=False)
            self.assertTrue(cfg.sources.bluesky.enabled)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            app_config.load_config(Path('/tmp/does-not-exist.toml'))

    def test_invalid_log_level_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), log_level='LOUD')
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_missing_required_paths_field_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'fetchlinks.toml'
            cfg.write_text('[paths]\ndb = "x.db"\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_invalid_ingest_max_age_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), extra='\n[ingest]\nmax_post_age_months = 0\n')
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_rss_section_minimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), extra='\n[sources.rss]\nenabled = true\n')
            loaded = app_config.load_config(cfg)
            self.assertIsNotNone(loaded.sources.rss)
            self.assertTrue(loaded.sources.rss.enabled)
            self.assertIsNone(loaded.sources.rss.seed_file)
            self.assertIsNone(loaded.sources.rss.export_path)
            self.assertEqual(loaded.sources.rss.auto_disable_after_failures, 10)
            self.assertEqual(loaded.sources.rss.request_timeout_seconds, 10)

    def test_rss_section_with_paths_and_knobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'seed.txt').write_text(
                'https://a.example/feed\nhttps://b.example/feed\n',
                encoding='utf-8',
            )
            cfg_path = _toml(
                tmp_path,
                extra=(
                    '\n[sources.rss]\nenabled = true\n'
                    'seed_file = "seed.txt"\n'
                    'export_path = "rss.export.txt"\n'
                    'auto_disable_after_failures = 5\n'
                    'request_timeout_seconds = 30\n'
                ),
            )
            cfg = app_config.load_config(cfg_path)
            self.assertEqual(cfg.sources.rss.seed_file.name, 'seed.txt')
            self.assertEqual(cfg.sources.rss.export_path.name, 'rss.export.txt')
            self.assertEqual(cfg.sources.rss.auto_disable_after_failures, 5)
            self.assertEqual(cfg.sources.rss.request_timeout_seconds, 30)

    def test_rss_invalid_auto_disable_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(
                Path(tmp),
                extra='\n[sources.rss]\nenabled = true\nauto_disable_after_failures = -1\n',
            )
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_rss_invalid_timeout_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(
                Path(tmp),
                extra='\n[sources.rss]\nenabled = true\nrequest_timeout_seconds = 0\n',
            )
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_mastodon_duplicate_instance_name_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cred = tmp_path / 'mastodon.json'
            cred.write_text('{}', encoding='utf-8')
            cfg = _toml(
                tmp_path,
                extra=(
                    '\n[sources.mastodon]\nenabled = true\n'
                    f'[[sources.mastodon.instances]]\nname = "a"\ninstance_url = "https://a.example"\ncredential_location = "{cred.as_posix()}"\n'
                    f'[[sources.mastodon.instances]]\nname = "a"\ninstance_url = "https://b.example"\ncredential_location = "{cred.as_posix()}"\n'
                ),
            )
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_mastodon_https_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cred = tmp_path / 'mastodon.json'
            cred.write_text('{}', encoding='utf-8')
            cfg = _toml(
                tmp_path,
                extra=(
                    '\n[sources.mastodon]\nenabled = true\n'
                    f'[[sources.mastodon.instances]]\nname = "a"\ninstance_url = "http://a.example"\ncredential_location = "{cred.as_posix()}"\n'
                ),
            )
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_disabled_mastodon_does_not_require_instance_credentials(self):
        # A source that is switched off must not be able to stop the collector
        # starting. Reddit and Bluesky already behaved this way; Mastodon
        # validated its instances regardless of the parent switch, so a host
        # with Mastodon deliberately disabled failed to load config at all.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            missing = tmp_path / 'nowhere' / 'mastodon.json'
            cfg = _toml(
                tmp_path,
                extra=(
                    '\n[sources.mastodon]\nenabled = false\n'
                    '[[sources.mastodon.instances]]\nname = "a"\n'
                    'instance_url = "https://a.example"\n'
                    f'credential_location = "{missing.as_posix()}"\n'
                ),
            )
            loaded = app_config.load_config(cfg)
            self.assertFalse(loaded.sources.mastodon.enabled)
            self.assertFalse(loaded.sources.mastodon.instances[0].enabled)

    def test_credential_file_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(
                Path(tmp),
                extra=(
                    '\n[sources.reddit]\nenabled = true\nsubreddits = ["netsec"]\n'
                    'credential_location = "no-such.json"\n'
                ),
            )
            with self.assertRaises(FileNotFoundError):
                app_config.load_config(cfg)

    def test_reddit_accepts_seed_file_without_inline_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cred = tmp_path / 'reddit.json'
            cred.write_text('{}', encoding='utf-8')
            (tmp_path / 'subreddits.txt').write_text('Netsec\n', encoding='utf-8')
            cfg = _toml(
                tmp_path,
                extra=(
                    '\n[sources.reddit]\nenabled = true\n'
                    'seed_file = "subreddits.txt"\n'
                    f'credential_location = "{cred.as_posix()}"\n'
                ),
            )
            loaded = app_config.load_config(cfg)
            self.assertEqual(loaded.sources.reddit.seed_file.name, 'subreddits.txt')
            self.assertEqual(loaded.sources.reddit.subreddits, ())

    def test_reddit_requires_subreddits_or_seed_file_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cred = tmp_path / 'reddit.json'
            cred.write_text('{}', encoding='utf-8')
            cfg = _toml(
                tmp_path,
                extra=(
                    '\n[sources.reddit]\nenabled = true\n'
                    f'credential_location = "{cred.as_posix()}"\n'
                ),
            )
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)


class RetentionConfigTests(unittest.TestCase):
    def test_defaults_when_section_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = app_config.load_config(_toml(Path(tmp)))
            self.assertTrue(cfg.retention.enabled)
            self.assertIsNone(cfg.retention.max_post_age_months)

    def test_explicit_values_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _toml(
                Path(tmp),
                extra='\n[retention]\nenabled = false\nmax_post_age_months = 6\n',
            )
            cfg = app_config.load_config(cfg_path)
            self.assertFalse(cfg.retention.enabled)
            self.assertEqual(cfg.retention.max_post_age_months, 6)

    def test_invalid_max_age_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), extra='\n[retention]\nmax_post_age_months = 0\n')
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_obsolete_vacuum_setting_is_ignored(self):
        """An older config file must still load after SQLite went away."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), extra='\n[retention]\nvacuum_threshold_pages = -1\n')
            loaded = app_config.load_config(cfg)
            self.assertTrue(loaded.retention.enabled)


if __name__ == '__main__':
    unittest.main()

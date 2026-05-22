import tempfile
import unittest
from pathlib import Path

import config as app_config


def _toml(
    cfg_dir: Path,
    *,
    db='fetchlinks.db',
    log_file='fetchlinks.log',
    log_level='INFO',
    extra: str = '',
) -> Path:
    cfg = cfg_dir / 'fetchlinks.toml'
    cfg.write_text(
        '[paths]\n'
        f'db = "{db}"\n'
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

            self.assertEqual(cfg.paths.db, (tmp_path / 'fetchlinks.db').resolve())
            self.assertEqual(cfg.paths.log_file, (tmp_path / 'fetchlinks.log').resolve())
            self.assertEqual(cfg.paths.log_level, 'INFO')
            self.assertEqual(cfg.sources.rss, None)
            self.assertEqual(cfg.sources.reddit, None)
            self.assertEqual(cfg.sources.bluesky, None)
            self.assertEqual(cfg.sources.mastodon, None)

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


class RetentionConfigTests(unittest.TestCase):
    def test_defaults_when_section_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = app_config.load_config(_toml(Path(tmp)))
            self.assertTrue(cfg.retention.enabled)
            self.assertIsNone(cfg.retention.max_post_age_months)
            self.assertEqual(cfg.retention.vacuum_threshold_pages, 1000)

    def test_explicit_values_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _toml(
                Path(tmp),
                extra='\n[retention]\nenabled = false\nmax_post_age_months = 6\nvacuum_threshold_pages = 250\n',
            )
            cfg = app_config.load_config(cfg_path)
            self.assertFalse(cfg.retention.enabled)
            self.assertEqual(cfg.retention.max_post_age_months, 6)
            self.assertEqual(cfg.retention.vacuum_threshold_pages, 250)

    def test_invalid_max_age_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), extra='\n[retention]\nmax_post_age_months = 0\n')
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)

    def test_negative_threshold_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _toml(Path(tmp), extra='\n[retention]\nvacuum_threshold_pages = -1\n')
            with self.assertRaises(ValueError):
                app_config.load_config(cfg)


if __name__ == '__main__':
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path

import startup_and_validate as sv


def _write(path, data):
    Path(path).write_text(json.dumps(data), encoding='utf-8')


class ParseConfigTests(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            sv.parse_config('/no/such/file.json')

    def test_valid_config_anchors_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / 'config.json'
            _write(cfg_path, {
                'db_info': {'db_name': 'x.db', 'db_location': 'db/'},
                'log_info': {
                    'log_config_location': 'log.conf',
                    'log_location': 'logs/app.log',
                    'log_level': 'INFO',
                },
            })

            config = sv.parse_config(str(cfg_path))

            # Relative paths get anchored to the script dir, not cwd.
            self.assertTrue(Path(config['db_info']['db_location']).is_absolute())
            self.assertTrue(Path(config['log_info']['log_location']).is_absolute())
            # Log directory was created.
            self.assertTrue(Path(config['log_info']['log_location']).parent.is_dir())

    def test_missing_section_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / 'config.json'
            _write(cfg_path, {'db_info': {'db_name': 'x.db', 'db_location': 'db/'}})
            with self.assertRaises(ValueError):
                sv.parse_config(str(cfg_path))

    def test_missing_field_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / 'config.json'
            _write(cfg_path, {
                'db_info': {'db_name': 'x.db'},  # missing db_location
                'log_info': {
                    'log_config_location': 'log.conf',
                    'log_location': 'logs/app.log',
                    'log_level': 'INFO',
                },
            })
            with self.assertRaises(ValueError):
                sv.parse_config(str(cfg_path))

    def test_non_string_field_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / 'config.json'
            _write(cfg_path, {
                'db_info': {'db_name': 123, 'db_location': 'db/'},
                'log_info': {
                    'log_config_location': 'log.conf',
                    'log_location': 'logs/app.log',
                    'log_level': 'INFO',
                },
            })
            with self.assertRaises(ValueError):
                sv.parse_config(str(cfg_path))


class ParseSourcesTests(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            sv.parse_sources('/no/such/file.json')

    def test_non_dict_settings_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {'reddit': 'oops'})
            with self.assertRaises(ValueError):
                sv.parse_sources(str(p))

    def test_disabled_source_skips_credential_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {
                'reddit': {
                    'enabled': False,
                    'credential_location': '/no/such/creds.json',
                }
            })
            # Should not raise even though creds file doesn't exist.
            sv.parse_sources(str(p))

    def test_missing_credential_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {
                'reddit': {'credential_location': str(Path(tmp) / 'missing.json')}
            })
            with self.assertRaises(FileNotFoundError):
                sv.parse_sources(str(p))

    def test_tilde_in_credential_location_is_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Place creds file under the fake HOME so ~ resolves to it.
            home = Path(tmp) / 'home'
            home.mkdir()
            creds = home / 'creds.json'
            creds.write_text('{}', encoding='utf-8')

            sources_path = Path(tmp) / 'sources.json'
            _write(sources_path, {'reddit': {'credential_location': '~/creds.json'}})

            old_home = os.environ.get('HOME')
            os.environ['HOME'] = str(home)
            try:
                sources = sv.parse_sources(str(sources_path))
            finally:
                if old_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = old_home

            # Settings now hold the expanded path.
            self.assertEqual(
                sources['reddit']['credential_location'],
                str(creds),
            )

    def test_empty_rss_feeds_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {'rss': {'feeds': []}})
            with self.assertRaises(ValueError):
                sv.parse_sources(str(p))

    def test_rss_feeds_must_be_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {'rss': {'feeds': 'https://feed.example/rss.xml'}})
            with self.assertRaises(ValueError):
                sv.parse_sources(str(p))

    def test_disabled_rss_allows_empty_feeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {'rss': {'enabled': False, 'feeds': []}})
            sv.parse_sources(str(p))

    def test_bluesky_enabled_without_creds_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'sources.json'
            _write(p, {'bluesky': {'enabled': True}})
            with self.assertRaises(ValueError):
                sv.parse_sources(str(p))

    def test_bluesky_timeline_limit_not_int_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / 'c.json'
            creds.write_text('{}', encoding='utf-8')
            p = Path(tmp) / 'sources.json'
            _write(p, {
                'bluesky': {
                    'enabled': True,
                    'credential_location': str(creds),
                    'timeline_limit': 'lots',
                }
            })
            with self.assertRaises(ValueError):
                sv.parse_sources(str(p))

    def test_bluesky_timeline_limit_zero_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / 'c.json'
            creds.write_text('{}', encoding='utf-8')
            p = Path(tmp) / 'sources.json'
            _write(p, {
                'bluesky': {
                    'enabled': True,
                    'credential_location': str(creds),
                    'timeline_limit': 0,
                }
            })
            with self.assertRaises(ValueError):
                sv.parse_sources(str(p))


class ResolveRelativePathTests(unittest.TestCase):
    def test_absolute_path_returned_as_is(self):
        result = sv._resolve_relative_to_script('/tmp/x')
        self.assertEqual(result, Path('/tmp/x'))

    def test_relative_path_anchored_to_script_dir(self):
        result = sv._resolve_relative_to_script('db/x.db')
        self.assertTrue(result.is_absolute())
        self.assertEqual(result.parent.parent, sv.SCRIPT_DIR)


if __name__ == '__main__':
    unittest.main()

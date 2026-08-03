"""Tests for the catalog inspection and seed-bootstrap CLI."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import catalog_tool
from pipeline.catalog import Catalog, build_catalog
from pipeline.layout import RuntimeLayout


def _config(seed_file=None, subreddits=()):
    return SimpleNamespace(
        sources=SimpleNamespace(
            rss=SimpleNamespace(seed_file=seed_file),
            reddit=SimpleNamespace(seed_file=None, subreddits=tuple(subreddits)),
        ),
        paths=SimpleNamespace(runtime_dir=None),
    )


class CatalogFromSeedsTests(unittest.TestCase):
    def test_builds_from_both_seed_sources(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            seed_file = Path(tmp_dir) / 'feeds.txt'
            seed_file.write_text('https://a.example/feed.xml\n', encoding='utf-8')

            catalog = catalog_tool.catalog_from_seeds(
                _config(seed_file=seed_file, subreddits=('r/NetSec',)),
            )

        self.assertEqual(catalog.normalized_feed_urls, {'https://a.example/feed.xml'})
        self.assertEqual(catalog.normalized_subreddit_names, {'netsec'})
        self.assertEqual(catalog.source, catalog_tool.SEED_SOURCE)

    def test_a_missing_seed_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            catalog = catalog_tool.catalog_from_seeds(
                _config(seed_file=Path(tmp_dir) / 'missing.txt',
                        subreddits=('netsec',)),
            )

        self.assertEqual(catalog.normalized_feed_urls, frozenset())
        self.assertEqual(catalog.normalized_subreddit_names, {'netsec'})

    def test_no_seeds_at_all_produces_an_empty_catalog(self):
        self.assertTrue(catalog_tool.catalog_from_seeds(_config()).is_empty)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime_dir = Path(self._tmp.name) / 'runtime'
        self.seed_file = Path(self._tmp.name) / 'feeds.txt'
        self.seed_file.write_text('https://a.example/feed.xml\n', encoding='utf-8')

    def _run(self, *argv, config=None):
        cfg = config if config is not None else _config(seed_file=self.seed_file,
                                                        subreddits=('netsec',))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(catalog_tool.app_config, 'load_config', return_value=cfg), \
             redirect_stdout(out), redirect_stderr(err):
            code = catalog_tool.main(['--runtime-dir', str(self.runtime_dir), *argv])
        return code, out.getvalue(), err.getvalue()

    @property
    def catalog_path(self):
        return RuntimeLayout.resolve(self.runtime_dir).catalog_path

    def test_build_from_seeds_writes_a_loadable_catalog(self):
        code, out, _err = self._run('build-from-seeds')

        self.assertEqual(code, 0)
        catalog = Catalog.load(self.catalog_path)
        self.assertEqual(catalog.normalized_feed_urls, {'https://a.example/feed.xml'})
        self.assertEqual(catalog.normalized_subreddit_names, {'netsec'})
        self.assertIn(catalog.revision, out)

    def test_build_from_seeds_refuses_to_write_an_empty_catalog(self):
        code, _out, err = self._run('build-from-seeds', config=_config())

        self.assertEqual(code, 1)
        self.assertIn('no feeds or subreddits', err)
        self.assertFalse(self.catalog_path.exists())

    def test_rebuilding_a_seed_catalog_is_allowed(self):
        self._run('build-from-seeds')

        code, _out, _err = self._run('build-from-seeds')

        self.assertEqual(code, 0)

    def test_refuses_to_overwrite_an_exported_catalog(self):
        """A seed list would resurrect feeds the web admin deliberately removed."""
        layout = RuntimeLayout.resolve(self.runtime_dir)
        layout.initialize()
        build_catalog([('https://kept.example/feed.xml',
                        'https://kept.example/feed.xml')],
                      source='postgresql').save(layout.catalog_path)

        code, _out, err = self._run('build-from-seeds')

        self.assertEqual(code, 1)
        self.assertIn("'postgresql'", err)
        self.assertEqual(Catalog.load(self.catalog_path).normalized_feed_urls,
                         {'https://kept.example/feed.xml'})

    def test_force_replaces_an_exported_catalog(self):
        layout = RuntimeLayout.resolve(self.runtime_dir)
        layout.initialize()
        build_catalog([('https://old.example/feed.xml',
                        'https://old.example/feed.xml')],
                      source='postgresql').save(layout.catalog_path)

        code, _out, _err = self._run('build-from-seeds', '--force')

        self.assertEqual(code, 0)
        self.assertEqual(Catalog.load(self.catalog_path).normalized_feed_urls,
                         {'https://a.example/feed.xml'})

    def test_show_reports_the_snapshot(self):
        self._run('build-from-seeds')

        code, out, _err = self._run('show')

        self.assertEqual(code, 0)
        self.assertIn('https://a.example/feed.xml', out)
        self.assertIn('r/netsec', out)
        self.assertIn(catalog_tool.SEED_SOURCE, out)

    def test_show_without_a_catalog_fails_with_guidance(self):
        code, _out, err = self._run('show')

        self.assertEqual(code, 1)
        self.assertIn('No catalog snapshot', err)


if __name__ == '__main__':
    unittest.main()

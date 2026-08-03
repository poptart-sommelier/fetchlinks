"""Tests for the file-backed collection catalog."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.catalog import (
    Catalog,
    CatalogError,
    CatalogFeed,
    CatalogSubreddit,
    build_catalog,
    compute_revision,
)


def _catalog(**kwargs):
    return build_catalog(
        [('https://a.example/feed.xml', 'https://a.example/feed.xml'),
         ('https://b.example/feed.xml', 'https://b.example/Feed.xml')],
        [('netsec', 'netsec'), ('blueteamsec', 'BlueTeamSec')],
        **kwargs,
    )


class BuildCatalogTests(unittest.TestCase):
    def test_builds_from_normalized_display_pairs(self):
        catalog = _catalog()

        self.assertEqual([feed.normalized_url for feed in catalog.feeds],
                         ['https://a.example/feed.xml', 'https://b.example/feed.xml'])
        self.assertEqual([item.name for item in catalog.subreddits],
                         ['BlueTeamSec', 'netsec'])

    def test_first_entry_wins_on_duplicate_keys(self):
        catalog = build_catalog(
            [('https://a.example/feed.xml', 'https://a.example/feed.xml'),
             ('https://a.example/feed.xml', 'https://A.EXAMPLE/feed.xml')],
            [('netsec', 'netsec'), ('netsec', 'NetSec')],
        )

        self.assertEqual(len(catalog.feeds), 1)
        self.assertEqual(catalog.feeds[0].feed_url, 'https://a.example/feed.xml')
        self.assertEqual(len(catalog.subreddits), 1)
        self.assertEqual(catalog.subreddits[0].name, 'netsec')

    def test_entries_without_a_key_are_dropped(self):
        catalog = build_catalog([('', 'https://a.example/feed.xml')],
                                [('', 'netsec')])

        self.assertTrue(catalog.is_empty)

    def test_empty_catalog_is_empty(self):
        self.assertTrue(build_catalog().is_empty)
        self.assertFalse(_catalog().is_empty)

    def test_exposes_lookup_helpers(self):
        catalog = _catalog()

        self.assertEqual(
            catalog.feed_urls,
            (('https://a.example/feed.xml', 'https://a.example/feed.xml'),
             ('https://b.example/feed.xml', 'https://b.example/Feed.xml')),
        )
        self.assertEqual(catalog.normalized_feed_urls,
                         {'https://a.example/feed.xml', 'https://b.example/feed.xml'})
        self.assertEqual(catalog.normalized_subreddit_names,
                         {'netsec', 'blueteamsec'})


class RevisionTests(unittest.TestCase):
    def test_revision_is_derived_from_content_not_time(self):
        first = _catalog(generated_at='2026-01-01T00:00:00Z')
        second = _catalog(generated_at='2026-06-01T00:00:00Z')

        self.assertEqual(first.revision, second.revision)
        self.assertNotEqual(first.generated_at, second.generated_at)

    def test_revision_ignores_input_ordering(self):
        forward = build_catalog([('https://a/', 'https://a/'), ('https://b/', 'https://b/')])
        reverse = build_catalog([('https://b/', 'https://b/'), ('https://a/', 'https://a/')])

        self.assertEqual(forward.revision, reverse.revision)

    def test_revision_changes_when_an_entry_changes(self):
        base = _catalog()
        added = build_catalog(
            list(base.feed_urls) + [('https://c.example/feed.xml',
                                     'https://c.example/feed.xml')],
            [(item.normalized_name, item.name) for item in base.subreddits],
        )

        self.assertNotEqual(base.revision, added.revision)

    def test_revision_changes_when_only_the_display_form_changes(self):
        """The display URL is published, so a change to it is a real change."""
        first = build_catalog([('https://a.example/feed.xml',
                                'https://a.example/feed.xml')])
        second = build_catalog([('https://a.example/feed.xml',
                                 'https://A.example/feed.xml')])

        self.assertNotEqual(first.revision, second.revision)

    def test_feeds_and_subreddits_are_kept_distinct(self):
        """A name moving between sections must change the digest."""
        as_feed = compute_revision([CatalogFeed('netsec', 'netsec')], [])
        as_subreddit = compute_revision([], [CatalogSubreddit('netsec', 'netsec')])

        self.assertNotEqual(as_feed, as_subreddit)


class RoundTripTests(unittest.TestCase):
    def test_save_then_load_preserves_everything(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'catalog.v1.json'
            original = _catalog(source='postgresql', generated_at='2026-01-01T00:00:00Z')
            original.save(path)

            loaded = Catalog.load(path)

        self.assertEqual(loaded.revision, original.revision)
        self.assertEqual(loaded.source, 'postgresql')
        self.assertEqual(loaded.generated_at, '2026-01-01T00:00:00Z')
        self.assertEqual(loaded.feed_urls, original.feed_urls)
        self.assertEqual(loaded.normalized_subreddit_names,
                         original.normalized_subreddit_names)

    def test_an_empty_catalog_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'catalog.v1.json'
            build_catalog(source='postgresql').save(path)

            loaded = Catalog.load(path)

        self.assertTrue(loaded.is_empty)

    def test_save_is_atomic_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'nested' / 'catalog.v1.json'
            _catalog().save(path)

            self.assertEqual([entry.name for entry in path.parent.iterdir()],
                             ['catalog.v1.json'])


class LoadFailureTests(unittest.TestCase):
    def _write(self, tmp_dir, document):
        path = Path(tmp_dir) / 'catalog.v1.json'
        path.write_text(json.dumps(document), encoding='utf-8')
        return path

    def test_missing_file_explains_how_to_create_one(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'catalog.v1.json'

            with self.assertRaises(CatalogError) as raised:
                Catalog.load(path)

        self.assertIn('No catalog snapshot', str(raised.exception))
        self.assertIn('catalog sync', str(raised.exception))

    def test_malformed_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'catalog.v1.json'
            path.write_text('{not json', encoding='utf-8')

            with self.assertRaises(CatalogError) as raised:
                Catalog.load(path)

        self.assertIn('Malformed catalog', str(raised.exception))

    def test_a_json_array_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'catalog.v1.json'
            path.write_text('[]', encoding='utf-8')

            with self.assertRaises(CatalogError):
                Catalog.load(path)

    def test_a_tampered_entry_is_rejected_by_the_revision(self):
        """A truncated or hand-edited file must not stamp batches."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            document = _catalog().to_dict()
            document['rss_feeds'].pop()
            path = self._write(tmp_dir, document)

            with self.assertRaises(CatalogError) as raised:
                Catalog.load(path)

        self.assertIn('does not match', str(raised.exception))

    def test_duplicate_feed_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            document = _catalog().to_dict()
            document['rss_feeds'].append(dict(document['rss_feeds'][0]))
            path = self._write(tmp_dir, document)

            with self.assertRaises(CatalogError) as raised:
                Catalog.load(path)

        self.assertIn('duplicate normalized_url', str(raised.exception))

    def test_duplicate_subreddit_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            document = _catalog().to_dict()
            document['subreddits'].append(dict(document['subreddits'][0]))
            path = self._write(tmp_dir, document)

            with self.assertRaises(CatalogError) as raised:
                Catalog.load(path)

        self.assertIn('duplicate normalized_name', str(raised.exception))

    def test_a_missing_required_field_is_rejected_by_the_schema(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            document = _catalog().to_dict()
            del document['generated_at']
            path = self._write(tmp_dir, document)

            with self.assertRaises(CatalogError):
                Catalog.load(path)


class CatalogContentTests(unittest.TestCase):
    def test_catalog_carries_identity_only(self):
        """Health, counters, and cursors belong to state, not the catalog."""
        document = _catalog().to_dict()

        self.assertEqual(
            sorted(document),
            ['catalog_version', 'generated_at', 'revision', 'rss_feeds',
             'source', 'subreddits'],
        )
        self.assertEqual(sorted(document['rss_feeds'][0]),
                         ['feed_url', 'normalized_url'])
        self.assertEqual(sorted(document['subreddits'][0]),
                         ['name', 'normalized_name'])


if __name__ == '__main__':
    unittest.main()

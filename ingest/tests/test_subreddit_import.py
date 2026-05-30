import tempfile
import unittest
from pathlib import Path

import db_setup
import db_utils
import subreddit_import
from config import RedditSource


class CleanAndNormalizeTests(unittest.TestCase):
    def test_clean_strips_prefix_and_slashes_preserving_case(self):
        self.assertEqual(subreddit_import.clean_subreddit_name('r/NetSec'), 'NetSec')
        self.assertEqual(subreddit_import.clean_subreddit_name('/r/Python/'), 'Python')
        self.assertEqual(subreddit_import.clean_subreddit_name('  Golang  '), 'Golang')

    def test_normalize_lowercases(self):
        self.assertEqual(subreddit_import.normalize_subreddit_name('r/NetSec'), 'netsec')
        self.assertEqual(subreddit_import.normalize_subreddit_name('/r/Python/'), 'python')


class SeedIfEmptyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / 'db' / 'fetchlinks.db'
        db_setup.db_initial_setup(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_seeds_normalized_subreddits_when_empty(self):
        inserted = subreddit_import.seed_if_empty(
            ('r/Netsec', 'Python', 'netsec'), self.db_path
        )

        # 'r/Netsec' and 'netsec' collapse to one normalized row.
        self.assertEqual(inserted, 2)
        active = db_utils.db_get_active_subreddits(self.db_path)
        self.assertEqual(
            [(name, normalized) for (_id, name, normalized) in active],
            [('Netsec', 'netsec'), ('Python', 'python')],
        )

    def test_does_not_seed_when_table_already_populated(self):
        db_utils.db_insert_subreddits([('Existing', 'existing')], self.db_path)

        inserted = subreddit_import.seed_if_empty(('Netsec',), self.db_path)

        self.assertEqual(inserted, 0)
        active = db_utils.db_get_active_subreddits(self.db_path)
        self.assertEqual([normalized for (_id, _name, normalized) in active], ['existing'])

    def test_empty_seed_list_is_noop(self):
        self.assertEqual(subreddit_import.seed_if_empty((), self.db_path), 0)
        self.assertEqual(db_utils.db_count_subreddits(self.db_path), 0)

    def test_seed_creates_db_when_missing(self):
        fresh_path = Path(self._tmp.name) / 'fresh' / 'fetchlinks.db'
        inserted = subreddit_import.seed_if_empty(('Netsec',), fresh_path)
        self.assertEqual(inserted, 1)


class ParseArgsTests(unittest.TestCase):
    def test_requires_seed_if_empty(self):
        with self.assertRaises(SystemExit):
            subreddit_import.parse_args([])

    def test_accepts_seed_if_empty(self):
        args = subreddit_import.parse_args(['--seed-if-empty'])
        self.assertTrue(args.seed_if_empty)


class ReadFileAndResolveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_file_skips_comments_and_blanks(self):
        path = self.base / 'subreddits.txt'
        path.write_text(
            '# header comment\n\nNetsec\n  Python  \n# trailing comment\nr/Golang\n',
            encoding='utf-8',
        )
        self.assertEqual(
            subreddit_import.read_subreddits_file(path),
            ['Netsec', 'Python', 'r/Golang'],
        )

    def test_resolve_prefers_seed_file_over_inline_list(self):
        path = self.base / 'subreddits.txt'
        path.write_text('FromFile\n', encoding='utf-8')
        cfg = RedditSource(
            enabled=True,
            credential_location=Path('/tmp/reddit.json'),
            subreddits=('FromInline',),
            seed_file=path,
        )
        self.assertEqual(subreddit_import.resolve_seed_names(cfg), ['FromFile'])

    def test_resolve_falls_back_to_inline_list_when_no_seed_file(self):
        cfg = RedditSource(
            enabled=True,
            credential_location=Path('/tmp/reddit.json'),
            subreddits=('FromInline',),
            seed_file=None,
        )
        self.assertEqual(subreddit_import.resolve_seed_names(cfg), ['FromInline'])

    def test_resolve_falls_back_when_seed_file_missing(self):
        cfg = RedditSource(
            enabled=True,
            credential_location=Path('/tmp/reddit.json'),
            subreddits=('FromInline',),
            seed_file=self.base / 'does-not-exist.txt',
        )
        self.assertEqual(subreddit_import.resolve_seed_names(cfg), ['FromInline'])

    def test_resolve_handles_none_config(self):
        self.assertEqual(subreddit_import.resolve_seed_names(None), [])


if __name__ == '__main__':
    unittest.main()

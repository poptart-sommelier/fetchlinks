"""The collector boundary: no module on the collection path may touch a database.

The whole point of the split is that collection is destination-independent, and
that property is easy to lose by accident -- one convenience import of
``db_utils`` in a source module and the collector can no longer run on a machine
without the database. This asserts the boundary at the source level so the
regression is caught the moment it is written, not when the Pi fails to start.
"""

import ast
import unittest
from pathlib import Path

INGEST_DIR = Path(__file__).resolve().parent.parent

# Every module the collector loads, directly or transitively, on a normal run.
COLLECTOR_MODULES = (
    'fetch_links.py',
    'rss_links.py',
    'reddit_links.py',
    'bluesky_links.py',
    'mastodon_links.py',
    'catalog_seed.py',
    'catalog_tool.py',
    'spool_tool.py',
    'utils.py',
    'ingest_limits.py',
    'url_filters.py',
)

FORBIDDEN_MODULES = frozenset({
    'db_utils', 'db_setup', 'sqlite3', 'psycopg', 'psycopg2', 'sqlalchemy',
})

# ``utils.py`` still carries the legacy SQLite row helpers, which Phase 3
# removes. It imports nothing database-specific, so the import checks below
# still cover it; only the stricter text scan skips it.
TEXT_SCAN_EXEMPT = frozenset({'utils.py'})


def _imported_names(path: Path) -> set[str]:
    """Top-level module names imported by a file, however they are spelled."""
    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split('.')[0])
    return names


class CollectorBoundaryTests(unittest.TestCase):
    def test_no_collector_module_imports_a_database_library(self):
        for name in COLLECTOR_MODULES:
            with self.subTest(module=name):
                imported = _imported_names(INGEST_DIR / name)
                offenders = sorted(imported & FORBIDDEN_MODULES)
                self.assertEqual(
                    offenders, [],
                    f'{name} imports {offenders}; collection must stay '
                    'destination-independent',
                )

    def test_no_collector_module_mentions_a_database_library(self):
        """Catches deferred imports and getattr tricks, not just import lines."""
        for name in COLLECTOR_MODULES:
            if name in TEXT_SCAN_EXEMPT:
                continue
            with self.subTest(module=name):
                source = (INGEST_DIR / name).read_text(encoding='utf-8')
                for forbidden in sorted(FORBIDDEN_MODULES):
                    self.assertFalse(forbidden in source,
                                     f'{name} mentions {forbidden}')

    def test_no_pipeline_module_imports_a_database_library(self):
        for path in sorted((INGEST_DIR / 'pipeline').glob('*.py')):
            with self.subTest(module=path.name):
                offenders = sorted(_imported_names(path) & FORBIDDEN_MODULES)
                self.assertEqual(offenders, [], f'{path.name} imports {offenders}')

    def test_no_collector_module_takes_a_database_path_or_url(self):
        """A collector that accepts a connection string is one import away."""
        suspicious = ('db_path', 'control_db_path', 'database_url', 'DATABASE_URL')
        for name in COLLECTOR_MODULES:
            if name in TEXT_SCAN_EXEMPT:
                continue
            with self.subTest(module=name):
                source = (INGEST_DIR / name).read_text(encoding='utf-8')
                for token in suspicious:
                    self.assertFalse(token in source, f'{name} mentions {token}')


if __name__ == '__main__':
    unittest.main()

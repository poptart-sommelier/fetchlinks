"""Tests for export_rss_feeds."""
from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import db_setup
import db_utils
import export_rss_feeds


def _fresh_db(tmp: Path) -> Path:
    db_path = tmp / 'fetchlinks.db'
    db_setup.db_initial_setup(db_path)
    return db_path


def _insert(db_path: Path, *, url: str, enabled: int = 1, deleted_at: str | None = None,
            last_error: str | None = None, consecutive_failures: int = 0) -> None:
    import sqlite3
    norm = url.lower().rstrip('/')
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'INSERT INTO rss_feeds (feed_url, normalized_url, enabled, added_at, '
            'deleted_at, last_error, consecutive_failures) VALUES (?,?,?,?,?,?,?)',
            (url, norm, enabled, '2026-01-01 00:00:00', deleted_at,
             last_error, consecutive_failures),
        )


class ClassifyTests(unittest.TestCase):
    def test_splits_rows_into_three_buckets(self):
        rows = [
            {'feed_url': 'a', 'enabled': 1, 'deleted_at': None},
            {'feed_url': 'b', 'enabled': 0, 'deleted_at': None},
            {'feed_url': 'c', 'enabled': 1, 'deleted_at': '2026-05-01 00:00:00'},
            {'feed_url': 'd', 'enabled': 0, 'deleted_at': '2026-05-02 00:00:00'},
        ]
        active, disabled, removed = export_rss_feeds._classify(rows)
        self.assertEqual([r['feed_url'] for r in active], ['a'])
        self.assertEqual([r['feed_url'] for r in disabled], ['b'])
        self.assertEqual([r['feed_url'] for r in removed], ['c', 'd'])


class RenderSnapshotTests(unittest.TestCase):
    def test_header_includes_counts_and_timestamp(self):
        when = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        text = export_rss_feeds.render_snapshot([], when)
        self.assertIn('2026-05-01T12:00:00Z', text)
        self.assertIn('active=0', text)
        self.assertIn('disabled=0', text)
        self.assertIn('removed=0', text)
        self.assertIn('snapshot copy', text)

    def test_header_can_include_snapshot_path(self):
        output = Path('/var/lib/fetchlinks/rss_feeds.txt')
        text = export_rss_feeds.render_snapshot([], datetime(2026, 5, 1, tzinfo=UTC), output)
        self.assertIn(f'Snapshot path: {output}', text)

    def test_renders_three_sections_in_order(self):
        rows = [
            {'feed_url': 'https://a/', 'enabled': 1, 'deleted_at': None,
             'last_error': None, 'consecutive_failures': 0},
            {'feed_url': 'https://b/', 'enabled': 0, 'deleted_at': None,
             'last_error': 'HTTP 500', 'consecutive_failures': 12},
            {'feed_url': 'https://c/', 'enabled': 0, 'deleted_at': '2026-05-01 00:00:00',
             'last_error': None, 'consecutive_failures': 0},
        ]
        text = export_rss_feeds.render_snapshot(rows, datetime(2026, 5, 1, tzinfo=UTC))
        a_pos = text.index('--- active (1) ---')
        d_pos = text.index('--- disabled (1) ---')
        r_pos = text.index('--- removed (1) ---')
        self.assertLess(a_pos, d_pos)
        self.assertLess(d_pos, r_pos)
        self.assertIn('https://a/', text)
        self.assertIn('# https://b/  (failures=12 last_error=HTTP 500)', text)
        self.assertIn('# https://c/  (removed 2026-05-01 00:00:00)', text)

    def test_is_deterministic_for_same_input(self):
        rows = [
            {'feed_url': 'https://a/', 'enabled': 1, 'deleted_at': None,
             'last_error': None, 'consecutive_failures': 0},
        ]
        when = datetime(2026, 5, 1, tzinfo=UTC)
        self.assertEqual(
            export_rss_feeds.render_snapshot(rows, when),
            export_rss_feeds.render_snapshot(rows, when),
        )


class WriteAtomicTests(unittest.TestCase):
    def test_creates_parent_and_replaces_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'sub' / 'out.txt'
            export_rss_feeds.write_atomic(target, 'hello\n')
            self.assertEqual(target.read_text(encoding='utf-8'), 'hello\n')
            # Overwrites cleanly.
            export_rss_feeds.write_atomic(target, 'world\n')
            self.assertEqual(target.read_text(encoding='utf-8'), 'world\n')
            self.assertFalse(target.with_name('out.txt.tmp').exists())

    def test_falls_back_to_direct_write_when_atomic_replace_is_not_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'out.txt'
            target.write_text('old\n', encoding='utf-8')

            with patch.object(export_rss_feeds.os, 'replace', side_effect=PermissionError):
                export_rss_feeds.write_atomic(target, 'new\n')

            self.assertEqual(target.read_text(encoding='utf-8'), 'new\n')
            self.assertFalse(target.with_name('out.txt.tmp').exists())


class ExportFlowTests(unittest.TestCase):
    def test_export_writes_snapshot_and_returns_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = _fresh_db(tmp_path)
            _insert(db_path, url='https://active/', enabled=1)
            _insert(db_path, url='https://disabled/', enabled=0,
                    last_error='HTTP 500', consecutive_failures=10)
            _insert(db_path, url='https://removed/', enabled=0,
                    deleted_at='2026-05-01 00:00:00')
            output = tmp_path / 'snapshot.txt'

            stats = export_rss_feeds.export_rss_feeds(db_path, output)

            self.assertEqual(stats['total'], 3)
            self.assertEqual(stats['active'], 1)
            self.assertEqual(stats['disabled'], 1)
            self.assertEqual(stats['removed'], 1)
            body = output.read_text(encoding='utf-8')
            self.assertIn('https://active/', body)
            self.assertIn('# https://disabled/', body)
            self.assertIn('# https://removed/', body)

    def test_main_ignores_unrelated_source_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = _fresh_db(tmp_path)
            _insert(db_path, url='https://active/', enabled=1)
            output = tmp_path / 'snapshot.txt'
            log_file = tmp_path / 'logs' / 'export.log'
            config_path = tmp_path / 'fetchlinks.toml'
            config_path.write_text(
                f'''
[paths]
db = "{db_path}"
log_file = "{log_file}"

[sources.rss]
export_path = "{output}"

[sources.reddit]
enabled = true
credential_location = "{tmp_path / 'missing-reddit.json'}"
seed_file = "subreddits.txt"
'''.lstrip(),
                encoding='utf-8',
            )

            self.assertEqual(export_rss_feeds.main(['--config', str(config_path)]), 0)
            self.assertIn('https://active/', output.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

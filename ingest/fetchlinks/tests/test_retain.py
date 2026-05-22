import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import db_setup
import retain


def _seed_posts(db_path: Path, ages_days: list[int]) -> None:
    """Insert a row per entry in ``ages_days``. Each value is the row's age in days."""
    now = datetime.now(UTC)
    conn = sqlite3.connect(db_path)
    try:
        for i, age in enumerate(ages_days):
            ts = (now - timedelta(days=age)).strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                'INSERT INTO posts (source, author, description, direct_link, date_created, unique_id_string) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                ('rss', 'a', 'd', 'https://example/', ts, f'uid-{i}'),
            )
        conn.commit()
    finally:
        conn.close()


def _row_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    finally:
        conn.close()


class RunRetentionTests(unittest.TestCase):
    def test_deletes_rows_older_than_cutoff_keeps_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'fetchlinks.db'
            db_setup.db_initial_setup(db)
            # 1 month = ~30 days. Use 200 days old (well past 3 months) vs 5 days old.
            _seed_posts(db, [200, 200, 200, 5, 5])

            stats = retain.run_retention(db, max_age_months=3, vacuum_threshold_pages=0)

            self.assertEqual(stats['deleted'], 3)
            self.assertEqual(_row_count(db), 2)

    def test_skips_vacuum_when_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'fetchlinks.db'
            db_setup.db_initial_setup(db)
            _seed_posts(db, [200])

            # Threshold is huge; even if a real delete frees pages it won't trip.
            with patch.object(retain, '_freelist_count', side_effect=[0, 3]):
                stats = retain.run_retention(db, max_age_months=3, vacuum_threshold_pages=10_000)

            self.assertFalse(stats['vacuumed'])
            self.assertEqual(stats['freelist_before'], 0)
            self.assertEqual(stats['freelist_after'], 3)

    def test_runs_vacuum_when_threshold_met(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'fetchlinks.db'
            db_setup.db_initial_setup(db)
            _seed_posts(db, [200])

            with patch.object(retain, '_freelist_count', side_effect=[0, 5]):
                stats = retain.run_retention(db, max_age_months=3, vacuum_threshold_pages=1)

            self.assertTrue(stats['vacuumed'])
            self.assertEqual(stats['freelist_before'], 0)
            self.assertEqual(stats['freelist_after'], 5)

    def test_zero_threshold_skips_vacuum_even_with_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'fetchlinks.db'
            db_setup.db_initial_setup(db)
            _seed_posts(db, [200])

            with patch.object(retain, '_freelist_count', side_effect=[0, 99]):
                stats = retain.run_retention(db, max_age_months=3, vacuum_threshold_pages=0)

            self.assertFalse(stats['vacuumed'])


class MainFlowTests(unittest.TestCase):
    def _make_cfg(self, tmp: Path, *, retention_enabled=True, max_age=None, threshold=1000, ingest_age=3):
        from config import (
            AppConfig, IngestPolicy, PathsConfig, RetentionPolicy, Sources,
        )
        return AppConfig(
            paths=PathsConfig(db=tmp / 'x.db', log_file=tmp / 'x.log'),
            ingest=IngestPolicy(max_post_age_months=ingest_age),
            sources=Sources(),
            retention=RetentionPolicy(
                enabled=retention_enabled,
                max_post_age_months=max_age,
                vacuum_threshold_pages=threshold,
            ),
            source_path=tmp / 'fetchlinks.toml',
        )

    def test_main_runs_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = self._make_cfg(tmp_path, max_age=6, threshold=500)

            class _Args:
                config = tmp_path / 'fetchlinks.toml'

            with patch.object(retain.app_config, 'parse_arguments', return_value=_Args()), \
                 patch.object(retain.app_config, 'load_config', return_value=cfg), \
                 patch.object(retain, 'configure_logging'), \
                 patch.object(retain, 'run_retention', return_value={
                     'deleted': 7, 'freelist_before': 0, 'freelist_after': 0, 'vacuumed': False,
                 }) as run:
                retain.main()

            run.assert_called_once_with(cfg.paths.db, 6, 500)

    def test_main_falls_back_to_ingest_age_when_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = self._make_cfg(tmp_path, max_age=None, ingest_age=4)

            class _Args:
                config = tmp_path / 'fetchlinks.toml'

            with patch.object(retain.app_config, 'parse_arguments', return_value=_Args()), \
                 patch.object(retain.app_config, 'load_config', return_value=cfg), \
                 patch.object(retain, 'configure_logging'), \
                 patch.object(retain, 'run_retention', return_value={
                     'deleted': 0, 'freelist_before': 0, 'freelist_after': 0, 'vacuumed': False,
                 }) as run:
                retain.main()

            run.assert_called_once_with(cfg.paths.db, 4, 1000)

    def test_main_skips_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = self._make_cfg(tmp_path, retention_enabled=False)

            class _Args:
                config = tmp_path / 'fetchlinks.toml'

            with patch.object(retain.app_config, 'parse_arguments', return_value=_Args()), \
                 patch.object(retain.app_config, 'load_config', return_value=cfg), \
                 patch.object(retain, 'configure_logging'), \
                 patch.object(retain, 'run_retention') as run:
                retain.main()

            run.assert_not_called()

    def test_main_exits_one_on_exception(self):
        class _Args:
            config = Path('/tmp/bad.toml')

        with patch.object(retain.app_config, 'parse_arguments', return_value=_Args()), \
             patch.object(retain.app_config, 'load_config', side_effect=ValueError('bad')), \
             patch.object(retain.logging, 'exception') as log_exception:
            with self.assertRaises(SystemExit) as exc:
                retain.main()

        self.assertEqual(exc.exception.code, 1)
        log_exception.assert_called_once()


if __name__ == '__main__':
    unittest.main()

"""Retention job: prune old posts and (optionally) VACUUM the SQLite DB.

Runs as a oneshot, typically on a weekly systemd timer. Deletes posts whose
``date_created`` is older than ``retention.max_post_age_months`` (falling back
to ``ingest.max_post_age_months``). If the number of freelist pages grows by
more than ``retention.vacuum_threshold_pages`` as a result, the DB is
VACUUMed to reclaim disk space.
"""

from __future__ import annotations

import logging
import sqlite3
from logging import StreamHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config as app_config

logger = logging.getLogger(__name__)

_LOG_LEVEL_VALUES = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}


def configure_logging(cfg: app_config.AppConfig) -> None:
    """Mirror fetch_links.configure_logging so retention runs share the log file."""
    log_level = _LOG_LEVEL_VALUES.get(cfg.paths.log_level, logging.INFO)
    logging.basicConfig(
        handlers=[
            RotatingFileHandler(cfg.paths.log_file, maxBytes=1_000_000, backupCount=5, encoding="utf8"),
            StreamHandler(),
        ],
        level=log_level,
        format="%(asctime)s (%(module)s) %(levelname)s - %(message)s",
        datefmt="%d/%m/%Y %I:%M:%S %p",
    )


def _resolve_max_age(cfg: app_config.AppConfig) -> int:
    if cfg.retention.max_post_age_months is not None:
        return cfg.retention.max_post_age_months
    return cfg.ingest.max_post_age_months


def _freelist_count(conn: sqlite3.Connection) -> int:
    return conn.execute('PRAGMA freelist_count').fetchone()[0]


def run_retention(db_path: Path, max_age_months: int, vacuum_threshold_pages: int) -> dict:
    """Delete posts older than the cutoff; VACUUM if enough pages were freed.

    Returns a stats dict (``deleted``, ``freelist_before``, ``freelist_after``,
    ``vacuumed``) for callers / tests.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        freelist_before = _freelist_count(conn)

        cutoff_expr = f"datetime('now','-{int(max_age_months)} months')"
        cur = conn.execute(f'DELETE FROM posts WHERE date_created < {cutoff_expr}')
        deleted = cur.rowcount
        conn.commit()

        freelist_after = _freelist_count(conn)
        delta = freelist_after - freelist_before

        vacuumed = False
        if vacuum_threshold_pages > 0 and delta >= vacuum_threshold_pages:
            logger.info('Freed %d pages (>= threshold %d); VACUUMing',
                        delta, vacuum_threshold_pages)
            conn.execute('VACUUM')
            vacuumed = True
        else:
            logger.info('Freed %d pages (< threshold %d); skipping VACUUM',
                        delta, vacuum_threshold_pages)

        return {
            'deleted': deleted,
            'freelist_before': freelist_before,
            'freelist_after': freelist_after,
            'vacuumed': vacuumed,
        }
    finally:
        conn.close()


def main() -> None:
    try:
        args = app_config.parse_arguments()
        cfg = app_config.load_config(args.config)
        configure_logging(cfg)

        if not cfg.retention.enabled:
            logger.info('Retention disabled in config; nothing to do.')
            return

        max_age = _resolve_max_age(cfg)
        logger.info('Running retention against %s (max_post_age_months=%d, vacuum_threshold_pages=%d)',
                    cfg.paths.db, max_age, cfg.retention.vacuum_threshold_pages)
        stats = run_retention(cfg.paths.db, max_age, cfg.retention.vacuum_threshold_pages)
        logger.info('Retention complete: deleted=%d freelist_before=%d freelist_after=%d vacuumed=%s',
                    stats['deleted'], stats['freelist_before'], stats['freelist_after'], stats['vacuumed'])
    except Exception as exc:
        logging.exception('Retention failed: %s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()

"""Publisher command line: the destination-specific half of the pipeline.

Kept separate from the Collector entry point on purpose. This is the only
program that holds a database URL, and the Collector's configuration file has
nowhere to put one, so the two roles cannot quietly merge back together on a
machine where both happen to be installed.

Commands::

    publish_tool.py migrate            apply outstanding SQL migrations
    publish_tool.py bootstrap-catalog  seed the catalog from the seed files
    publish_tool.py sync-catalog       export the catalog for the Collector
    publish_tool.py publish            drain queued batches into PostgreSQL
    publish_tool.py retain             apply the post age limit
    publish_tool.py status             queue and database summary

The database URL comes from FETCHLINKS_DATABASE_URL or DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import config as app_config
from pipeline.layout import RuntimeLayout
from publisher import connection
from publisher.bootstrap import bootstrap_catalog
from publisher.catalog_sync import sync_catalog
from publisher.drain import drain_ready
from publisher.migrations import MigrationError, migrate
from publisher.retention import run_retention

logger = logging.getLogger(__name__)

DEFAULT_PRUNE_DAYS = 14


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s (%(module)s) %(levelname)s - %(message)s',
        stream=sys.stderr,
    )


def _load_config(args):
    if not args.config:
        return None
    # The Publisher shares fetchlinks.toml with the Collector but holds none of
    # its secrets, so a missing source credential must not stop a publish.
    return app_config.load_config(Path(args.config), require_credentials=False)


def _layout(args, cfg) -> RuntimeLayout:
    runtime_dir = args.runtime_dir or (cfg.paths.runtime_dir if cfg else None)
    return RuntimeLayout.resolve(runtime_dir)


# --- commands --------------------------------------------------------------


def cmd_migrate(args) -> int:
    with connection.connect(args.database_url) as conn:
        applied = migrate(
            conn,
            Path(args.migrations_dir) if args.migrations_dir else None,
            dry_run=args.dry_run,
        )
    if args.dry_run:
        print('Pending: ' + (', '.join(applied) if applied else 'none'))
    elif applied:
        print('Applied: ' + ', '.join(applied))
    else:
        print('Database is up to date')
    return 0


def cmd_bootstrap_catalog(args) -> int:
    cfg = _load_config(args)
    if cfg is None:
        print('bootstrap-catalog needs --config to find the seed files',
              file=sys.stderr)
        return 2

    rss = cfg.sources.rss
    seed_path = getattr(rss, 'seed_file', None) if rss else None
    with connection.connect(args.database_url) as conn:
        report = bootstrap_catalog(
            conn,
            feeds_seed_path=seed_path if seed_path and Path(seed_path).exists() else None,
            reddit_config=cfg.sources.reddit,
        )
    print(report.summary())
    return 0


def cmd_sync_catalog(args) -> int:
    cfg = _load_config(args)
    layout = _layout(args, cfg)
    layout.initialize()
    with connection.connect(args.database_url) as conn:
        catalog = sync_catalog(conn, layout.catalog_path)
    print(
        f'Catalog {catalog.revision[:12]}: {len(catalog.feeds)} feeds, '
        f'{len(catalog.subreddits)} subreddits -> {layout.catalog_path}'
    )
    return 0


def cmd_publish(args) -> int:
    cfg = _load_config(args)
    layout = _layout(args, cfg)
    layout.initialize()
    spool = layout.spool()

    with connection.connect(args.database_url) as conn:
        report = drain_ready(conn, spool, max_batches=args.max_batches)

    if args.prune_days > 0:
        spool.prune_published(args.prune_days)

    print(report.summary())
    # A quarantined batch or a stalled queue is an operational problem the
    # systemd unit should surface, not something to report as success.
    if report.failed or report.stopped_on:
        return 1
    return 0


def cmd_retain(args) -> int:
    cfg = _load_config(args)
    if args.max_age_months:
        max_age = args.max_age_months
    elif cfg is not None:
        max_age = (cfg.retention.max_post_age_months
                   or cfg.ingest.max_post_age_months)
    else:
        print('retain needs --max-age-months or --config', file=sys.stderr)
        return 2

    if cfg is not None and not cfg.retention.enabled and not args.force:
        print('Retention is disabled in config; nothing to do.')
        return 0

    with connection.connect(args.database_url) as conn:
        report = run_retention(conn, max_age)
    print(report.summary())
    return 0


_STATUS_COUNTS = """
SELECT
    (SELECT count(*) FROM content.posts),
    (SELECT count(*) FROM content.post_urls),
    (SELECT count(*) FROM content.published_batches),
    (SELECT max(published_at) FROM content.published_batches),
    (SELECT count(*) FROM catalog.rss_feeds WHERE enabled AND deleted_at IS NULL),
    (SELECT count(*) FROM catalog.subreddits WHERE enabled AND deleted_at IS NULL),
    (SELECT count(*) FROM content.rss_feed_health WHERE consecutive_failures > 0)
"""


def cmd_status(args) -> int:
    cfg = _load_config(args)
    layout = _layout(args, cfg)
    layout.initialize()

    status = {'runtime_dir': str(layout.root), 'spool': layout.spool().queue_stats()}

    try:
        with connection.connect(args.database_url) as conn, conn.cursor() as cur:
            cur.execute(_STATUS_COUNTS)
            row = cur.fetchone()
        status['database'] = {
            'posts': row[0],
            'post_urls': row[1],
            'published_batches': row[2],
            'last_published_at': row[3].isoformat() if row[3] else None,
            'live_feeds': row[4],
            'live_subreddits': row[5],
            'failing_feeds': row[6],
        }
    except Exception as exc:
        # Status must still report the local queue when the database is down;
        # that is precisely the situation it is most useful in.
        status['database'] = {'error': str(exc).strip()}

    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


# --- wiring ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--config', help='Path to fetchlinks.toml')
    parser.add_argument('--runtime-dir', help='Override the runtime directory')
    parser.add_argument('--database-url', help='Override the database URL')
    parser.add_argument('-v', '--verbose', action='store_true')

    sub = parser.add_subparsers(dest='command', required=True)

    migrate_parser = sub.add_parser('migrate', help='Apply SQL migrations')
    migrate_parser.add_argument('--migrations-dir')
    migrate_parser.add_argument('--dry-run', action='store_true')
    migrate_parser.set_defaults(func=cmd_migrate)

    bootstrap_parser = sub.add_parser(
        'bootstrap-catalog', help='Seed the catalog from the seed files'
    )
    bootstrap_parser.set_defaults(func=cmd_bootstrap_catalog)

    sync_parser = sub.add_parser(
        'sync-catalog', help='Export the catalog snapshot for the Collector'
    )
    sync_parser.set_defaults(func=cmd_sync_catalog)

    publish_parser = sub.add_parser('publish', help='Drain queued batches')
    publish_parser.add_argument('--max-batches', type=int, default=None)
    publish_parser.add_argument(
        '--prune-days', type=int, default=DEFAULT_PRUNE_DAYS,
        help='Delete published batches older than this; 0 disables pruning',
    )
    publish_parser.set_defaults(func=cmd_publish)

    retain_parser = sub.add_parser('retain', help='Apply the post age limit')
    retain_parser.add_argument('--max-age-months', type=int, default=None)
    retain_parser.add_argument(
        '--force', action='store_true',
        help='Run even when retention is disabled in config',
    )
    retain_parser.set_defaults(func=cmd_retain)

    status_parser = sub.add_parser('status', help='Queue and database summary')
    status_parser.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.func(args)
    except (connection.PublisherConfigError, MigrationError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception('Command failed: %s', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

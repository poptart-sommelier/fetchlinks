"""Import subreddits into the subreddits DB table.

One mode for now, mirroring ``rss_feed_import.py --seed-if-empty``:

- ``--seed-if-empty``: bulk INSERT OR IGNORE the subreddits resolved from the
  ``[sources.reddit]`` config (its ``seed_file`` if set, otherwise the inline
  ``subreddits`` list) only when the ``subreddits`` table is empty. No network
  calls. Used by bootstrap.

After the first seed the DB is the live source of truth; later use the web
admin to add/remove/restore subreddits.
"""
import argparse
from pathlib import Path
import sys

import config as app_config
import db_setup
import db_utils
from catalog_seed import (
    clean_subreddit_name,
    normalize_subreddit_name,
    read_subreddits_file,
)
from catalog_seed import resolve_seed_subreddit_names as resolve_seed_names


def seed_if_empty(subreddits, db_path: Path) -> int:
    """Bulk-insert ``subreddits`` only when the subreddits table is empty.

    ``subreddits`` is an iterable of raw names (optionally ``r/``-prefixed).
    Returns the number of rows inserted (0 if the table already had rows or
    no valid names were supplied). No network calls.
    """
    db_setup.db_initial_setup(db_path)
    if db_utils.db_count_subreddits(db_path) > 0:
        return 0

    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for subreddit in subreddits:
        normalized = normalize_subreddit_name(subreddit)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append((clean_subreddit_name(subreddit), normalized))

    if not rows:
        return 0
    return db_utils.db_insert_subreddits(rows, db_path)


def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(description='Import subreddits into the subreddits DB table.')
    parser.add_argument('--config', type=Path, default=app_config.DEFAULT_CONFIG,
                        help='Path to fetchlinks.toml (used to locate the DB and subreddit seed list)')
    parser.add_argument('--seed-if-empty', action='store_true', dest='seed_if_empty',
                        help='Seed the subreddits table from the configured seed_file or inline list only when empty (no network)')
    args = parser.parse_args(argv)
    if not args.seed_if_empty:
        parser.error('--seed-if-empty is required')
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cfg = app_config.load_config(args.config)
    # subreddits is a control-owned identity table; seed it into control_db
    # (which equals paths.db in single-host mode).
    db_path = cfg.paths.control_db

    subreddits = resolve_seed_names(cfg.sources.reddit)
    inserted = seed_if_empty(subreddits, db_path)
    if inserted:
        print(f'Seeded {inserted} subreddit(s) into subreddits from {args.config}')
    else:
        print('subreddits already populated (or no seed names configured); no changes made.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

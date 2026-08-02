"""Inspect the collection catalog, and build one from the seed files.

The production catalog is exported from the destination database, because the
web admin is where feeds and subreddits are actually managed. But that exporter
is destination-specific, and the collector must be testable without one, so
this also builds a catalog from the checked-in seed files.

``build-from-seeds`` is therefore both the bootstrap for a brand-new install
and the way to exercise collection on a machine that has never talked to a
database.

Usage::

    python catalog_tool.py show
    python catalog_tool.py build-from-seeds [--force]
"""

import argparse
import sys
from pathlib import Path

import catalog_seed
import config as app_config
from pipeline.catalog import Catalog, CatalogError, build_catalog
from pipeline.layout import RuntimeLayout

SEED_SOURCE = 'seed-files'


def catalog_from_seeds(cfg) -> Catalog:
    """Build a catalog from the configured RSS and subreddit seed files."""
    rss_config = cfg.sources.rss
    seed_file = getattr(rss_config, 'seed_file', None) if rss_config else None
    feed_pairs = catalog_seed.seed_feed_pairs(seed_file) if seed_file and Path(seed_file).exists() else []
    subreddit_pairs = catalog_seed.seed_subreddit_pairs(cfg.sources.reddit)
    return build_catalog(feed_pairs, subreddit_pairs, source=SEED_SOURCE)


def cmd_show(layout: RuntimeLayout, _args) -> int:
    try:
        catalog = Catalog.load(layout.catalog_path)
    except CatalogError as exc:
        print(f'{exc}', file=sys.stderr)
        return 1
    print(f'catalog:    {layout.catalog_path}')
    print(f'revision:   {catalog.revision}')
    print(f'generated:  {catalog.generated_at}')
    print(f'source:     {catalog.source}')
    print(f'feeds:      {len(catalog.feeds)}')
    for feed in catalog.feeds:
        print(f'  {feed.feed_url}')
    print(f'subreddits: {len(catalog.subreddits)}')
    for subreddit in catalog.subreddits:
        print(f'  r/{subreddit.name}')
    return 0


def cmd_build_from_seeds(layout: RuntimeLayout, args) -> int:
    cfg = app_config.load_config(args.config)
    catalog = catalog_from_seeds(cfg)
    if catalog.is_empty:
        print('Seed files produced no feeds or subreddits; nothing written.',
              file=sys.stderr)
        return 1

    # An exported catalog reflects live admin edits; a seed catalog does not.
    # Overwriting one with the other would silently resurrect feeds the admin
    # removed, so it takes an explicit --force.
    if layout.catalog_path.exists() and not args.force:
        existing = Catalog.load(layout.catalog_path)
        if existing.source != SEED_SOURCE:
            print(f'Refusing to overwrite a catalog exported from '
                  f'{existing.source!r}; pass --force to replace it.',
                  file=sys.stderr)
            return 1

    layout.initialize()
    catalog.save(layout.catalog_path)
    print(f'Wrote {layout.catalog_path}')
    print(f'  revision {catalog.revision}')
    print(f'  {len(catalog.feeds)} feed(s), {len(catalog.subreddits)} subreddit(s)')
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Inspect or build the collection catalog.')
    parser.add_argument('--config', type=Path, default=app_config.DEFAULT_CONFIG,
                        help='Path to fetchlinks.toml')
    parser.add_argument('--runtime-dir', type=Path, default=None,
                        help='Runtime directory (default: from config, then '
                             'FETCHLINKS_RUNTIME_DIR)')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('show', help='Print the current catalog snapshot')

    build = sub.add_parser('build-from-seeds',
                           help='Write a catalog from the configured seed files')
    build.add_argument('--force', action='store_true',
                       help='Replace a catalog that was exported from a database')

    args = parser.parse_args(argv)

    runtime_dir = args.runtime_dir
    if runtime_dir is None:
        try:
            runtime_dir = app_config.load_config(args.config).paths.runtime_dir
        except Exception:
            runtime_dir = None
    layout = RuntimeLayout.resolve(runtime_dir)

    handlers = {'show': cmd_show, 'build-from-seeds': cmd_build_from_seeds}
    try:
        return handlers[args.command](layout, args)
    except CatalogError as exc:
        print(f'{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

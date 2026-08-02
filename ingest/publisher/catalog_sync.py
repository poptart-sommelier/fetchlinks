"""Export the catalog from PostgreSQL into a local snapshot for the Collector.

This is the only path by which an admin's change to the feed or subreddit list
reaches the collecting machine. The Collector never queries the database; it
reads the file this writes.

The write is atomic and validated, so a Collector that runs mid-sync sees
either the old snapshot or the new one, never a half-written file, and a sync
against an unreachable database leaves the previous snapshot in place.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from pipeline.catalog import Catalog, build_catalog

logger = logging.getLogger(__name__)

CATALOG_SOURCE = 'postgresql'

_LIVE_FEEDS = """
SELECT normalized_url, feed_url
  FROM catalog.rss_feeds
 WHERE enabled AND deleted_at IS NULL
 ORDER BY normalized_url
"""

_LIVE_SUBREDDITS = """
SELECT normalized_name, name
  FROM catalog.subreddits
 WHERE enabled AND deleted_at IS NULL
 ORDER BY normalized_name
"""


def read_catalog(conn: psycopg.Connection) -> Catalog:
    """Build a catalog from the live catalog rows."""
    with conn.cursor() as cur:
        cur.execute(_LIVE_FEEDS)
        feed_pairs = cur.fetchall()
        cur.execute(_LIVE_SUBREDDITS)
        subreddit_pairs = cur.fetchall()
    return build_catalog(feed_pairs, subreddit_pairs, source=CATALOG_SOURCE)


def sync_catalog(conn: psycopg.Connection, path) -> Catalog:
    """Export the live catalog to ``path``, returning what was written.

    Refuses to write an empty catalog over a populated one. An empty result is
    far more often a pointed-at-the-wrong-database mistake than a genuine
    instruction to stop collecting everything, and the cost of being wrong is
    a Collector that quietly does nothing.
    """
    catalog = read_catalog(conn)
    path = Path(path)

    if catalog.is_empty and path.exists():
        try:
            existing = Catalog.load(path)
        except Exception:  # unreadable existing snapshot: overwrite is fine
            existing = None
        if existing is not None and not existing.is_empty:
            raise RuntimeError(
                f'Refusing to overwrite {path} with an empty catalog. The '
                f'database has no live feeds or subreddits; if that is really '
                f'intended, remove the snapshot by hand.'
            )

    catalog.save(path)
    logger.info(
        'Wrote catalog %s (%d feeds, %d subreddits) to %s',
        catalog.revision[:12], len(catalog.feeds), len(catalog.subreddits), path,
    )
    return catalog

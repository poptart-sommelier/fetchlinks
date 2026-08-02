"""Seed the PostgreSQL catalog from the committed seed files.

Run once when standing up a database. Idempotent by design and, more
importantly, non-destructive: an entry that already exists is left exactly as
it is. That matters because the seed files are a historical bootstrap list
while the catalog is live admin state — re-running this must never re-enable a
feed the admin disabled, or resurrect one they deleted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

from catalog_seed import seed_feed_pairs, seed_subreddit_pairs

logger = logging.getLogger(__name__)

_INSERT_FEED = """
INSERT INTO catalog.rss_feeds (feed_url, normalized_url)
VALUES (%s, %s)
ON CONFLICT (normalized_url) DO NOTHING
"""

_INSERT_SUBREDDIT = """
INSERT INTO catalog.subreddits (name, normalized_name)
VALUES (%s, %s)
ON CONFLICT (normalized_name) DO NOTHING
"""


@dataclass
class BootstrapReport:
    feeds_seen: int = 0
    feeds_inserted: int = 0
    subreddits_seen: int = 0
    subreddits_inserted: int = 0

    def summary(self) -> str:
        return (
            f'{self.feeds_inserted} of {self.feeds_seen} feeds and '
            f'{self.subreddits_inserted} of {self.subreddits_seen} subreddits '
            f'inserted; the rest were already catalogued'
        )


def bootstrap_catalog(
    conn: psycopg.Connection,
    *,
    feeds_seed_path=None,
    reddit_config=None,
) -> BootstrapReport:
    """Insert any seed entries the catalog does not already have."""
    report = BootstrapReport()

    feed_pairs = seed_feed_pairs(feeds_seed_path) if feeds_seed_path else []
    subreddit_pairs = seed_subreddit_pairs(reddit_config) if reddit_config else []
    report.feeds_seen = len(feed_pairs)
    report.subreddits_seen = len(subreddit_pairs)

    try:
        with conn.cursor() as cur:
            for normalized_url, feed_url in feed_pairs:
                cur.execute(_INSERT_FEED, (feed_url, normalized_url))
                report.feeds_inserted += cur.rowcount
            for normalized_name, name in subreddit_pairs:
                cur.execute(_INSERT_SUBREDDIT, (name, normalized_name))
                report.subreddits_inserted += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    logger.info('Catalog bootstrap: %s', report.summary())
    return report

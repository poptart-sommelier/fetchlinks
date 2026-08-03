"""PostgreSQL retention.

Replaces the SQLite retention job. Two differences worth noting:

* There is no VACUUM. PostgreSQL's autovacuum reclaims and reuses the space,
  and a manual ``VACUUM FULL`` would take an exclusive lock on the busiest
  table in the database to buy nothing the next insert would not.
* The cutoff is computed by the database, not by Python, so the answer does not
  depend on the clock of whichever machine happened to run the job.

``post_urls`` is removed by the foreign key's ON DELETE CASCADE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

logger = logging.getLogger(__name__)

_DELETE_OLD_POSTS = """
DELETE FROM content.posts
 WHERE posted_at < now() - make_interval(months => %s)
"""

_PRUNE_BATCH_LEDGER = """
DELETE FROM content.published_batches
 WHERE published_at < now() - make_interval(days => %s)
"""

#: The ledger only has to outlive the spool's own retention of published
#: batches, since a batch that no longer exists on disk can never be replayed.
#: A generous margin over the 14-day spool default.
DEFAULT_LEDGER_RETENTION_DAYS = 90


@dataclass
class RetentionReport:
    posts_deleted: int = 0
    batches_forgotten: int = 0

    def summary(self) -> str:
        return (
            f'{self.posts_deleted} posts deleted, '
            f'{self.batches_forgotten} batch ledger rows forgotten'
        )


def run_retention(
    conn: psycopg.Connection,
    max_age_months: int,
    *,
    ledger_retention_days: int = DEFAULT_LEDGER_RETENTION_DAYS,
) -> RetentionReport:
    """Delete posts past the age limit and forget long-published batch ids."""
    if max_age_months <= 0:
        raise ValueError('max_age_months must be positive')

    report = RetentionReport()
    try:
        with conn.cursor() as cur:
            cur.execute(_DELETE_OLD_POSTS, (max_age_months,))
            report.posts_deleted = max(cur.rowcount, 0)
            if ledger_retention_days > 0:
                cur.execute(_PRUNE_BATCH_LEDGER, (ledger_retention_days,))
                report.batches_forgotten = max(cur.rowcount, 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    logger.info('Retention: %s', report.summary())
    return report

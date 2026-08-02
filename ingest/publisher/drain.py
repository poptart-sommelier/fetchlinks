"""Drain the spool into PostgreSQL, oldest batch first.

The loop is deliberately unforgiving about the difference between the two ways
a batch can fail:

* A batch that can never succeed — truncated file, bad checksum, unknown
  contract version — is quarantined in ``failed`` so the queue keeps moving.
* A batch the database merely could not accept right now is left in
  ``processing`` and the drain stops. Stopping preserves FIFO order, and
  leaving it claimed means the next run recovers it rather than skipping it.

Anything else risks the one outcome the whole design exists to prevent: losing
collected records because a database was briefly unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import psycopg

from pipeline.spool import BatchValidationError, Spool

from .apply import PublishOutcome, apply_batch

logger = logging.getLogger(__name__)


@dataclass
class DrainReport:
    published: list[PublishOutcome] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped_on: tuple[str, str] | None = None

    @property
    def published_count(self) -> int:
        return len(self.published)

    @property
    def posts_inserted(self) -> int:
        return sum(outcome.posts_inserted for outcome in self.published)

    def summary(self) -> str:
        parts = [
            f'{self.published_count} batches published',
            f'{self.posts_inserted} new posts',
        ]
        if self.failed:
            parts.append(f'{len(self.failed)} quarantined')
        if self.stopped_on:
            parts.append(f'stopped on {self.stopped_on[0]}: {self.stopped_on[1]}')
        return ', '.join(parts)


def drain_ready(
    conn: psycopg.Connection,
    spool: Spool,
    *,
    max_batches: int | None = None,
) -> DrainReport:
    """Publish outstanding batches until the queue empties or something breaks."""
    report = DrainReport()
    processed = 0

    while max_batches is None or processed < max_batches:
        claimed = spool.claim_next()
        if claimed is None:
            break
        processed += 1

        try:
            outcome = apply_batch(conn, claimed)
        except BatchValidationError as exc:
            reason = str(exc)
            logger.error('Batch %s is invalid: %s', claimed.batch_id, reason)
            claimed.mark_failed(reason)
            report.failed.append((claimed.batch_id, reason))
            continue
        except psycopg.Error as exc:
            # Could be a dropped connection, a sleeping Neon compute, or a
            # constraint the data genuinely violates. Only the last is
            # permanent, and telling them apart reliably is not possible here,
            # so the safe reading is "retry later" — the batch stays claimed.
            reason = str(exc).strip()
            logger.error('Database refused batch %s: %s', claimed.batch_id, reason)
            report.stopped_on = (claimed.batch_id, reason)
            return report

        try:
            claimed.mark_published()
        except OSError as exc:
            # The data is committed; only the local archive move failed. The
            # next run re-claims the batch from `processing`, finds its id in
            # the ledger, and completes the archive without reapplying it.
            logger.error(
                'Batch %s committed but could not be archived: %s',
                claimed.batch_id, exc,
            )
            report.stopped_on = (claimed.batch_id, f'archive failed: {exc}')
            report.published.append(outcome)
            return report

        logger.info('%s', outcome.summary())
        report.published.append(outcome)

    return report

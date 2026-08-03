"""Apply one verified batch to PostgreSQL.

Everything a batch contains is applied in a single transaction, and the first
statement in that transaction claims the batch id in ``content.published_batches``.
That ordering is what makes the whole pipeline replay-safe: a second attempt at
the same batch conflicts immediately and applies nothing, so the failure mode
where the database commits but the spool directory fails to move costs nothing
more than a repeated claim.

Read this module against ``pipeline/contract.py``. Column names were chosen to
match the contract's field names so the mapping is checkable by eye.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

import psycopg

from pipeline import contract
from utils import build_hash

logger = logging.getLogger(__name__)

#: Batch fields are RFC 3339 UTC strings; the database wants aware datetimes.
_UTC = datetime.timezone.utc


def _timestamp(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC)


@dataclass
class PublishOutcome:
    """What applying a batch actually changed."""

    batch_id: str
    already_published: bool = False
    record_count: int = 0
    posts_inserted: int = 0
    posts_skipped: int = 0
    urls_inserted: int = 0
    observations_applied: int = 0
    checkpoints_applied: int = 0
    checkpoints_skipped: int = 0
    follows_replaced: list[str] = field(default_factory=list)
    follows_stale: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.already_published:
            return f'{self.batch_id}: already published, nothing applied'
        parts = [
            f'{self.posts_inserted} posts (+{self.urls_inserted} urls)',
            f'{self.observations_applied} rss observations',
            f'{self.checkpoints_applied} checkpoints',
            f'{len(self.follows_replaced)} follows snapshots',
        ]
        if self.posts_skipped:
            parts.append(f'{self.posts_skipped} duplicate posts skipped')
        if self.checkpoints_skipped:
            parts.append(f'{self.checkpoints_skipped} stale checkpoints skipped')
        if self.follows_stale:
            parts.append(f'{len(self.follows_stale)} stale snapshots skipped')
        return f'{self.batch_id}: ' + ', '.join(parts)


# --- ledger ----------------------------------------------------------------

_CLAIM_BATCH = """
INSERT INTO content.published_batches
    (batch_id, contract_version, batch_created_at, collector_version,
     collector_commit, catalog_revision, record_count)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (batch_id) DO NOTHING
"""

_RECORD_COUNTS = """
UPDATE content.published_batches
   SET posts_inserted = %s, urls_inserted = %s
 WHERE batch_id = %s
"""


def _claim(cur, manifest: contract.Manifest) -> bool:
    """Take the batch id. Returns False if another run already has it."""
    cur.execute(_CLAIM_BATCH, (
        manifest.batch_id,
        manifest.contract_version,
        _timestamp(manifest.created_at),
        manifest.collector_version,
        manifest.collector_commit,
        manifest.catalog_revision,
        manifest.total_records,
    ))
    return cur.rowcount == 1


# --- posts -----------------------------------------------------------------

_INSERT_POST = """
INSERT INTO content.posts
    (unique_id, source, source_type, author, description, direct_link, posted_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (unique_id) DO NOTHING
RETURNING post_id
"""

_INSERT_URL = """
INSERT INTO content.post_urls (post_id, position, url, url_hash)
VALUES (%s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


def _apply_posts(cur, claimed, outcome: PublishOutcome) -> None:
    for record in claimed.records(contract.KIND_POSTS):
        cur.execute(_INSERT_POST, (
            record['unique_id'],
            record.get('source') or '',
            record['source_type'],
            record.get('author') or '',
            record.get('description') or '',
            record.get('direct_link') or '',
            _timestamp(record['posted_at']),
        ))
        row = cur.fetchone()
        if row is None:
            # The post is already stored. Its URL rows are left alone: the
            # contract's identity is the URL set, so re-deriving them could
            # only reproduce what is there or contradict an unshortened_url
            # that has since been resolved.
            outcome.posts_skipped += 1
            continue

        post_id = row[0]
        outcome.posts_inserted += 1

        # Position and hash are derived here rather than carried in the batch,
        # so a Collector built against an older hashing scheme cannot pin one
        # into the database.
        url_rows = [
            (post_id, position, url, build_hash(url))
            for position, url in enumerate(record.get('urls') or ())
        ]
        if url_rows:
            cur.executemany(_INSERT_URL, url_rows)
            outcome.urls_inserted += max(cur.rowcount, 0)


# --- rss health ------------------------------------------------------------
#
# The batch carries the observation; the counter is the Publisher's conclusion
# from it. Keeping the counter out of the batch is what stops a replay from
# inflating feed health, and the ledger claim is what stops a replay at all.

_HEALTH_SUCCESS = """
INSERT INTO content.rss_feed_health
    (normalized_url, last_fetched_at, last_success_at, last_status, last_error,
     consecutive_failures, etag, last_modified, latest_entry_at, site_link)
VALUES (%(url)s, %(observed)s, %(observed)s, %(status)s, NULL, 0,
        %(etag)s, %(last_modified)s, %(latest_entry)s, %(site_link)s)
ON CONFLICT (normalized_url) DO UPDATE SET
    last_fetched_at      = EXCLUDED.last_fetched_at,
    last_success_at      = EXCLUDED.last_success_at,
    last_status          = EXCLUDED.last_status,
    last_error           = NULL,
    consecutive_failures = 0,
    etag                 = EXCLUDED.etag,
    last_modified        = EXCLUDED.last_modified,
    -- A 304 carries no entries and no site link. Coalescing keeps the last
    -- known values instead of blanking them on every unchanged fetch.
    latest_entry_at      = COALESCE(EXCLUDED.latest_entry_at,
                                    content.rss_feed_health.latest_entry_at),
    site_link            = COALESCE(EXCLUDED.site_link,
                                    content.rss_feed_health.site_link)
WHERE content.rss_feed_health.last_fetched_at IS NULL
   OR EXCLUDED.last_fetched_at >= content.rss_feed_health.last_fetched_at
"""

_HEALTH_FAILURE = """
INSERT INTO content.rss_feed_health
    (normalized_url, last_fetched_at, last_status, last_error, consecutive_failures)
VALUES (%(url)s, %(observed)s, %(status)s, %(error)s, 1)
ON CONFLICT (normalized_url) DO UPDATE SET
    last_fetched_at      = EXCLUDED.last_fetched_at,
    last_status          = EXCLUDED.last_status,
    last_error           = EXCLUDED.last_error,
    consecutive_failures = content.rss_feed_health.consecutive_failures + 1
WHERE content.rss_feed_health.last_fetched_at IS NULL
   OR EXCLUDED.last_fetched_at >= content.rss_feed_health.last_fetched_at
"""


def _apply_rss_observations(cur, claimed, outcome: PublishOutcome) -> None:
    for record in claimed.records(contract.KIND_RSS_OBSERVATIONS):
        params = {
            'url': record['normalized_url'],
            'observed': _timestamp(record['observed_at']),
            'status': record.get('status'),
            'error': record.get('error'),
            'etag': record.get('etag'),
            'last_modified': record.get('last_modified'),
            'latest_entry': _timestamp(record.get('latest_entry_at')),
            'site_link': record.get('site_link'),
        }
        cur.execute(
            _HEALTH_SUCCESS if record['success'] else _HEALTH_FAILURE, params
        )
        outcome.observations_applied += max(cur.rowcount, 0)


# --- checkpoints -----------------------------------------------------------
#
# Each guarded by observed_at so an out-of-order batch cannot rewind a cursor.
# These rows are a published *view* of the Collector's resume position; the
# authoritative copy is the Collector's own state file.

_CHECKPOINT_SQL = {
    'reddit': """
        INSERT INTO content.reddit_state
            (subreddit, last_seen_fullname, source_url, observed_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (subreddit) DO UPDATE SET
            last_seen_fullname = EXCLUDED.last_seen_fullname,
            source_url         = EXCLUDED.source_url,
            observed_at        = EXCLUDED.observed_at
        WHERE EXCLUDED.observed_at > content.reddit_state.observed_at
    """,
    'bluesky': """
        INSERT INTO content.bluesky_state
            (source_key, cursor, source_url, observed_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_key) DO UPDATE SET
            cursor      = EXCLUDED.cursor,
            source_url  = EXCLUDED.source_url,
            observed_at = EXCLUDED.observed_at
        WHERE EXCLUDED.observed_at > content.bluesky_state.observed_at
    """,
    'mastodon': """
        INSERT INTO content.mastodon_state
            (source_name, last_seen_id, instance_url, observed_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_name) DO UPDATE SET
            last_seen_id = EXCLUDED.last_seen_id,
            instance_url = EXCLUDED.instance_url,
            observed_at  = EXCLUDED.observed_at
        WHERE EXCLUDED.observed_at > content.mastodon_state.observed_at
    """,
}


def _apply_checkpoints(cur, claimed, outcome: PublishOutcome) -> None:
    for record in claimed.records(contract.KIND_CHECKPOINTS):
        statement = _CHECKPOINT_SQL.get(record['source_type'])
        if statement is None:
            # A source this build does not know about. Skipped rather than
            # failed: these rows are informational, and quarantining an
            # otherwise valid batch over one would strand its posts.
            logger.warning(
                'Batch %s carries a checkpoint for unknown source type %r; skipping',
                claimed.batch_id, record['source_type'],
            )
            outcome.checkpoints_skipped += 1
            continue
        cur.execute(statement, (
            record['source_key'],
            record['cursor'],
            record.get('source_url'),
            _timestamp(record['observed_at']),
        ))
        if cur.rowcount == 1:
            outcome.checkpoints_applied += 1
        else:
            outcome.checkpoints_skipped += 1


# --- follows ---------------------------------------------------------------
#
# Follows arrive as complete snapshots, so applying one is a replacement.
# ``content.follows_snapshots`` records when each scope was last observed and
# gates the replacement, so a batch delayed behind a database outage cannot
# reinstate a follow list that a later batch has already superseded.

_CLAIM_SNAPSHOT = """
INSERT INTO content.follows_snapshots (source_type, scope, observed_at, record_count)
VALUES (%s, %s, %s, %s)
ON CONFLICT (source_type, scope) DO UPDATE SET
    observed_at  = EXCLUDED.observed_at,
    record_count = EXCLUDED.record_count
WHERE EXCLUDED.observed_at > content.follows_snapshots.observed_at
"""

_INSERT_BLUESKY_FOLLOW = """
INSERT INTO content.bluesky_follows (did, handle, display_name, synced_at)
VALUES (%s, %s, %s, %s)
ON CONFLICT (did) DO UPDATE SET
    handle       = EXCLUDED.handle,
    display_name = EXCLUDED.display_name,
    synced_at    = EXCLUDED.synced_at
"""

_INSERT_MASTODON_FOLLOW = """
INSERT INTO content.mastodon_follows
    (instance_name, account_id, acct, display_name, url, synced_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (instance_name, account_id) DO UPDATE SET
    acct         = EXCLUDED.acct,
    display_name = EXCLUDED.display_name,
    url          = EXCLUDED.url,
    synced_at    = EXCLUDED.synced_at
"""


def _claim_snapshot(cur, source_type: str, scope: str, observed, count: int) -> bool:
    cur.execute(_CLAIM_SNAPSHOT, (source_type, scope, observed, count))
    return cur.rowcount == 1


def _apply_bluesky_follows(cur, claimed, manifest, outcome: PublishOutcome) -> None:
    for entry, records in claimed.snapshots(contract.KIND_BLUESKY_FOLLOWS):
        # Bluesky follows are a single unscoped list; the empty scope says so.
        scope = entry.scope or ''
        observed = _timestamp(entry.observed_at or manifest.created_at)
        if not _claim_snapshot(cur, 'bluesky', scope, observed, entry.record_count):
            outcome.follows_stale.append('bluesky')
            continue
        rows = list(records)
        cur.execute('DELETE FROM content.bluesky_follows')
        for record in rows:
            cur.execute(_INSERT_BLUESKY_FOLLOW, (
                record['did'], record['handle'], record.get('display_name'), observed,
            ))
        outcome.follows_replaced.append('bluesky')


def _apply_mastodon_follows(cur, claimed, manifest, outcome: PublishOutcome) -> None:
    for entry, records in claimed.snapshots(contract.KIND_MASTODON_FOLLOWS):
        scope = entry.scope or ''
        observed = _timestamp(entry.observed_at or manifest.created_at)
        if not _claim_snapshot(cur, 'mastodon', scope, observed, entry.record_count):
            outcome.follows_stale.append(f'mastodon:{scope}')
            continue
        rows = list(records)
        cur.execute(
            'DELETE FROM content.mastodon_follows WHERE instance_name = %s', (scope,)
        )
        for record in rows:
            cur.execute(_INSERT_MASTODON_FOLLOW, (
                scope,
                record['account_id'],
                record['acct'],
                record.get('display_name'),
                record.get('url'),
                observed,
            ))
        outcome.follows_replaced.append(f'mastodon:{scope}')


# --- entry point -----------------------------------------------------------


def apply_batch(conn: psycopg.Connection, claimed) -> PublishOutcome:
    """Apply a claimed batch inside one transaction.

    The batch is fully verified — checksums, counts and schemas — before a
    single statement runs, so a truncated file is caught by the spool rather
    than half-applied by the database.

    The caller owns the spool-side outcome: commit here means the data is in
    PostgreSQL, and the caller then archives the batch.
    """
    manifest = claimed.verify()
    outcome = PublishOutcome(
        batch_id=claimed.batch_id, record_count=manifest.total_records
    )

    try:
        with conn.cursor() as cur:
            if not _claim(cur, manifest):
                # Already applied by an earlier run that failed before it could
                # archive the directory. Nothing to do but let the caller
                # finish the local move.
                conn.rollback()
                outcome.already_published = True
                return outcome

            _apply_posts(cur, claimed, outcome)
            _apply_rss_observations(cur, claimed, outcome)
            _apply_checkpoints(cur, claimed, outcome)
            _apply_bluesky_follows(cur, claimed, manifest, outcome)
            _apply_mastodon_follows(cur, claimed, manifest, outcome)

            cur.execute(_RECORD_COUNTS, (
                outcome.posts_inserted, outcome.urls_inserted, manifest.batch_id,
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return outcome

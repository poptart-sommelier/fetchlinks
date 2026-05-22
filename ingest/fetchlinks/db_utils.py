import sqlite3
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _ensure_bluesky_state_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS bluesky_state (
    idx INTEGER PRIMARY KEY,
    cursor TEXT,
    time_created TEXT)
    """)


def db_insert(fetched_data, db_location):
    """Insert posts and their URLs atomically.

    Returns the number of newly inserted posts (existing posts, identified by
    `unique_id_string`, are skipped — their URL rows are not re-inserted).
    """
    if not fetched_data:
        return 0

    insert_post_sql = (
        'INSERT OR IGNORE INTO posts '
        '(source, author, description, direct_link, date_created, unique_id_string) '
        'VALUES (?, ?, ?, ?, ?, ?)'
    )
    insert_url_sql = (
        'INSERT OR IGNORE INTO post_urls (post_id, position, url, url_hash) '
        'VALUES (?, ?, ?, ?)'
    )

    inserted = 0
    try:
        with sqlite3.connect(db_location) as db:
            db.execute('PRAGMA foreign_keys=ON')
            cur = db.cursor()
            for post in fetched_data:
                cur.execute(insert_post_sql, post.get_post_row())
                if cur.rowcount == 0:
                    # Post already exists; leave its URL rows alone.
                    continue
                post_id = cur.lastrowid
                url_rows = [
                    (post_id, position, url, url_hash)
                    for (position, url, url_hash) in post.get_url_rows()
                ]
                if url_rows:
                    cur.executemany(insert_url_sql, url_rows)
                inserted += 1
            db.commit()
            return inserted
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load posts into posts table: {exc}') from exc


def db_get_bluesky_cursor(db_location):
    # Read latest by idx so we still work on legacy DBs that have multiple rows.
    db_command = """SELECT cursor FROM bluesky_state ORDER BY idx DESC LIMIT 1"""

    try:
        with sqlite3.connect(db_location) as db:
            _ensure_bluesky_state_table(db)
            cur = db.cursor()
            cur.execute(db_command)
            result = cur.fetchone()
            if not result:
                return None
            cursor = result[0]
            return cursor if cursor else None
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not retrieve bluesky cursor: {exc}') from exc


def db_set_bluesky_cursor(cursor, db_location):
    # Upsert single-row state at idx=1 so the table doesn't grow unbounded.
    # Also clean up any legacy rows from when this table grew on every run.
    upsert_sql = (
        'INSERT INTO bluesky_state (idx, cursor, time_created) VALUES (1, ?, ?) '
        'ON CONFLICT(idx) DO UPDATE SET cursor = excluded.cursor, '
        'time_created = excluded.time_created'
    )
    cleanup_sql = 'DELETE FROM bluesky_state WHERE idx != 1'

    try:
        with sqlite3.connect(db_location) as db:
            _ensure_bluesky_state_table(db)
            cur = db.cursor()
            cur.execute(upsert_sql, [cursor or '', datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')])
            cur.execute(cleanup_sql)
            db.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not persist bluesky cursor: {exc}') from exc


def _ensure_rss_feed_state_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS rss_feed_state (
    feed_url TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    last_status INTEGER,
    last_fetched TEXT)
    """)


def _ensure_rss_feeds_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS rss_feeds (
    feed_id              INTEGER PRIMARY KEY,
    feed_url             TEXT NOT NULL,
    normalized_url       TEXT NOT NULL UNIQUE,
    enabled              INTEGER NOT NULL DEFAULT 1,
    added_at             TEXT NOT NULL,
    deleted_at           TEXT,
    last_fetched_at      TEXT,
    last_success_at      TEXT,
    last_status          INTEGER,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    etag                 TEXT,
    last_modified        TEXT,
    latest_entry_at      TEXT)
    """)
    db.execute(
        'CREATE INDEX IF NOT EXISTS idx_rss_feeds_live '
        'ON rss_feeds(enabled, deleted_at)'
    )


def _ensure_reddit_state_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS reddit_state (
    subreddit TEXT PRIMARY KEY,
    last_seen_fullname TEXT,
    time_created TEXT)
    """)


def _ensure_mastodon_state_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS mastodon_state (
    source_name TEXT PRIMARY KEY,
    instance_url TEXT NOT NULL,
    last_seen_id TEXT,
    time_created TEXT)
    """)


def db_get_rss_feed_states(db_location):
    """Return a {feed_url: (etag, last_modified)} map for all known feeds."""
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feed_state_table(db)
            cur = db.cursor()
            cur.execute('SELECT feed_url, etag, last_modified FROM rss_feed_state')
            return {row[0]: (row[1] or '', row[2] or '') for row in cur.fetchall()}
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load RSS feed state: {exc}') from exc


def db_set_rss_feed_states(states, db_location):
    """Persist a list of (feed_url, etag, last_modified, last_status) tuples."""
    if not states:
        return
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    rows = [(url, etag or '', last_mod or '', status, now) for (url, etag, last_mod, status) in states]
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feed_state_table(db)
            db.executemany("""
                INSERT INTO rss_feed_state (feed_url, etag, last_modified, last_status, last_fetched)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(feed_url) DO UPDATE SET
                    etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    last_status=excluded.last_status,
                    last_fetched=excluded.last_fetched
            """, rows)
            db.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not persist RSS feed state: {exc}') from exc


# --- rss_feeds (subscription + health, supersedes rss_feed_state) ---------


def db_count_rss_feeds(db_location):
    """Return total row count of rss_feeds (including tombstoned)."""
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            return db.execute('SELECT COUNT(*) FROM rss_feeds').fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not count rss_feeds: {exc}') from exc


def db_insert_rss_feeds(feeds, db_location):
    """Bulk INSERT OR IGNORE feeds.

    ``feeds`` is an iterable of ``(feed_url, normalized_url)`` tuples.
    Rows whose ``normalized_url`` collides with an existing row (including
    tombstoned rows where ``deleted_at`` is set) are skipped. Returns the
    number of rows actually inserted.
    """
    feeds = list(feeds)
    if not feeds:
        return 0
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    rows = [(feed_url, normalized, now) for (feed_url, normalized) in feeds]
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            inserted = 0
            cur = db.cursor()
            for row in rows:
                cur.execute(
                    'INSERT OR IGNORE INTO rss_feeds '
                    '(feed_url, normalized_url, added_at) VALUES (?, ?, ?)',
                    row,
                )
                inserted += cur.rowcount
            db.commit()
            return inserted
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not insert rss_feeds: {exc}') from exc


def db_get_active_rss_feeds(db_location):
    """Return live (enabled, not tombstoned) feeds for ingestion.

    Each row is ``(feed_id, feed_url, etag, last_modified)``. Cache headers
    are returned as empty strings when NULL so callers can use them directly
    in HTTP header construction.
    """
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            cur = db.execute(
                'SELECT feed_id, feed_url, etag, last_modified '
                'FROM rss_feeds '
                'WHERE enabled = 1 AND deleted_at IS NULL '
                'ORDER BY feed_id'
            )
            return [
                (row[0], row[1], row[2] or '', row[3] or '')
                for row in cur.fetchall()
            ]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load active rss_feeds: {exc}') from exc


def db_update_rss_feed_after_fetch(results, db_location, auto_disable_after):
    """Persist per-feed fetch outcomes.

    ``results`` is an iterable of dicts with keys:
        feed_id (int), status (int), etag (str), last_modified (str),
        error (str|None), latest_entry_at (str|None, optional).

    Status 200 or 304 counts as success: ``consecutive_failures`` resets to
    0, ``last_success_at`` is updated, cache headers are persisted. Any
    other status counts as failure: ``consecutive_failures`` is incremented
    and ``last_error`` recorded. When ``auto_disable_after`` is positive
    and the counter reaches that threshold, ``enabled`` flips to 0.

    Returns the number of feeds auto-disabled on this call.
    """
    results = list(results)
    if not results:
        return 0
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    auto_disabled = 0
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            for r in results:
                status = r.get('status') or 0
                success = status in (200, 304)
                etag = r.get('etag') or None
                last_mod = r.get('last_modified') or None
                latest_entry = r.get('latest_entry_at')
                error = r.get('error')

                if success:
                    db.execute(
                        'UPDATE rss_feeds SET '
                        '  last_fetched_at = ?, '
                        '  last_success_at = ?, '
                        '  last_status     = ?, '
                        '  last_error      = NULL, '
                        '  consecutive_failures = 0, '
                        '  etag            = ?, '
                        '  last_modified   = ?, '
                        '  latest_entry_at = COALESCE(?, latest_entry_at) '
                        'WHERE feed_id = ?',
                        (now, now, status, etag, last_mod,
                         latest_entry, r['feed_id']),
                    )
                else:
                    db.execute(
                        'UPDATE rss_feeds SET '
                        '  last_fetched_at = ?, '
                        '  last_status     = ?, '
                        '  last_error      = ?, '
                        '  consecutive_failures = consecutive_failures + 1 '
                        'WHERE feed_id = ?',
                        (now, status, error, r['feed_id']),
                    )
                    if auto_disable_after and auto_disable_after > 0:
                        cur = db.execute(
                            'UPDATE rss_feeds SET enabled = 0 '
                            'WHERE feed_id = ? '
                            '  AND enabled = 1 '
                            '  AND consecutive_failures >= ?',
                            (r['feed_id'], auto_disable_after),
                        )
                        if cur.rowcount:
                            auto_disabled += cur.rowcount
            db.commit()
            return auto_disabled
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not update rss_feeds health: {exc}') from exc


def db_get_all_rss_feeds(db_location):
    """Return every feed row, including disabled and tombstoned.

    Used by the exporter and any future admin tooling. Each row is a dict
    with all canonical columns from ``rss_feeds``.
    """
    cols = ['feed_id', 'feed_url', 'normalized_url', 'enabled', 'added_at',
            'deleted_at', 'last_fetched_at', 'last_success_at', 'last_status',
            'last_error', 'consecutive_failures', 'etag', 'last_modified',
            'latest_entry_at']
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            cur = db.execute(
                f'SELECT {", ".join(cols)} FROM rss_feeds '
                'ORDER BY normalized_url'
            )
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load rss_feeds: {exc}') from exc


def db_get_reddit_states(db_location):
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_reddit_state_table(db)
            cur = db.cursor()
            cur.execute('SELECT subreddit, last_seen_fullname FROM reddit_state')
            return {row[0]: row[1] for row in cur.fetchall() if row[1]}
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load Reddit state: {exc}') from exc


def db_set_reddit_states(states, db_location):
    if not states:
        return
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    rows = [(subreddit, fullname or '', now) for (subreddit, fullname) in states]
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_reddit_state_table(db)
            db.executemany("""
                INSERT INTO reddit_state (subreddit, last_seen_fullname, time_created)
                VALUES (?, ?, ?)
                ON CONFLICT(subreddit) DO UPDATE SET
                    last_seen_fullname=excluded.last_seen_fullname,
                    time_created=excluded.time_created
            """, rows)
            db.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not persist Reddit state: {exc}') from exc


def db_get_mastodon_last_seen_id(source_name, db_location):
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_mastodon_state_table(db)
            cur = db.cursor()
            cur.execute('SELECT last_seen_id FROM mastodon_state WHERE source_name = ?', [source_name])
            result = cur.fetchone()
            if not result:
                return None
            last_seen_id = result[0]
            return last_seen_id if last_seen_id else None
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not retrieve mastodon state for {source_name}: {exc}') from exc


def db_set_mastodon_last_seen_id(source_name, instance_url, last_seen_id, db_location):
    upsert_sql = (
        'INSERT INTO mastodon_state (source_name, instance_url, last_seen_id, time_created) '
        'VALUES (?, ?, ?, ?) '
        'ON CONFLICT(source_name) DO UPDATE SET instance_url = excluded.instance_url, '
        'last_seen_id = excluded.last_seen_id, time_created = excluded.time_created'
    )

    try:
        with sqlite3.connect(db_location) as db:
            _ensure_mastodon_state_table(db)
            db.execute(
                upsert_sql,
                [
                    source_name,
                    instance_url,
                    last_seen_id or '',
                    datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S'),
                ],
            )
            db.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not persist mastodon state for {source_name}: {exc}') from exc

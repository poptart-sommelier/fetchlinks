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
        '(source, source_type, author, description, direct_link, date_created, unique_id_string) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)'
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


def _ensure_rss_feed_health_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS rss_feed_health (
    normalized_url       TEXT PRIMARY KEY,
    last_fetched_at      TEXT,
    last_success_at      TEXT,
    last_status          INTEGER,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    etag                 TEXT,
    last_modified        TEXT,
    latest_entry_at      TEXT,
    site_link            TEXT)
    """)


def _ensure_reddit_state_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS reddit_state (
    subreddit TEXT PRIMARY KEY,
    last_seen_fullname TEXT,
    time_created TEXT)
    """)


def _ensure_subreddits_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS subreddits (
    subreddit_id    INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    enabled         INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT NOT NULL,
    deleted_at      TEXT)
    """)
    db.execute(
        'CREATE INDEX IF NOT EXISTS idx_subreddits_live '
        'ON subreddits(enabled, deleted_at)'
    )


def _ensure_mastodon_state_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS mastodon_state (
    source_name TEXT PRIMARY KEY,
    instance_url TEXT NOT NULL,
    last_seen_id TEXT,
    time_created TEXT)
    """)


def _ensure_bluesky_follows_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS bluesky_follows (
    did          TEXT PRIMARY KEY,
    handle       TEXT NOT NULL,
    display_name TEXT,
    synced_at    TEXT NOT NULL)
    """)


def _ensure_mastodon_follows_table(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS mastodon_follows (
    instance_name TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    acct          TEXT NOT NULL,
    display_name  TEXT,
    url           TEXT,
    synced_at     TEXT NOT NULL,
    PRIMARY KEY (instance_name, account_id))
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


def db_get_active_rss_feeds(db_location, data_db_location=None):
    """Return live (enabled, not tombstoned) feeds for ingestion.

    Feed *identity + on/off* lives in ``db_location`` (the control DB,
    ``rss_feeds``); per-feed cache headers live in ``data_db_location`` (the
    data DB, ``rss_feed_health``, keyed by ``normalized_url``). When
    ``data_db_location`` is omitted both come from ``db_location`` (single
    physical file). The two are merged on ``normalized_url`` -- never an
    autoincrement id, which would not match across two separate DB files.

    Each row is ``(normalized_url, feed_url, etag, last_modified)``. Cache
    headers are returned as empty strings when NULL so callers can use them
    directly in HTTP header construction.
    """
    data_db_location = data_db_location or db_location
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            cur = db.execute(
                'SELECT feed_url, normalized_url '
                'FROM rss_feeds '
                'WHERE enabled = 1 AND deleted_at IS NULL '
                'ORDER BY normalized_url'
            )
            identity = [(row[0], row[1]) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load active rss_feeds: {exc}') from exc

    try:
        with sqlite3.connect(data_db_location) as db:
            _ensure_rss_feed_health_table(db)
            cur = db.execute(
                'SELECT normalized_url, etag, last_modified FROM rss_feed_health'
            )
            cache = {
                row[0]: (row[1] or '', row[2] or '')
                for row in cur.fetchall()
            }
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load rss_feed_health: {exc}') from exc

    return [
        (normalized, feed_url, *cache.get(normalized, ('', '')))
        for (feed_url, normalized) in identity
    ]


def db_update_rss_feed_after_fetch(results, db_location):
    """Persist per-feed fetch outcomes into ``rss_feed_health``.

    ``results`` is an iterable of dicts with keys:
        normalized_url (str), status (int), etag (str), last_modified (str),
        error (str|None), latest_entry_at (str|None, optional),
        site_link (str|None, optional).

    Health is ingest-owned and lives in the data DB, keyed by
    ``normalized_url``. Status 200 or 304 counts as success:
    ``consecutive_failures`` resets to 0, ``last_success_at`` is updated,
    cache headers are persisted. Any other status counts as failure:
    ``consecutive_failures`` is incremented and ``last_error`` recorded.

    Auto-disable was removed when feed on/off moved to the admin-owned
    control DB (the ingest job can no longer flip ``enabled``); failing feeds
    are surfaced for manual action via their health row instead.
    """
    results = list(results)
    if not results:
        return
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feed_health_table(db)
            for r in results:
                normalized = r['normalized_url']
                status = r.get('status') or 0
                success = status in (200, 304)
                etag = r.get('etag') or None
                last_mod = r.get('last_modified') or None
                latest_entry = r.get('latest_entry_at')
                site_link = r.get('site_link')
                error = r.get('error')

                if success:
                    db.execute(
                        'INSERT INTO rss_feed_health '
                        '(normalized_url, last_fetched_at, last_success_at, '
                        ' last_status, last_error, consecutive_failures, '
                        ' etag, last_modified, latest_entry_at, site_link) '
                        'VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?, ?) '
                        'ON CONFLICT(normalized_url) DO UPDATE SET '
                        '  last_fetched_at = excluded.last_fetched_at, '
                        '  last_success_at = excluded.last_success_at, '
                        '  last_status     = excluded.last_status, '
                        '  last_error      = NULL, '
                        '  consecutive_failures = 0, '
                        '  etag            = excluded.etag, '
                        '  last_modified   = excluded.last_modified, '
                        '  latest_entry_at = COALESCE(excluded.latest_entry_at, '
                        '                             rss_feed_health.latest_entry_at), '
                        '  site_link       = COALESCE(excluded.site_link, '
                        '                             rss_feed_health.site_link)',
                        (normalized, now, now, status, etag, last_mod,
                         latest_entry, site_link),
                    )
                else:
                    db.execute(
                        'INSERT INTO rss_feed_health '
                        '(normalized_url, last_fetched_at, last_status, '
                        ' last_error, consecutive_failures) '
                        'VALUES (?, ?, ?, ?, 1) '
                        'ON CONFLICT(normalized_url) DO UPDATE SET '
                        '  last_fetched_at = excluded.last_fetched_at, '
                        '  last_status     = excluded.last_status, '
                        '  last_error      = excluded.last_error, '
                        '  consecutive_failures = '
                        '      rss_feed_health.consecutive_failures + 1',
                        (normalized, now, status, error),
                    )
            db.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not update rss_feed_health: {exc}') from exc


def db_get_all_rss_feeds(db_location, data_db_location=None):
    """Return every feed row, including disabled and tombstoned.

    Merges feed *identity* (control DB ``rss_feeds``) with per-feed *health*
    (data DB ``rss_feed_health``) on ``normalized_url``. When
    ``data_db_location`` is omitted both come from ``db_location``. Each row
    is a dict carrying the full set of identity + health columns; feeds with
    no health row yet get NULL health values (and ``consecutive_failures``
    of 0). Used by the exporter and admin tooling.
    """
    data_db_location = data_db_location or db_location
    id_cols = ['feed_id', 'feed_url', 'normalized_url', 'enabled',
               'added_at', 'deleted_at']
    health_cols = ['last_fetched_at', 'last_success_at', 'last_status',
                   'last_error', 'consecutive_failures', 'etag',
                   'last_modified', 'latest_entry_at', 'site_link']
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_rss_feeds_table(db)
            cur = db.execute(
                f'SELECT {", ".join(id_cols)} FROM rss_feeds '
                'ORDER BY normalized_url'
            )
            feeds = [dict(zip(id_cols, row)) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load rss_feeds: {exc}') from exc

    try:
        with sqlite3.connect(data_db_location) as db:
            _ensure_rss_feed_health_table(db)
            cur = db.execute(
                f'SELECT normalized_url, {", ".join(health_cols)} '
                'FROM rss_feed_health'
            )
            health = {
                row[0]: dict(zip(health_cols, row[1:]))
                for row in cur.fetchall()
            }
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load rss_feed_health: {exc}') from exc

    for feed in feeds:
        h = health.get(feed['normalized_url'])
        for col in health_cols:
            if h is not None:
                feed[col] = h[col]
            else:
                feed[col] = 0 if col == 'consecutive_failures' else None
    return feeds



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


# --- subreddits (subscription list, supersedes config subreddits) ---------


def db_count_subreddits(db_location):
    """Return total row count of subreddits (including tombstoned)."""
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_subreddits_table(db)
            return db.execute('SELECT COUNT(*) FROM subreddits').fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not count subreddits: {exc}') from exc


def db_insert_subreddits(subreddits, db_location):
    """Bulk INSERT OR IGNORE subreddits.

    ``subreddits`` is an iterable of ``(name, normalized_name)`` tuples.
    Rows whose ``normalized_name`` collides with an existing row (including
    tombstoned rows where ``deleted_at`` is set) are skipped. Returns the
    number of rows actually inserted.
    """
    subreddits = list(subreddits)
    if not subreddits:
        return 0
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    rows = [(name, normalized, now) for (name, normalized) in subreddits]
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_subreddits_table(db)
            inserted = 0
            cur = db.cursor()
            for row in rows:
                cur.execute(
                    'INSERT OR IGNORE INTO subreddits '
                    '(name, normalized_name, added_at) VALUES (?, ?, ?)',
                    row,
                )
                inserted += cur.rowcount
            db.commit()
            return inserted
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not insert subreddits: {exc}') from exc


def db_get_active_subreddits(db_location):
    """Return live (enabled, not tombstoned) subreddits for ingestion.

    Each row is ``(subreddit_id, name, normalized_name)``.
    """
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_subreddits_table(db)
            cur = db.execute(
                'SELECT subreddit_id, name, normalized_name '
                'FROM subreddits '
                'WHERE enabled = 1 AND deleted_at IS NULL '
                'ORDER BY normalized_name'
            )
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load active subreddits: {exc}') from exc


def db_get_all_subreddits(db_location):
    """Return every subreddit row, including disabled and tombstoned.

    Used by any future admin tooling and exporters. Each row is a dict with
    all canonical columns from ``subreddits``.
    """
    cols = ['subreddit_id', 'name', 'normalized_name', 'enabled',
            'added_at', 'deleted_at']
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_subreddits_table(db)
            cur = db.execute(
                f'SELECT {", ".join(cols)} FROM subreddits '
                'ORDER BY normalized_name'
            )
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load subreddits: {exc}') from exc


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


# --- follows snapshots (read-only mirror of remote social graph) -----------


def db_replace_bluesky_follows(follows, db_location):
    """Replace the entire Bluesky follows snapshot in one transaction.

    ``follows`` is an iterable of ``(did, handle, display_name)`` tuples.
    The table is fully rewritten so removed follows disappear. Returns the
    number of rows written.
    """
    follows = list(follows)
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    rows = [
        (did, handle, display_name or None, now)
        for (did, handle, display_name) in follows
        if did and handle
    ]
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_bluesky_follows_table(db)
            db.execute('DELETE FROM bluesky_follows')
            if rows:
                db.executemany(
                    'INSERT OR REPLACE INTO bluesky_follows '
                    '(did, handle, display_name, synced_at) VALUES (?, ?, ?, ?)',
                    rows,
                )
            db.commit()
            return len(rows)
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not persist bluesky follows: {exc}') from exc


def db_get_bluesky_follows(db_location):
    """Return every Bluesky follow as a dict, ordered by handle."""
    cols = ['did', 'handle', 'display_name', 'synced_at']
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_bluesky_follows_table(db)
            cur = db.execute(
                f'SELECT {", ".join(cols)} FROM bluesky_follows '
                'ORDER BY LOWER(handle)'
            )
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load bluesky follows: {exc}') from exc


def db_replace_mastodon_follows(instance_name, follows, db_location):
    """Replace the follows snapshot for one Mastodon instance.

    ``follows`` is an iterable of ``(account_id, acct, display_name, url)``
    tuples. Only rows for ``instance_name`` are rewritten, so other instances
    are left untouched. Returns the number of rows written.
    """
    follows = list(follows)
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    rows = [
        (instance_name, account_id, acct, display_name or None, url or None, now)
        for (account_id, acct, display_name, url) in follows
        if account_id and acct
    ]
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_mastodon_follows_table(db)
            db.execute('DELETE FROM mastodon_follows WHERE instance_name = ?', [instance_name])
            if rows:
                db.executemany(
                    'INSERT OR REPLACE INTO mastodon_follows '
                    '(instance_name, account_id, acct, display_name, url, synced_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    rows,
                )
            db.commit()
            return len(rows)
    except sqlite3.Error as exc:
        raise RuntimeError(
            f'Could not persist mastodon follows for {instance_name}: {exc}'
        ) from exc


def db_get_mastodon_follows(db_location):
    """Return every Mastodon follow as a dict, ordered by instance then acct."""
    cols = ['instance_name', 'account_id', 'acct', 'display_name', 'url', 'synced_at']
    try:
        with sqlite3.connect(db_location) as db:
            _ensure_mastodon_follows_table(db)
            cur = db.execute(
                f'SELECT {", ".join(cols)} FROM mastodon_follows '
                'ORDER BY instance_name, LOWER(acct)'
            )
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load mastodon follows: {exc}') from exc

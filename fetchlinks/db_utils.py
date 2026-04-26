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

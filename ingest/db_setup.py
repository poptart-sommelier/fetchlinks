import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def db_create(db_path):
    db_path = Path(db_path)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(db_path)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f'Failed to create or open database at {db_path}') from exc


def table_posts_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS posts (
    idx INTEGER PRIMARY KEY,
    source TEXT,
    source_type TEXT,
    author TEXT,
    description TEXT,
    direct_link TEXT,
    date_created TEXT,
    unique_id_string TEXT UNIQUE NOT NULL )
    """)
        # Idempotent in-place upgrade for DBs created before source_type
        # existed. Matches the pattern used for rss_feeds.site_link.
        existing_columns = {row[1] for row in conn.execute('PRAGMA table_info(posts)').fetchall()}
        if 'source_type' not in existing_columns:
            conn.execute('ALTER TABLE posts ADD COLUMN source_type TEXT')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_unique_id   ON posts(unique_id_string)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_source      ON posts(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_date        ON posts(date_created)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_source_type ON posts(source_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_author      ON posts(author)")
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure posts table') from exc


def table_post_urls_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS post_urls (
    idx INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(idx) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    unshortened_url TEXT,
    UNIQUE (post_id, position),
    UNIQUE (post_id, url_hash) )
    """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_urls_post     ON post_urls(post_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_urls_url_hash ON post_urls(url_hash)")
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure post_urls table') from exc


def table_bluesky_state_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS bluesky_state (
    idx INTEGER PRIMARY KEY,
    cursor TEXT,
    time_created TEXT)
    """)
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure bluesky_state table') from exc


def table_rss_feed_state_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS rss_feed_state (
    feed_url TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    last_status INTEGER,
    last_fetched TEXT)
    """)
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure rss_feed_state table') from exc


def table_rss_feeds_configure(conn):
    """Create the per-feed catalog + health table.

    Holds both subscription state (enabled/deleted) and per-feed health/cache
    state (etag, last_modified, last_status, consecutive_failures). Supersedes
    ``rss_feed_state``; ``migrate_rss_feed_state_into_rss_feeds`` copies any
    legacy rows over the first time this runs against an existing DB.
    """
    try:
        conn.execute("""
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
    latest_entry_at      TEXT,
    site_link            TEXT)
    """)
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_rss_feeds_live '
            'ON rss_feeds(enabled, deleted_at)'
        )
        # In-place upgrade for DBs created before site_link existed.
        existing_columns = {
            row[1]
            for row in conn.execute('PRAGMA table_info(rss_feeds)').fetchall()
        }
        if 'site_link' not in existing_columns:
            conn.execute('ALTER TABLE rss_feeds ADD COLUMN site_link TEXT')
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure rss_feeds table') from exc


def _normalize_feed_url_for_migration(url):
    """Local copy of the normalizer to avoid a circular import on db_setup.

    Must match ``rss_feed_import.normalize_feed_url`` semantics: lowercase
    scheme + host, drop fragment, keep path/query, drop default-empty path.
    """
    from urllib.parse import urldefrag, urlsplit, urlunsplit
    cleaned, _fragment = urldefrag((url or '').strip())
    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or '/'
    return urlunsplit((scheme, netloc, path, parts.query, ''))


def migrate_rss_feed_state_into_rss_feeds(conn):
    """One-time copy of rss_feed_state rows into rss_feeds.

    Runs only if rss_feed_state exists AND rss_feeds is empty. Preserves
    etag/last_modified/last_status/last_fetched. The legacy table is left in
    place (not dropped) so a downgrade is still possible; later cleanup is
    safe once we're confident no one needs it.
    """
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='rss_feed_state'"
    )
    if cur.fetchone() is None:
        return 0

    existing = conn.execute('SELECT COUNT(*) FROM rss_feeds').fetchone()[0]
    if existing:
        return 0

    rows = conn.execute(
        'SELECT feed_url, etag, last_modified, last_status, last_fetched '
        'FROM rss_feed_state'
    ).fetchall()
    if not rows:
        return 0

    from datetime import UTC, datetime
    now = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')

    migrated = 0
    seen_normalized = set()
    cur = conn.cursor()
    for feed_url, etag, last_modified, last_status, last_fetched in rows:
        if not feed_url:
            continue
        normalized = _normalize_feed_url_for_migration(feed_url)
        if normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        cur.execute(
            'INSERT OR IGNORE INTO rss_feeds '
            '(feed_url, normalized_url, enabled, added_at, '
            ' last_fetched_at, last_status, etag, last_modified) '
            'VALUES (?, ?, 1, ?, ?, ?, ?, ?)',
            (feed_url, normalized, now,
             last_fetched or None, last_status,
             etag or None, last_modified or None),
        )
        migrated += cur.rowcount

    return migrated


def table_reddit_state_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS reddit_state (
    subreddit TEXT PRIMARY KEY,
    last_seen_fullname TEXT,
    time_created TEXT)
    """)
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure reddit_state table') from exc


def table_subreddits_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS subreddits (
    subreddit_id    INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    enabled         INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT NOT NULL,
    deleted_at      TEXT)
    """)
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_subreddits_live '
            'ON subreddits(enabled, deleted_at)'
        )
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure subreddits table') from exc


def table_mastodon_state_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS mastodon_state (
    source_name TEXT PRIMARY KEY,
    instance_url TEXT NOT NULL,
    last_seen_id TEXT,
    time_created TEXT)
    """)
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure mastodon_state table') from exc


def db_initial_setup(db_path):
    db_path = Path(db_path)
    logger.info('Creating or validating %s', db_path)
    conn = db_create(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    table_posts_configure(conn)
    table_post_urls_configure(conn)
    table_bluesky_state_configure(conn)
    table_rss_feed_state_configure(conn)
    table_rss_feeds_configure(conn)
    migrate_rss_feed_state_into_rss_feeds(conn)
    table_reddit_state_configure(conn)
    table_subreddits_configure(conn)
    table_mastodon_state_configure(conn)
    conn.commit()
    conn.close()
    logger.info('Successfully created DB')


if __name__ == '__main__':
    db_initial_setup('db/fetchlinks.db')

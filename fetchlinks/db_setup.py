import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def db_create(db_location, db_name):
    db_dir = Path(db_location)
    db_path = db_dir / db_name

    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(db_path)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f'Failed to create or open database at {db_path}') from exc


def table_posts_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS posts (
    idx INTEGER PRIMARY KEY,
    source TEXT,
    author TEXT,
    description TEXT,
    direct_link TEXT,
    date_created TEXT,
    unique_id_string TEXT UNIQUE NOT NULL )
    """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_unique_id ON posts(unique_id_string)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_source    ON posts(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_date      ON posts(date_created)")
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


def db_initial_setup(db_location, db_name):
    db_path = Path(db_location) / db_name
    logger.info('Creating or validating %s', db_path)
    conn = db_create(db_location, db_name)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    table_posts_configure(conn)
    table_post_urls_configure(conn)
    table_bluesky_state_configure(conn)
    table_rss_feed_state_configure(conn)
    conn.commit()
    conn.close()
    logger.info('Successfully created DB')


if __name__ == '__main__':
    db_initial_setup('db/', 'fetchlinks.db')

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
    date_created INTEGER,
    unique_id_string TEXT UNIQUE,
    url_1 TEXT,
    url_2 TEXT,
    url_3 TEXT,
    url_4 TEXT,
    url_5 TEXT,
    url_6 TEXT,
    urls_missing INTEGER NOT NULL DEFAULT 0 )
    """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unique_id_string ON posts(unique_id_string)")
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure posts table') from exc


def table_urls_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE IF NOT EXISTS urls (
    idx INTEGER PRIMARY KEY,
    url TEXT,
    unique_id TEXT UNIQUE )
    """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unique_id ON urls(unique_id)")
    except sqlite3.OperationalError as exc:
        raise RuntimeError('Failed to configure urls table') from exc


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


def db_initial_setup(db_location, db_name):
    db_path = Path(db_location) / db_name
    logger.info(f'Creating or validating {db_path}')
    conn = db_create(db_location, db_name)
    table_posts_configure(conn)
    table_urls_configure(conn)
    table_bluesky_state_configure(conn)
    conn.commit()
    conn.close()
    logger.info('Successfully created DB')


if __name__ == '__main__':
    db_initial_setup('db/', 'fetchlinks.db')

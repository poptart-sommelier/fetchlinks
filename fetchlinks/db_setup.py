import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def db_create(db_location, db_name):
    if Path(db_location + db_name).is_file():
        logger.error('DB file already exists. Back it up before overwriting it.')
        exit(1)
    else:
        try:
            Path(db_location).mkdir(parents=True, exist_ok=False)
            conn = sqlite3.connect(db_location + db_name)
        except sqlite3.OperationalError as e:
            logger.error(e)
            exit(1)
    return conn


def table_posts_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE posts (
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
        conn.execute("CREATE INDEX idx_unique_id_string ON posts(unique_id_string)")
    except sqlite3.OperationalError as e:
        logger.error(e)


def table_twitter_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE twitter (
    idx INTEGER PRIMARY KEY,
    last_accessed_id TEXT,
    time_created INTEGER )
    """)
    except sqlite3.OperationalError as e:
        logger.error(e)


def table_urls_configure(conn):
    try:
        conn.execute("""
    CREATE TABLE urls (
    idx INTEGER PRIMARY KEY,
    url TEXT,
    unique_id TEXT UNIQUE )
    """)
        conn.execute("CREATE INDEX idx_unique_id ON urls(unique_id)")
    except sqlite3.OperationalError as e:
        logger.error(e)


def db_initial_setup(db_location, db_name):
    logger.info(f'Creating {db_location+db_name}')
    conn = db_create(db_location, db_name)
    table_posts_configure(conn)
    table_twitter_configure(conn)
    table_urls_configure(conn)
    logger.info('Successfully created DB')


if __name__ == '__main__':
    db_initial_setup('db/', 'fetchlinks.db')

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
    db_command_posts = """INSERT OR IGNORE INTO posts (source, author, description, direct_link, 
                        date_created, unique_id_string, url_1, url_2, url_3, url_4, url_5, url_6, urls_missing) 
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    if not fetched_data:
        return 0

    post_list = [post.get_db_friendly_list() for post in fetched_data]

    try:
        with sqlite3.connect(db_location) as db:
            cur = db.cursor()
            cur.executemany(db_command_posts, post_list)
            db.commit()
            return cur.rowcount
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not load posts into posts table: {exc}') from exc


def db_get_bluesky_cursor(db_location):
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
    db_command = """INSERT INTO bluesky_state (cursor, time_created) values (?, ?)"""

    try:
        with sqlite3.connect(db_location) as db:
            _ensure_bluesky_state_table(db)
            cur = db.cursor()
            cur.execute(db_command, [cursor or '', datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')])
            db.commit()
    except sqlite3.Error as exc:
        raise RuntimeError(f'Could not persist bluesky cursor: {exc}') from exc

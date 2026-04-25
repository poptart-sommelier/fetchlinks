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
    lookup_post_sql = 'SELECT idx FROM posts WHERE unique_id_string = ?'

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
                if post_id is None:
                    cur.execute(lookup_post_sql, (post.unique_id_string,))
                    row = cur.fetchone()
                    if not row:
                        continue
                    post_id = row[0]
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

import sqlite3
import logging

logger = logging.getLogger(__name__)


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

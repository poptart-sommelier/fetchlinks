import sqlite3
import logging

logger = logging.getLogger(__name__)


def db_insert(fetched_data, db_location):
    db_command_posts = """INSERT OR IGNORE INTO posts (source, author, description, direct_link, 
                        date_created, unique_id_string, url_1, url_2, url_3, url_4, url_5, url_6, urls_missing) 
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    try:
        db = sqlite3.connect(db_location)
    except sqlite3.Error as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    post_list = []

    for post in fetched_data:
        post_list.append(post.get_db_friendly_list())

    try:
        cur.executemany(db_command_posts, post_list)
        db.commit()
    except sqlite3.Error as e:
        logger.error('Could not load posts into posts. DB error: {0}'.format(e))
        db.rollback()

import datetime
import sqlite3
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def db_set_last_tweet_id(last_accessed_id, db_location):
    db_command = """INSERT INTO twitter (last_accessed_id, time_created) values (?, ?)"""

    try:
        db = sqlite3.connect(db_location)
    except Exception as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    try:
        cur.execute(db_command, [last_accessed_id, datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')])
        db.commit()
    except sqlite3.Error as e:
        logger.error('Could not set last accessed id. DB error: {0}'.format(e))
        db.rollback()
        exit(1)


def db_get_last_tweet_id(db_location):
    db_command = """SELECT last_accessed_id FROM twitter ORDER BY idx DESC LIMIT 1"""

    try:
        db = sqlite3.connect(db_location)
    except sqlite3.Error as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    try:
        cur.execute(db_command)
        result = cur.fetchall()
        db.close()
        if len(result) < 1:
            return 1
        else:
            return int(result[0][0])

    except sqlite3.Error as e:
        logger.error('Could not get last accessed id. DB error: {0}'.format(e))
        return 1


def db_insert(fetched_data, db_location):
    db_command_posts = """INSERT OR IGNORE INTO posts (source, author, description, direct_link, 
                        date_created, unique_id_string, url_1, url_2, url_3, url_4, url_5, url_6, urls_missing) 
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    db_command_urls = """INSERT OR IGNORE INTO urls (url, unique_id) values (?, ?)"""

    try:
        db = sqlite3.connect(db_location)
    except sqlite3.Error as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    post_list = []
    url_list = []

    for post in fetched_data:
        # Build URL list, truncate long urls
        post.prep_for_db()

        # TODO: THE LIST BEING APPENDED HERE SHOULD BE RETURNED BY A CLASS METHOD
        #  post.get_list_of_fields()
        post_list.append([post.source,
                          post.author,
                          post.description,
                          post.direct_link,
                          post.date_created,
                          post.unique_id_string,
                          post.url_1,
                          post.url_2,
                          post.url_3,
                          post.url_4,
                          post.url_5,
                          post.url_6,
                          post.urls_missing
                          ])

        # TODO: THIS IS REDUNDANT
        for url in post.urls:
            if url['unshort_url'] is None:
                url_list.append([url['url'],
                                 url['unique_id']])
            else:
                url_list.append([url['unshort_url'],
                                 url['unshort_unique_id']])

    try:
        cur.executemany(db_command_posts, post_list)
        db.commit()
    except sqlite3.Error as e:
        logger.error('Could not load posts into posts. DB error: {0}'.format(e))
        db.rollback()

    try:
        cur.executemany(db_command_urls, url_list)
        db.commit()
    except sqlite3.Error as e:
        logger.error('Could not load urls into urls. DB error: {0}'.format(e))
        db.rollback()

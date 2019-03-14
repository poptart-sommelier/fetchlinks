import MySQLdb
import logging
import structure_data

logger = logging.getLogger(__name__)


def db_set_last_tweet_id(last_accessed_id):
    db_command = """INSERT INTO fetchlinks.twitter (last_accessed_id) values (%s)"""

    try:
        db = MySQLdb.connect(host="127.0.0.1", port=3306, user="root", passwd="thepassword", db="fetchlinks",
                             use_unicode=True, charset="utf8mb4")
    except Exception as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    try:
        cur.execute(db_command, [last_accessed_id])
        db.commit()
    except MySQLdb.Error as e:
        logger.error('Could not set last accessed id. DB error: {0}'.format(e))
        db.rollback()


def db_get_last_tweet_id():
    db_command = """SELECT last_accessed_id FROM fetchlinks.twitter ORDER BY idx LIMIT 1"""

    try:
        db = MySQLdb.connect(host="127.0.0.1", port=3306, user="root", passwd="thepassword", db="fetchlinks",
                             use_unicode=True, charset="utf8mb4")
    except Exception as e:
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

    except MySQLdb.Error as e:
        logger.error('Could not set last accessed id. DB error: {0}'.format(e))
        return 1


def db_insert(fetched_data):
    db_command_posts = """INSERT IGNORE INTO fetchlinks.posts (source, author, description, direct_link, 
                        date_created, unique_id_string) values (%s, %s, %s, %s, %s, %s)"""
    db_command_urls = """INSERT IGNORE INTO fetchlinks.urls (url, unique_id) values (%s, %s)"""

    try:
        db = MySQLdb.connect(host="127.0.0.1", port=3306, user="root", passwd="thepassword", db="fetchlinks",
                             use_unicode=True, charset="utf8mb4")
    except Exception as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    post_list = []
    for line in fetched_data:
        post_list.append([line.data_structure['source'],
                          line.data_structure['author'],
                          line.data_structure['description'],
                          line.data_structure['direct_link'],
                          line.data_structure['date_created'],
                          line.data_structure['unique_id_string']])

    url_list = []
    for line in fetched_data:
        for url in line.data_structure['urls']:
            if url['unshort_url'] is None:
                url_list.append([url['url'],
                                 url['unique_id']])
            else:
                url_list.append([url['unshort_url'],
                                 url['unshort_unique_id']])

    try:
        cur.executemany(db_command_posts, post_list)
        db.commit()
    except MySQLdb.Error as e:
        logger.error('Could not load posts into fetchlinks.posts. DB error: {0}'.format(e))
        db.rollback()

    try:
        cur.executemany(db_command_urls, url_list)
        db.commit()
    except MySQLdb.Error as e:
        logger.error('Could not load urls into fetchlinks.urls. DB error: {0}'.format(e))
        db.rollback()

import MySQLdb
import logging
import structure_data

logger = logging.getLogger(__name__)


def db_insert(entry_list):
    db_command = """INSERT IGNORE INTO fetchlinks.links (source, author, description, direct_link, urls, date_created, 
                    unique_id) values (%s, %s, %s, %s, %s, %s, %s)"""

    try:
        db = MySQLdb.connect(host="127.0.0.1", port=33600, user="root", passwd="thepassword", db="fetchlinks",
                             use_unicode=True, charset="utf8mb4")
    except Exception as e:
        logger.error("Could not connect to database!")
        logger.error(e)
        exit(1)

    cur = db.cursor()

    try:
        cur.executemany(db_command, entry_list)
        db.commit()
    except MySQLdb.Error as e:
        logger.error('Could not load tweets. DB error: {0}'.format(e))
        db.rollback()


def dict_to_row(fetched_data):
    list_of_rows_for_db = []

    for entry in fetched_data:
        url_list = []

        for url in entry.data_structure['urls']:
            if not url['unshort_url']:
                url_list.append(url['unshort_url'])
            else:
                url_list.append(url['url'])

        list_of_rows_for_db.append([entry.data_structure['source'],
                                    entry.data_structure['author'],
                                    entry.data_structure['description'],
                                    entry.data_structure['direct_link'],
                                    '|'.join([url['unshort_url'] if url['unshort_url'] else url['url']
                                              for url in entry.data_structure['urls']]),
                                    entry.data_structure['date_created'],
                                    entry.data_structure['unique_id']])

    return list_of_rows_for_db


def main(fetched_data):
    # USE THIS FOR PROD
    if fetched_data:
        db_rows = dict_to_row(fetched_data)
        db_insert(db_rows)
    else:
        logger.error('No data to load!')

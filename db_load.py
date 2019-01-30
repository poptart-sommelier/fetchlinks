import json
import MySQLdb
import glob
import os
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# TODO: Create script to generate DB, table, permissions, etc.
# TODO: PROPERLY IMPLEMENT FINDING NEW FILES AND LOADING THEM.
# RIGHT NOW THIS ONLY WORKS WITH HARDCODED jsonfile

JSON_FILE_LOCATION = './JSON/'
JSON_WILDCARD = '*.json'
JSON_BACKUP_DIR = './OLD_JSON/'
LOG_LOCATION = './LOGS/'
LOG_NAME = 'db_load.log'
# jsonfile = '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/json_output_2018-07-26_221746.json'

logger = logging.getLogger("LOG")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%m/%d/%Y %I:%M:%S %p')
handler = RotatingFileHandler(LOG_LOCATION + LOG_NAME, maxBytes=1000000, backupCount=5)
handler.setFormatter(formatter)
logger.addHandler(handler)


def read_json_file(jsonfile):
    try:
        with open(jsonfile, 'r') as f:
            all_json_data = json.load(f)
        return all_json_data
    except (IOError, OSError) as e:
        logger.error('Cannot read {0}. System error: {1}'.format(jsonfile, e))
        return None


def get_new_files(JSON_FILE_LOCATION):
    newfiles = glob.glob(JSON_FILE_LOCATION + JSON_WILDCARD)
    return newfiles


def move_file(jsonfile, JSON_BACKUP_DIR):
    file_loc = Path(jsonfile)
    file_name = file_loc.name
    new_file_loc = JSON_BACKUP_DIR + file_name
    try:
        os.rename(jsonfile, new_file_loc)
        logger.info('{0} moved to {1}'.format(jsonfile, new_file_loc))
        return True
    except Exception as e:
        logger.error('Could not move {0}. System error: {1}'.format(jsonfile, e))
        return False


def db_insert(tweetlist):
    db_command = """INSERT INTO tweets (tweet_direct_link, urls, full_text, id, user, screen_name, unshort_urls, tweet_type, date_created) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""

    db = MySQLdb.connect(host="127.0.0.1", port=33600, user="rich", passwd="testpassword", db="twitter", use_unicode=True, charset="utf8mb4")

    cur = db.cursor()

    try:
        cur.executemany(db_command, tweetlist)
        db.commit()
    except Exception as e:
        logger.error('Could not load tweets. DB error: {0}'.format(e))
        db.rollback()


def prep_json_for_db(json_data):

    list_of_rows_for_db = []

    for j in json_data:
        url_list = []
        unshorturl_list = []

        for url in j['urls']:
            if url['url'] is not None:
                url_list.append(url)
                if url['unshort_url'] is not None:
                    unshorturl_list.append(url['unshort_url'])
                else:
                    unshorturl_list.append(url['url'])

        list_of_rows_for_db.append((j['tweet_direct_link'], json.dumps(url_list), j['full_text'], j['id'], j['user'],
                                    j['screen_name'], '|'.join(unshorturl_list), j['tweet_type'], j['date_created']))

    return list_of_rows_for_db


def start():
    # USE THIS FOR PROD
    for file in get_new_files(JSON_FILE_LOCATION):
        json_from_file = read_json_file(file)
        if json_from_file:
            db_rows = prep_json_for_db(json_from_file)
            db_insert(db_rows)
            move_file(file, JSON_BACKUP_DIR)
        else:
            logger.error('Could not read JSON file')
            continue

# USE THIS FOR TESTING
# json_from_file = read_json_file(jsonfile)
# db_rows = prep_json_for_db(json_from_file)
# db_insert(db_rows)

start()



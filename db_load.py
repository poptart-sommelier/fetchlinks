import json
import MySQLdb
import glob
import os
from pathlib import Path

JSON_FILE_LOCATION '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/'
JSON_BACKUP_DIR '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/OLD/'
JSONFILE = '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/json_output_2018-07-08_105145.json'


def read_json_file(JSONFILE):
	with open (JSONFILE, 'r') as f:
		all_json_data = json.load(f)
	return all_json_data


def get_new_files(JSON_FILE_LOCATION):
	newfiles = glob.glob(JSON_FILE_LOCATION)
	return newfiles


def move_file(JSONFILE, JSON_BACKUP_DIR):
	file_loc = Path(JSONFILE)
	file_name = file_loc.name
	new_file_loc = JSON_BACKUP_DIR + file_name
	try:
		os.rename(JSONFILE, new_file_loc)
		return True, '%s moved to %s'.format(JSONFILE, new_file_loc)
	except Exception as e:
		return False, e


def db_insert(tweetlist):
	db_command = """INSERT INTO tweets (tweet_direct_link, urls, full_text, id, user, screen_name, unshort_urls, tweet_type) values (%s, %s, %s, %s, %s, %s, %s, %s)"""

	db = MySQLdb.connect(host="127.0.0.1", port=33600, user="rich", passwd="testpassword", db="twitter", use_unicode=True, charset="utf8mb4")

	cur = db.cursor()

	try:
		cur.executemany(db_command, tweetlist)
		db.commit()
	except Exception as e:
		print(e)
		db.rollback()


def prep_json_for_db(json_data):

	list_of_rows_for_db = []

	for j in json_data:
		url_list = []
		unshorturl_list = []

		for url in j['urls']:
			if url is not None:
				url_list.append(url)
		for unshorturl in j['unshort_urls']:
			if unshorturl is not None:
				unshorturl_list.append(unshorturl)

		list_of_rows_for_db.append(( j['tweet_direct_link'], '|'.join(url_list), j['full_text'], j['id'], j['user'],
				j['screen_name'], '|'.join(unshorturl_list), j['tweet_type'] ))

	return list_of_rows_for_db


def start():
	db_rows = prep_json_for_db(read_json_file())
	db_insert(db_rows)

start()


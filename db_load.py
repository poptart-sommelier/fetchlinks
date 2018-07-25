import json
import MySQLdb
import glob
import os
from pathlib import Path

# TODO: Create script to generate DB, table, permissions, etc.
# TODO: PROPERLY IMPLEMENT FINDING NEW FILES AND LOADING THEM.
# RIGHT NOW THIS ONLY WORKS WITH HARDCODED JSONFILE

JSON_FILE_LOCATION = '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/'
JSON_BACKUP_DIR = '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/OLD/'
JSONFILE = '/home/rich/Documents/SCRIPTS/PROJECTS/TWITTERLINKS/json_output_2018-07-24_112519.json'


def read_json_file(JSONFILE):
	with open(JSONFILE, 'r') as f:
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
			if url['url'] is not None:
				url_list.append(json.dumps(url))
				if url['unshort_url'] is not None:
					unshorturl_list.append(url['unshort_url'])
				else:
					unshorturl_list.append(url['url'])

		list_of_rows_for_db.append((j['tweet_direct_link'], '|'.join(url_list), j['full_text'], j['id'], j['user'],
				j['screen_name'], '|'.join(unshorturl_list), j['tweet_type']))

	return list_of_rows_for_db


def start():
	# USE THIS FOR PROD
	# for file in get_new_files(JSON_FILE_LOCATION):
	# 	json_from_file = read_json_file(file)
	# 	db_rows = prep_json_for_db(json_from_file)
	# 	db_insert(db_rows)

	# USE THIS FOR TESTING
	json_from_file = read_json_file(JSONFILE)
	db_rows = prep_json_for_db(json_from_file)
	db_insert(db_rows)

start()



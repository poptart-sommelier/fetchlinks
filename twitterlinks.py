#!/usr/bin/python3.5

# references:
# https://dev.twitter.com/rest/public/timelines
# http://docs.tweepy.org/en/v3.5.0/api.html

import tweepy
import unshorten_links
import os
import json
import datetime
import logging

# TODO: ADD LOGGING, OUTPUT THIS AS LOG ENTRY WHEN IN DEBUG MODE
# TODO: THREADING FOR UNSHORTEN_URL
# TODO: flask megatutorial, and start using flask + DB in AWS.
# TODO: FUNCTION (PPRINT) TO PRINT EVERYTHING NICELY
# TODO: ROTATE LOGS

logging.basicConfig(filename='twitterlinks.log', level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')

LAST_ACCESSED_FILE = "./LAST_ACCESSED.txt"

NUMBER_OF_ITEMS = 200

CRED_PATH = '/home/rich/.creds/twitter_api.json'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

CONSUMER_KEY = creds['twitter_creds'][0]['CONSUMER_KEY']
CONSUMER_SECRET = creds['twitter_creds'][0]['CONSUMER_SECRET']
ACCESS_TOKEN = creds['twitter_creds'][0]['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = creds['twitter_creds'][0]['ACCESS_TOKEN_SECRET']

JSON_OUTPUT_DIR = './'

def twitter_auth():
	auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
	auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
	_api = tweepy.API(auth)
	return _api

def last_accessed_read():
	try:
		with open(LAST_ACCESSED_FILE, "r") as f:
			last_read = f.read()
			if last_read.isdigit():
				return last_read
			else:
				return 1
	except IOError:
		logging.info('Cannot locate ./LAST_ACCESSED.txt. Creating a blank one now.')
		print("Cannot locate ./LAST_ACCESSED.txt. Creating a blank one now.")
		with open(LAST_ACCESSED_FILE, "w") as f:

			last_accessed_write("1")
		return 1

def last_accessed_write(last_write):
	try:
		with open(LAST_ACCESSED_FILE, "w") as f:
			f.write(last_write)
	except IOError:
		logging.info('Error writing to file ./LAST_ACCESSED.txt')
		print("Error writing to file ./LAST_ACCESSED.txt")

def write_json_output(_all_tweetdeets):

	json_output = JSON_OUTPUT_DIR + "json_output_" + str(datetime.datetime.now().strftime("%Y-%m-%d_%I%M%S")) + ".json"

	with open(json_output, 'w') as j:
		json.dump(_all_tweetdeets, j)

def get_status_info(statuses):

	_all_tweetdeets = []

	for status in statuses:

		tweetdeets = {
			'status_id': '',
			'user': '',
			'name': '',
			'text': '',
			'tco_expandedurl': [],
			'unshortened_url': [],
			'rt_url': [],
			'rt_user': '',
			'rt_name': '',
			'rt_text': '',
			'is_retweet': '',
			'rt_tco_expandedurl': '',
			'created_at': ''
		}

		try:
			if (status.entities['urls']) or (status.retweeted_status.entities['urls']):
				tweetdeets['status_id'] = status.id
				tweetdeets['user'] = status.user.screen_name
				tweetdeets['name'] = status.user.name
				tweetdeets['text'] = status.full_text
				tweetdeets['created_at'] = str(status.created_at)

				try:
					for url in status.entities['urls']:
						tweetdeets['tco_expandedurl'].append(url['expanded_url'])
						#tweetdeets['unshortened_url'].append(unshorten_links.unshorten(url['expanded_url']))
				except:
					pass
				# IS THIS A RETWEET?
				if not hasattr(status, 'retweeted_status'):

					tweetdeets['is_retweet'] = False

				try:
					if status.retweeted_status.entities['urls']:
						tweetdeets['rt_user'] = status.retweeted_status.user.screen_name
						tweetdeets['rt_name'] = status.retweeted_status.user.name
						tweetdeets['rt_text'] = status.retweeted_status.full_text

						for rturl in status.retweeted_status.entities['urls']:
							#tweetdeets['rt_url'].append(unshorten_links.unshorten(rturl['expanded_url']))
							tweetdeets['rt_tco_expandedurl'].append(rturl['expanded_url'])
				except:
					pass
		except:
			pass

		# IF STATUS_ID IS EMPTY, WE DID NOT GET A RESULT, DON'T RETURN ANYTHING
		if tweetdeets['status_id']:
			_all_tweetdeets.append(tweetdeets)

	return(_all_tweetdeets)


api = twitter_auth()

set_cursor = last_accessed_read()

# WHILE NOT tweepy.RateLimitError, reinit statuses[], grab another 200 (set cursor to newest in last batch), unshorten everything, then loop.
all_tweetdeets = []

while True:
	try:
		statuses = []
		statuses = api.home_timeline(count=NUMBER_OF_ITEMS, since_id=set_cursor, include_entites=True, include_rts=True, tweet_mode='extended')
		all_tweetdeets.extend(get_status_info(statuses))
		if statuses:
			logging.info('Retrieved %s tweets', len(statuses))
		# INSERT BREAK HERE TO ONLY RUN ONCE
		break
	except tweepy.TweepError as e:
		logging.info(e)
		print(e)
		break

# for x in all_tweetdeets:
	# print(x)
	# print("\n*************************************************")
	# print(x['status_id'])
	# print("User: @" + x['user'] + " | Name: " + x['name'])
	# print(x['text'])
	# # print("EXPANDING: " + url['expanded_url'])
	# print("URL: " + x['unshortened_url'])
	# #print("*************************************************\n")
	# if x['is_retweet']:
	# 	print("RETWEETED:")
	# 	print("User: @" + x['rt_user'] + " | Name: " + x['rt_name'])
	# 	print(x['rt_text'])
	# 	print("RT URL: " + x['rt_url'])
	# 	print("*************************************************\n")##


if set_cursor:
	logging.info('Cursor was previously: %s', str(set_cursor))
	print("Cursor was previously: " + str(set_cursor))

if all_tweetdeets:
	logging.info('Cursor now set to: %s', str(all_tweetdeets[0]['status_id']))
	print("Cursor now set to: " + str(all_tweetdeets[0]['status_id']))
	last_accessed_write(str(all_tweetdeets[0]['status_id']))
	write_json_output(all_tweetdeets)
else:
	logging.info('No new tweets')
	print("No new tweets")

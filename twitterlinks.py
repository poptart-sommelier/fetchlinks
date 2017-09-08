#!/usr/bin/python3.5

# references:
# https://dev.twitter.com/rest/public/timelines
# http://docs.tweepy.org/en/v3.5.0/api.html
#

import tweepy
import unshorten_links
import os
import json
from queue import Queue
import threading

LAST_ACCESSED_FILE = "./LAST_ACCESSED.txt"

NUMBER_OF_ITEMS = 250

CRED_PATH = '/home/rich/.creds/twitter_api.json'

json_data=open(CRED_PATH).read()
creds = json.loads(json_data)

CONSUMER_KEY = creds['twitter_creds'][0]['CONSUMER_KEY']
CONSUMER_SECRET = creds['twitter_creds'][0]['CONSUMER_SECRET']
ACCESS_TOKEN = creds['twitter_creds'][0]['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = creds['twitter_creds'][0]['ACCESS_TOKEN_SECRET']

# Variables for Threading/Queuing
fetch_threads = 5
the_queue = Queue()

def twitter_auth():
	auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
	auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

	_api = tweepy.API(auth)
	return _api

def last_accessed_read():
	try:
		with open(LAST_ACCESSED_FILE, "r") as f:
			last_read = f.read()
			return last_read
	except IOError:
		print("Cannot locate ./LAST_ACCESSED.txt. Creating a blank one now.")
		with open(LAST_ACCESSED_FILE, "w") as f:
			pass

def last_accessed_write(last_write):
	try:
		with open(LAST_ACCESSED_FILE, "w") as f:
			f.write(last_write)
	except IOError:
		print("Error writing to file ./LAST_ACCESSED.txt")

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
			'rt_tco_expandedurl': ''
		}

		try:
			if (status.entities['urls']) or (status.retweeted_status.entities['urls']):
				tweetdeets['status_id'] = status.id
				tweetdeets['user'] = status.user.screen_name
				tweetdeets['name'] = status.user.name
				tweetdeets['text'] = status.text

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
						tweetdeets['rt_text'] = status.retweeted_status.text

						for rturl in status.retweeted_status.entities['urls']:
							#tweetdeets['rt_url'].append(unshorten_links.unshorten(rturl['expanded_url']))
							tweetdeets['rt_tco_expandedurl'].append(rturl['expanded_url'])
				except:
					pass
		except:
			pass

		# TODO: IF THE URL IS IN A BLACKLIST, SKIP IT (EX. TWITTER, TWITCH, ETC)
		# IF STATUS_ID IS EMPTY, WE DID NOT GET A RESULT, DON'T RETURN ANYTHING
		if tweetdeets['status_id']:
			_all_tweetdeets.append(tweetdeets)

	return(_all_tweetdeets)

def thread_unshorten(q, td_ref_dict):
	while True:
		message('unshortening URL')
		tweetdict = q.get()
		if not tweetdict['is_retweet']:
			for long_url in tweetdict['tco_expandedurl']:
				tweetdict['unshortened_url'].append(unshorten_links.unshorten(long_url))
		elif tweetdict['is_retweet']:
			for long_url in tweetdict['rt_tco_expandedurl']:
				tweetdict['rt_url'].append(unshorten_links.unshorten(long_url))
		q.task_done()

# TODO
# 1) THREADING FOR UNSHORTEN_URL
# 2) flask megatutorial, and start using flask + DB in AWS.

api = twitter_auth()

set_cursor = last_accessed_read()

statuses = []
# TODO: THIS SHOULD USE tweet_mode='extended', which will show full text of tweets with no truncation.
# TODO: ALL REFERENCES TO '.text' become '.full_text'
statuses.extend(tweepy.Cursor(api.home_timeline, since_id=set_cursor, include_entites=True, include_rts=True).items(NUMBER_OF_ITEMS))

all_tweetdeets = []
all_tweetdeets.extend(get_status_info(statuses))

# TODO: NOW WE HAVE ALL TWEETS IN A LIST OF DICTIONARIES. CREATE THREADED REQUESTS TO EXTEND ALL URLS
# 1) if !is_retweet, call unshorten function with all expanded_urls as argument, using queue to return using .put
# 2) if is_retweet, call unshorten function with all expanded_urls as argument, using queue to return using .put

for i in range(fetch_threads):
	worker = threading.Thread(target=thread_unshorten, args=(the_queue,td))
	worker.setDaemon(True)
	worker.start()

for td in all_tweetdeets:
	#TODO MOVE THE LOGIC FOR IS_RETWEET DOWN HERE
	thread_unshorten(q, td)

# TODO: ADD LOGGING, OUTPUT THIS AS LOG ENTRY WHEN IN DEBUG MODE
for x in all_tweetdeets:
	print(x)
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
	# 	print("*************************************************\n")

if set_cursor:
	print("Oldest tweet was: " + set_cursor)

if not statuses:
	print("No new tweets")
else:
	print("New Oldest tweet is: " + str(statuses[0].id))
	last_accessed_write(str(statuses[0].id))

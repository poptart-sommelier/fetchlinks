#!/usr/bin/python3

import requests
import json
import urllib3
from requests_oauthlib import OAuth1

# TODO: THREAD URL UNSHORTEN (TEST LEN FIRST WITH urllib3 to reduce lookups)
# TODO: API CALLS SHOULD HAVE A PARAMETER THAT LIMITS THE NUMBER OF CALLS (FOR TESTING). I.E DON'T LOOP UNTIL THROTTLED

NUMBER_OF_ITEMS = 200
CRED_PATH = '/home/rich/.creds/twitter_api.json'

LAST_ACCESSED_FILE = './LAST_ACCESSED.txt'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

CONSUMER_KEY = creds['twitter_creds'][0]['CONSUMER_KEY']
CONSUMER_SECRET = creds['twitter_creds'][0]['CONSUMER_SECRET']
ACCESS_TOKEN = creds['twitter_creds'][0]['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = creds['twitter_creds'][0]['ACCESS_TOKEN_SECRET']

AUTH = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

HOME_TIMELINE_URL = 'https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count=200&include_rts=True&include_entities=True'

def parse_retweet(json_response):
	urls = [url['expanded_url'] for url in json_response['retweeted_status']['entities']['urls']]

	if len(urls) > 0:
		r_dict = {
			'screen_name': json_response['retweeted_status']['user']['screen_name'],
			'user': json_response['retweeted_status']['user']['name'],
			'full_text': json_response['retweeted_status']['full_text'],
			'id': json_response['retweeted_status']['id_str'],
			'tweet_direct_link': 'https://twitter.com/' + json_response['retweeted_status']['screen_name'] + '/status/' + json_response['retweeted_status']['id_str'],
			'urls': urls
		}
		return r_dict

	else:
		return None


def parse_tweet(json_response):
	urls = [url['expanded_url'] for url in json_response['entities']['urls']]

	if len(urls) > 0:
		r_dict = {
			'screen_name': json_response['user']['screen_name'],
			'user': json_response['user']['name'],
			'full_text': json_response['full_text'],
			'id': json_response['id_str'],
			'tweet_direct_link': 'https://twitter.com/' + json_response['user']['screen_name'] + '/status/' + json_response['id_str'],
			'urls': urls
		}
		return r_dict

	else:
		return None


def get_last_tweet_id():
	try:
		with open(LAST_ACCESSED_FILE, 'r') as f:
			last_read = f.read()
			if last_read.isdigit():
				return last_read
			else:
				return 1

	except IOError:
		# logger.info('Cannot locate ./LAST_ACCESSED.txt. Creating a blank one now.')
		print('Cannot locate ./LAST_ACCESSED.txt. Creating a blank one now.')

		set_last_tweet_id('1')

		return 1


def set_last_tweet_id(last_write):
	try:
		with open(LAST_ACCESSED_FILE, 'w') as f:
			f.write(str(last_write))

	except IOError:
		# logger.info('Error writing to file ./LAST_ACCESSED.txt')
		print('Error writing to file ./LAST_ACCESSED.txt')


def is_shortened(url):
	# shortened paths look like this:
	# https://bit.ly/AjIOdjl/
	# use urllib to measure the following:
	# host length
	# does it have a path
	# how long is the path (shortened paths only have 6-8 characters-ish)
	# does the path have more than one '/'
	# we can eliminate a lot of urls that way, then unshorten the rest
	pass


def unshorten_url(url):
	# threaded function to unshorten urls
	pass


def get_tweets(since_id=1, max_id=None):
	tweets = []

	if max_id:
		req_path = HOME_TIMELINE_URL + '&since_id=' + str(since_id) + '&max_id=' + str(max_id)
	else:
		req_path = HOME_TIMELINE_URL + '&since_id=' + str(since_id)

	r = requests.get(req_path, auth=AUTH)

	if r.status_code == 200:
		json_resp = r.json()

		first_last_ids = {
			'latest_id': max([j['id'] for j in r.json()]),
			'oldest_id': min([j['id'] for j in r.json()])
		}
		print(first_last_ids)

# 		set_last_tweet_id(first_last_ids['latest_id'])

		for jr in json_resp:
			# Is a retweet
			if 'retweeted_status' in jr:
				t = parse_tweet(jr)
				if t:
					tweets.append(t)
			# Is not a retweet
			else:
				t = parse_tweet(jr)
				if t:
					tweets.append(t)

		return tweets, first_last_ids, r.status_code

	else:
		return None, None, None

all_tweets = []

temp_tweets = []

last_tweet_id = get_last_tweet_id()

temp_tweets, first_last, status_code = get_tweets(last_tweet_id)

all_tweets.extend(temp_tweets)

if status_code == 200:
# set_last_tweet_id(first_last['latest_id'])
	# use 'while' for normal function
	# while True:
	# use 'for' to restrict number of api calls.
	for i in range(2):
		temp_tweets, first_last, status_code = get_tweets(last_tweet_id, first_last['oldest_id'])
		# while since_id < max_id - 1
		if status_code == 200:
			if first_last['latest_id'] - 1 > int(last_tweet_id):
				all_tweets.extend(temp_tweets)
			else:
				break
		else:
			set_last_tweet_id(first_last['latest_id'])
			break

print(json.dumps(all_tweets))

#!/usr/bin/python3

import requests
import json
import urllib3
from requests_oauthlib import OAuth1

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

HOME_TIMELINE_URL = 'https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count=200&include_rts=True&include_entities=True&since_id='

def parse_retweet(json_response):
	urls = [url['expanded_url'] for url in json_response['retweeted_status']['entities']['urls']]

	if len(urls) > 0:
		r_dict = {
			'screen_name': json_response['retweeted_status']['user']['screen_name'],
			'user': json_response['retweeted_status']['user']['name'],
			'full_text': json_response['retweeted_status']['full_text'],
			'urls': [url['expanded_url'] for url in json_response['retweeted_status']['entities']['urls']]
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
			'urls': [url['expanded_url'] for url in json_response['entities']['urls']]
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

		with open(LAST_ACCESSED_FILE, 'w') as f:
			set_last_tweet_id('1')

		return 1


def set_last_tweet_id(last_write):
	try:
		with open(LAST_ACCESSED_FILE, 'w') as f:
			f.write(str(last_write))

	except IOError:
		# logger.info('Error writing to file ./LAST_ACCESSED.txt')
		print('Error writing to file ./LAST_ACCESSED.txt')


def get_tweets(since_id=1):
	tweets = []

	req_path = HOME_TIMELINE_URL + str(since_id)
	r = requests.get(req_path, auth=AUTH)

	if r.status_code == 200:
		json_resp = r.json()

		first_last_ids = {
			'latest_id': max([i['id'] for i in r.json()]),
			'oldest_id': min([i['id'] for i in r.json()])
		}
		print(first_last_ids)

		set_last_tweet_id(first_last_ids['latest_id'])

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

		return tweets

	else:
		return False

all_tweets = []

# while True:
for i in range(1):
	all_t = get_tweets(get_last_tweet_id())
	if all_t:
		all_tweets.extend(all_t)
	else:
		continue

print(all_tweets)
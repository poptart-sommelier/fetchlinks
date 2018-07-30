#!/usr/bin/python3

import requests
import json
from requests_oauthlib import OAuth1
import operator
import datetime
import unshorten_links
import os

# TODO: Add logging

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

JSON_OUTPUT_DIR = './JSON/'

HOME_TIMELINE_URL = 'https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count=200&include_rts=True&include_entities=True'


def parse_retweet(json_response):
	urls = [{'url': url['expanded_url'], 'unshort_url': None} for url in json_response['retweeted_status']['entities']['urls']]

	if len(urls) > 0:
		r_dict = {
			'screen_name': json_response['retweeted_status']['user']['screen_name'],
			'user': json_response['retweeted_status']['user']['name'],
			'full_text': json_response['retweeted_status']['full_text'],
			'id': json_response['retweeted_status']['id_str'],
			'tweet_direct_link': 'https://twitter.com/' + json_response['retweeted_status']['user']['screen_name'] + '/status/' + json_response['retweeted_status']['id_str'],
			'urls': urls,
			'tweet_type': 'retweet',
			'date_created': json_response['retweeted_status']['created_at']

		}
		return r_dict

	else:
		return None


def parse_quoted_tweet(json_response):
	urls = [{'url': url['expanded_url'], 'unshort_url': None} for url in json_response['quoted_status']['entities']['urls']]

	if len(urls) > 0:
		r_dict = {
			'screen_name': json_response['quoted_status']['user']['screen_name'],
			'user': json_response['quoted_status']['user']['name'],
			'full_text': json_response['quoted_status']['full_text'],
			'id': json_response['quoted_status']['id_str'],
			'tweet_direct_link': 'https://twitter.com/' + json_response['quoted_status']['user']['screen_name'] + '/status/' + json_response['quoted_status']['id_str'],
			'urls': urls,
			'tweet_type': 'quoted tweet',
			'date_created': json_response['quoted_status']['created_at']
		}
		return r_dict

	else:
		return None


def parse_tweet(json_response):
	urls = [{'url': url['expanded_url'], 'unshort_url': None} for url in json_response['entities']['urls']]

	if len(urls) > 0:
		r_dict = {
			'screen_name': json_response['user']['screen_name'],
			'user': json_response['user']['name'],
			'full_text': json_response['full_text'],
			'id': json_response['id_str'],
			'tweet_direct_link': 'https://twitter.com/' + json_response['user']['screen_name'] + '/status/' + json_response['id_str'],
			'urls': urls,
			'tweet_type': 'standard tweet',
			'date_created': json_response['created_at']
		}
		return r_dict

	else:
		return None


def write_json_output(_all_tweets):

	if os.path.exists(JSON_OUTPUT_DIR):
		json_output = JSON_OUTPUT_DIR + "json_output_" + str(datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")) + ".json"
	else:
		try:
			os.mkdir(JSON_OUTPUT_DIR)
			json_output = JSON_OUTPUT_DIR + "json_output_" + str(datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")) + ".json"
		except (IOError, OSError) as exception:
			print(exception)
			raise SystemExit

	with open(json_output, 'w') as j:
		json.dump(_all_tweets, j)


def get_last_tweet_id():
	try:
		with open(LAST_ACCESSED_FILE, 'r') as f:
			last_read = f.read()
			if last_read.isdigit():
				return int(last_read)
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


def get_tweets(since_id=1, max_id=None):
	tweets = []

	if max_id:
		req_path = HOME_TIMELINE_URL + '&since_id=' + str(since_id) + '&max_id=' + str(max_id)
	else:
		req_path = HOME_TIMELINE_URL + '&since_id=' + str(since_id)

	r = requests.get(req_path, auth=AUTH)

	json_resp = r.json()

	if (r.status_code == 200) and (len(json_resp) > 0):
		first_last_ids = {
			'latest_id': max([j['id'] for j in r.json()]),
			'oldest_id': min([j['id'] for j in r.json()])
		}
		print(first_last_ids)

# 		set_last_tweet_id(first_last_ids['latest_id'])

		for jr in json_resp:
			# Is a retweet
			if 'retweeted_status' in jr:
				t = parse_retweet(jr)
				if t:
					tweets.append(t)
			# Is a quoted tweet
			elif 'quoted_status' in jr:
				t = parse_quoted_tweet(jr)
				if t:
					tweets.append(t)
			# Is standard tweet
			else:
				t = parse_tweet(jr)
				if t:
					tweets.append(t)

		return tweets, first_last_ids, r.status_code, r.headers['x-rate-limit-remaining']

	else:
		return None, None, None


def go(api_calls):
	all_tweets = []
	temp_tweets = []

	last_tweet_id = get_last_tweet_id()

	temp_tweets, first_last, status_code, rate_limit_remaining = get_tweets(last_tweet_id)

	if temp_tweets:
		last_tweet_id_new = first_last['latest_id']
		all_tweets.extend(temp_tweets)
		for i in range(api_calls - 1):
			if last_tweet_id < first_last['oldest_id'] - 1 and \
							int(rate_limit_remaining) > 0 and \
							first_last['latest_id'] > first_last['oldest_id']:

				temp_tweets, first_last, status_code, rate_limit_remaining = get_tweets(last_tweet_id, first_last['oldest_id'])
				if status_code == 200:
					if first_last['latest_id'] - 1 > int(last_tweet_id):
						all_tweets.extend(temp_tweets)
					else:
						break
				else:
					break
			else:
				break

		set_last_tweet_id(last_tweet_id_new)

	all_tweets.sort(key=operator.itemgetter('id'))

	all_tweets_unshort = unshorten_links.unshorten_start(all_tweets)

	print(json.dumps(all_tweets_unshort))

	write_json_output(all_tweets_unshort)

go(9)

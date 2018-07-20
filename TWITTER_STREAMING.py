import json
import requests
from requests_oauthlib import OAuth1

CRED_PATH = '/home/rich/.creds/twitter_api.json'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

CONSUMER_KEY = creds['twitter_creds'][0]['CONSUMER_KEY']
CONSUMER_SECRET = creds['twitter_creds'][0]['CONSUMER_SECRET']
ACCESS_TOKEN = creds['twitter_creds'][0]['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = creds['twitter_creds'][0]['ACCESS_TOKEN_SECRET']

req_path = 'https://stream.twitter.com/1.1/statuses/filter.json?tweet_mode=extended&track=mykeywordhere'

AUTH = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

r = requests.get(req_path, auth=AUTH, stream=True)

for line in r.iter_lines():
	if line:
		j = json.loads(line)
		# print(json.dumps(j))
		if 'quoted_status' in j:
			if 'extended_tweet' in j['quoted_status']:
				print(j['quoted_status']['extended_tweet']['full_text'])
		elif 'retweeted_status' in j:
			if 'extended_tweet' in j['retweeted_status']:
				print(j['retweeted_status']['extended_tweet']['full_text'])
		elif 'extended_tweet' in j:
			print(j['extended_tweet']['full_text'])
		else:
			if 'text' in j:
				print(j['text'])


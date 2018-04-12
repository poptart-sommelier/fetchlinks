#!/usr/bin/python3

import requests
import json
from requests_oauthlib import OAuth1

CRED_PATH = '/home/rich/.creds/twitter_api.json'

JSON_DATA = open(CRED_PATH).read()
CREDS = json.loads(JSON_DATA)

CONSUMER_KEY = CREDS['twitter_creds'][0]['CONSUMER_KEY']
CONSUMER_SECRET = CREDS['twitter_creds'][0]['CONSUMER_SECRET']
ACCESS_TOKEN = CREDS['twitter_creds'][0]['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = CREDS['twitter_creds'][0]['ACCESS_TOKEN_SECRET']

AUTH = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

HOME_TIMELINE_URL = 'https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count=200&include_rts=True'

# NOW WE'RE MAKING REQUESTS

r = requests.get(HOME_TIMELINE_URL, auth=AUTH)

data = r.json()

latest_id = max([ i['id'] for i in data])
print('latest id is: ' + str(latest_id))

for t in data:
	print(t['full_text'])
	for u in t['entities']['urls']:
		print(u['expanded_url'])

with open('output.json', 'w') as j:
	json.dump(data, j)

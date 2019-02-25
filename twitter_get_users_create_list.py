# TODO: THIS SHOULD BE GENERALIZED TO CLONE THE ACCOUNT TO A LIST ON DEMAND.
import json
import requests
from requests_oauthlib import OAuth1

# MAIN PART OF THIS IS USING 'requests.get(stream=True)

CRED_PATH = '/home/rich/.creds/twitter_api.json'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

CONSUMER_KEY = creds['twitter_creds']['CONSUMER_KEY']
CONSUMER_SECRET = creds['twitter_creds']['CONSUMER_SECRET']
ACCESS_TOKEN = creds['twitter_creds']['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = creds['twitter_creds']['ACCESS_TOKEN_SECRET']

FOLLOWING_REQ_PATH = 'https://api.twitter.com/1.1/friends/list.json'

FOLLOWING_PARAMS = {'screen_name': 'poptrtsommelier',
          'skip_status': 'true',
          'count': '200',
          'include_user_entities': 'true'
                    }

LIST_REQ_PATH = 'https://api.twitter.com/1.1/lists/members/create_all.json'

LIST_POST_DATA = {'slug': 'infosec',
               'owner_screen_name': 'poptrtsommelier',
               'screen_name': ''}

AUTH = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

r = requests.get(FOLLOWING_REQ_PATH, params=FOLLOWING_PARAMS, auth=AUTH)

response = r.json()

users = [u['screen_name'] for u in response['users']]

LIST_POST_DATA['screen_name'] = ','.join(users[:99])
r = requests.post(LIST_REQ_PATH, data=LIST_POST_DATA, auth=AUTH)

LIST_POST_DATA['screen_name'] = ','.join(users[100:])
r = requests.post(LIST_REQ_PATH, data=LIST_POST_DATA, auth=AUTH)

print()

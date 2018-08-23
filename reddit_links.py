import requests
import json

CRED_PATH = '/home/rich/.creds/reddit_api.json'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

APP_CLIENT_ID = creds['reddit_creds']['APP_CLIENT_ID']
APP_CLIENT_SECRET = creds['reddit_creds']['APP_CLIENT_SECRET']

SUBREDDITS = ['Netsec']
QUERY_PART1 = 'https://oauth.reddit.com/r/'
QUERY_PART2 = '/.json'
USER_AGENT = 'Get_Links Agent'
PARAMS = {'sort': 'top', 'show': 'all', 't': 'all'}


def auth():
    # Get an access token
    auth = requests.post('https://www.reddit.com/api/v1/access_token',
                         headers={'User-agent': 'get_links_lol'},
                         data={'grant_type': 'client_credentials'},
                         auth=(APP_CLIENT_ID, APP_CLIENT_SECRET))

    access_token = auth.json()['access_token']
    return access_token


def make_request(reddit, token, after=None):
    # Build the URL
    if not after:
        url = QUERY_PART1 + reddit + QUERY_PART2
    else:
        url = QUERY_PART1 + reddit + QUERY_PART2
        PARAMS['after'] = after

    api_res = requests.get(url=url, params=PARAMS, headers={'authorization': 'Bearer ' + token, 'User-agent': USER_AGENT})

    # Now for a regular API request (but to oauth.reddit.com)
    # api_res = requests.get('https://oauth.reddit.com/r/redditdev/comments/61noov/do_you_have_to_authenticate_even_if_only_using/.json',
    #                        headers={'authorization': 'Bearer ' + token, 'User-agent': USER_AGENT})

    post_listing = api_res.json()
    post = post_listing['data']['children'][0]
    title = post['data']['title']


def parse_json():
    pass


token = auth()

make_request('Netsec', token)

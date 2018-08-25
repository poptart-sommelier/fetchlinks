import requests
import json

CRED_PATH = '/home/rich/.creds/reddit_api.json'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

APP_CLIENT_ID = creds['reddit_creds']['APP_CLIENT_ID']
APP_CLIENT_SECRET = creds['reddit_creds']['APP_CLIENT_SECRET']

SUBREDDITS = ['Netsec', 'Malware', 'Antiforensics', 'Computerforensics', 'ReverseEngineering']
QUERY_PART1 = 'https://oauth.reddit.com/r/'
QUERY_PART2 = '/new/.json'
USER_AGENT = 'Get_Links Agent'


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
        params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '25'}
    else:
        url = QUERY_PART1 + reddit + QUERY_PART2
        params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '25', 'after': after}

    api_res = requests.get(url=url, params=params, headers={'authorization': 'Bearer ' + token, 'User-agent': USER_AGENT})

    new_posts = api_res.json()

    return new_posts

# TODO: Parse 'selftext' for urls and add them. condition should be if not reddit and selftext_urls = None
def parse_json(json_response):
    if not json_response['data']['url'].startswith('https://www.reddit.com/'):
        r_dict = {
            'user': json_response['data']['author'],
            'title': json_response['data']['title'],
            'id': json_response['data']['id'],
            'direct_link': 'https://www.reddit.com' + json_response['data']['permalink'],
            'url': json_response['data']['url'],
            'subreddit': json_response['data']['subreddit_name_prefixed'],
            'date_created': json_response['data']['created_utc']
        }

        return r_dict
    else:
        return None


all_posts = []

token = auth()

for sr in SUBREDDITS:
    resp = make_request(sr, token)
    print(json.dumps(resp))

    for r in resp['data']['children']:
        post = parse_json(r)
        if post:
            all_posts.append(post)



print(json.dumps(all_posts))

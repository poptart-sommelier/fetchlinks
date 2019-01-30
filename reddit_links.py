import requests
import json
import re
import hashlib

# Importing Datastructure class
# from structure_data import Datastructure

# TODO: LIMIT API CALL -> NEWER THAN UTC TIME?
# TODO: /r/all.json?before=yyy WHERE yyy = first instance of data.children[0].data.name
# TODO: error handling
# TODO: proper logging

CRED_PATH = '/home/rich/.creds/reddit_api.json'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

APP_CLIENT_ID = creds['reddit_creds']['APP_CLIENT_ID']
APP_CLIENT_SECRET = creds['reddit_creds']['APP_CLIENT_SECRET']

SUBREDDITS = ['Netsec', 'Malware', 'Antiforensics', 'Computerforensics', 'ReverseEngineering']
QUERY_PART1 = 'https://oauth.reddit.com/r/'
QUERY_PART2 = '/new/.json'
USER_AGENT = 'Get_Links Agent'


def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def auth():
    # Get an access token
    authentication = requests.post('https://www.reddit.com/api/v1/access_token',
                                   headers={'User-agent': 'get_links_lol'},
                                   data={'grant_type': 'client_credentials'},
                                   auth=(APP_CLIENT_ID, APP_CLIENT_SECRET))

    access_token = authentication.json()['access_token']
    return access_token


def make_request(reddit, token, before=None):
    # Build the URL
    if not before:
        url = QUERY_PART1 + reddit + QUERY_PART2
        params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '25'}
    else:
        url = QUERY_PART1 + reddit + QUERY_PART2
        params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '25', 'before': before}

    api_res = requests.get(url=url, params=params, headers={'authorization': 'Bearer ' + token,
                                                            'User-agent': USER_AGENT})

    new_posts = api_res.json()

    return new_posts


def parse_json(json_response):
    # Detection for reddit posts with no links
    feed_dict = {
        'source': '',
        'author': '',
        'title': '',
        'description': '',
        'direct_link': '',
        'urls': [],
        'date_created': '',
        'unique_id': ''
    }
    if not json_response['data']['url'].startswith('https://www.reddit.com/'):
        feed_dict['source'] = 'reddit/' + json_response['data']['subreddit_name_prefixed']
        feed_dict['author'] = json_response['data']['author']
        feed_dict['title'] = json_response['data']['title']
        feed_dict['description'] = None
        feed_dict['direct_link'] = 'https://www.reddit.com' + json_response['data']['permalink']
        feed_dict['urls'] = [json_response['data']['url']]
        feed_dict['date_created'] = json_response['data']['created_utc']
        feed_dict['unique_id'] = build_hash(json_response['data']['url'])

        return feed_dict

    else:
        selftext_urls = []
        if json_response['data']['selftext_html'] is not None:
            selftext_urls = re.findall(r'href=[\'"]?([^\'" >]+)', json_response['data']['selftext_html'])
            if selftext_urls is None:
                return None

        feed_dict['source'] = 'reddit' + json_response['data']['subreddit_name_prefixed']
        feed_dict['author'] = json_response['data']['author']
        feed_dict['title'] = json_response['data']['title']
        feed_dict['description'] = None
        feed_dict['direct_link'] = 'https://www.reddit.com' + json_response['data']['permalink']
        feed_dict['urls'] = selftext_urls
        feed_dict['date_created'] = json_response['data']['created_utc']
        feed_dict['unique_id'] = build_hash(''.join(sorted(selftext_urls)))

        return feed_dict


def go():
    all_posts = []

    token = auth()

    for sr in SUBREDDITS:
        resp = make_request(sr, token)
        # print(json.dumps(resp))

        for r in resp['data']['children']:
            post = parse_json(r)
            if post:
                all_posts.append(post)

    print(json.dumps(all_posts))
    return all_posts


if __name__ == '__main__':
    go()

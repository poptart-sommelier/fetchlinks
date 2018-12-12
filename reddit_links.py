import requests
import json
import re
import hashlib

# TODO: LIMIT API CALL -> NEWER THAN UTC TIME?
# TODO: /r/all.json?before=yyy WHERE yyy = first instance of data.children[0].data.name
# TODO: generate unique ids for all entries based on unshortened link

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
    hash = hashlib.sha256(link.encode())
    return hash.hexdigest()


def auth():
    # Get an access token
    auth = requests.post('https://www.reddit.com/api/v1/access_token',
                         headers={'User-agent': 'get_links_lol'},
                         data={'grant_type': 'client_credentials'},
                         auth=(APP_CLIENT_ID, APP_CLIENT_SECRET))

    access_token = auth.json()['access_token']
    return access_token


def make_request(reddit, token, before=None):
    # Build the URL
    if not before:
        url = QUERY_PART1 + reddit + QUERY_PART2
        params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '25'}
    else:
        url = QUERY_PART1 + reddit + QUERY_PART2
        params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '25', 'before': before}

    api_res = requests.get(url=url, params=params, headers={'authorization': 'Bearer ' + token, 'User-agent': USER_AGENT})

    new_posts = api_res.json()

    return new_posts


def parse_json(json_response):
    if not json_response['data']['url'].startswith('https://www.reddit.com/'):
        r_dict = {
            'author': json_response['data']['author'],
            'title': json_response['data']['title'],
            'description': None,
            'uid': build_hash(json_response['data']['url']),
            'direct_link': 'https://www.reddit.com' + json_response['data']['permalink'],
            'urls': [json_response['data']['url']],
            'source': 'reddit/' + json_response['data']['subreddit_name_prefixed'],
            'date_created': json_response['data']['created_utc']
        }

        return r_dict

    else:
        selftext_urls = []
        if json_response['data']['selftext_html'] is not None:
            selftext_urls = re.findall(r'href=[\'"]?([^\'" >]+)', json_response['data']['selftext_html'])

        r_dict = {
            'author': json_response['data']['author'],
            'title': json_response['data']['title'],
            'description': None,
            'uid': ''.join(sorted(selftext_urls)),
            'direct_link': 'https://www.reddit.com' + json_response['data']['permalink'],
            'urls': selftext_urls,
            'source': 'reddit/' + json_response['data']['subreddit_name_prefixed'],
            'date_created': json_response['data']['created_utc']
        }

        if selftext_urls is not None:
            return r_dict

        else:
            return None


def main():
    all_posts = []

    token = auth()

    for sr in SUBREDDITS:
        resp = make_request(sr, token)
        # print(json.dumps(resp))

        for r in resp['data']['children']:
            post = parse_json(r)
            if post:
                all_posts.append(post)

    # print(json.dumps(all_posts))
    return all_posts


if __name__ == '__main__':
    main()

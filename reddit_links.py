import requests
import json
import re
import hashlib
import datetime
import db_interact

# Importing Datastructure class
import structure_data

import logging
logger = logging.getLogger(__name__)


def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def auth(credential_location):
    try:
        with open(credential_location, 'r') as json_data:
            creds = json.load(json_data)
    except IOError:
        logger.error('Could not load Reddit credentials from: ' + credential_location)
        exit()

    app_client_id = creds['reddit_creds']['APP_CLIENT_ID']
    app_client_secret = creds['reddit_creds']['APP_CLIENT_SECRET']

    # Get an access token
    authentication = requests.post('https://www.reddit.com/api/v1/access_token',
                                   headers={'User-agent': 'get_links_lol'},
                                   data={'grant_type': 'client_credentials'},
                                   auth=(app_client_id, app_client_secret))

    access_token = authentication.json()['access_token']
    return access_token


def make_request(subreddit, token):
    query_part1 = 'https://oauth.reddit.com/r/'
    query_part2 = '/new/.json'
    user_agent = 'Get_Links Agent'

    # Build the URL
    url = query_part1 + subreddit + query_part2
    params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '100'}

    api_res = requests.get(url=url, params=params, headers={'authorization': 'Bearer ' + token,
                                                            'User-agent': user_agent})

    new_posts = api_res.json()
    logger.info('{} returned {} entries'.format(subreddit, len(new_posts['data']['children'])))

    after = new_posts['data']['after']

    return new_posts


def convert_date_reddit_to_mysql(reddit_date):
    date_object = datetime.datetime.utcfromtimestamp(int(reddit_date))
    return datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')


def parse_json(json_response):
    parsed_reddit_data = structure_data.Datastructure()

    if not json_response['data']['url'].startswith('https://www.reddit.com/'):
        parsed_reddit_data.data_structure['source'] = 'https://www.reddit.com/' + json_response['data']['subreddit_name_prefixed']
        parsed_reddit_data.data_structure['author'] = json_response['data']['author']
        parsed_reddit_data.data_structure['description'] = json_response['data']['title']
        parsed_reddit_data.data_structure['direct_link'] = 'https://www.reddit.com' + json_response['data']['permalink']
        parsed_reddit_data.data_structure['urls'] = [{'url': json_response['data']['url'], 'unshort_url': None,
                                                      'unique_id': build_hash(json_response['data']['url']),
                                                      'unshort_unique_id': None}]
        parsed_reddit_data.data_structure['date_created'] = \
            convert_date_reddit_to_mysql(json_response['data']['created_utc'])
        parsed_reddit_data.data_structure['unique_id_string'] = ','.join([url['unique_id'] for
                                                                   url in parsed_reddit_data.data_structure['urls']])

        return parsed_reddit_data

    else:
        selftext_urls = []
        if not json_response['data']['selftext_html']:
            return None

        else:
            selftext_urls = [{'url': url, 'unshort_url': None, 'unique_id': build_hash(url), 'unshort_unique_id': None}
                             for url in re.findall(r'href=[\'"]?([^\'" >]+)', json_response['data']['selftext_html'])
                             if '.' in url]

            if len(selftext_urls) < 1:
                return None

        parsed_reddit_data.data_structure['source'] = 'https://www.reddit.com/' + json_response['data']['subreddit_name_prefixed']
        parsed_reddit_data.data_structure['author'] = json_response['data']['author']
        parsed_reddit_data.data_structure['description'] = json_response['data']['title']
        parsed_reddit_data.data_structure['direct_link'] = 'https://www.reddit.com' + json_response['data']['permalink']
        parsed_reddit_data.data_structure['urls'] = selftext_urls
        parsed_reddit_data.data_structure['date_created'] = \
            convert_date_reddit_to_mysql(json_response['data']['created_utc'])
        parsed_reddit_data.data_structure['unique_id_string'] = ','.join([url['unique_id'] for
                                                                   url in parsed_reddit_data.data_structure['urls']])

        return parsed_reddit_data


def main(config):
    all_posts = []

    token = auth(config['credential_location'])

    for sr in config['subreddits']:

        resp = make_request(sr, token)

        # print(json.dumps(resp))

        for r in resp['data']['children']:
            post = parse_json(r)
            if post:
                all_posts.append(post)

    logger.info('Returning {} entries.'.format(len(all_posts)))

    return all_posts


if __name__ == '__main__':
    main()

#!/usr/bin/python3

import requests
import json
from requests_oauthlib import OAuth1
import operator
import datetime
import unshorten_links
import os
import hashlib

# Importing Datastructure class
import structure_data

# TODO: Add logging
# TODO: Generate unique IDs (based on unshortened url) for all entries
# TODO: Store all state in a DB

NUMBER_OF_ITEMS = 200
CRED_PATH = '/home/rich/.creds/twitter_api.json'

# TODO: This needs to be put into the database
LAST_ACCESSED_FILE = './LAST_ACCESSED.txt'

json_data = open(CRED_PATH).read()
creds = json.loads(json_data)

CONSUMER_KEY = creds['twitter_creds']['CONSUMER_KEY']
CONSUMER_SECRET = creds['twitter_creds']['CONSUMER_SECRET']
ACCESS_TOKEN = creds['twitter_creds']['ACCESS_TOKEN']
ACCESS_TOKEN_SECRET = creds['twitter_creds']['ACCESS_TOKEN_SECRET']

AUTH = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

JSON_OUTPUT_DIR = './data/JSON/'

HOME_TIMELINE_URL = 'https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count=200&include_rts=True&include_entities=True'

def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def parse_retweet(json_response):
    parsed_tweet_data = structure_data.Datastructure()

    urls = [{'url': url['expanded_url'], 'unshort_url': None} for url in json_response['retweeted_status']['entities']['urls']]
    urls_list = [u for u in json_response['retweeted_status']['entities']['urls']]

    if len(urls) > 0:
        parsed_tweet_data.data_structure['source'] = 'twitter'
        parsed_tweet_data.data_structure['author'] = json_response['retweeted_status']['user']['screen_name'] + ': ' + json_response['retweeted_status']['user']['name']
        parsed_tweet_data.data_structure['title'] = None
        parsed_tweet_data.data_structure['description'] = json_response['retweeted_status']['full_text']
        parsed_tweet_data.data_structure['direct_link'] = 'https://twitter.com/' + json_response['retweeted_status']['user']['screen_name'] + '/status/' + json_response['retweeted_status']['id_str']
        parsed_tweet_data.data_structure['urls'] = urls
        parsed_tweet_data.data_structure['date_created'] = json_response['retweeted_status']['created_at']
        # parsed_tweet_data.data_structure['unique_id'] = build_hash(''.join(sorted(urls_list)))

        return parsed_tweet_data

    else:
        return None


def parse_quoted_tweet(json_response):
    parsed_tweet_data = structure_data.Datastructure()

    urls = [{'url': url['expanded_url'], 'unshort_url': None} for url in json_response['quoted_status']['entities']['urls']]
    urls_list = [u for u in json_response['quoted_status']['entities']['urls']]

    if len(urls) > 0:

        parsed_tweet_data.data_structure['source'] = 'twitter'
        parsed_tweet_data.data_structure['author'] = json_response['quoted_status']['user']['screen_name'] + ': ' + json_response['quoted_status']['user']['name']
        parsed_tweet_data.data_structure['title'] = None
        parsed_tweet_data.data_structure['description'] = json_response['quoted_status']['full_text']
        parsed_tweet_data.data_structure['direct_link'] = 'https://twitter.com/' + json_response['quoted_status']['user']['screen_name'] + '/status/' + json_response['quoted_status']['id_str']
        parsed_tweet_data.data_structure['urls'] = urls
        parsed_tweet_data.data_structure['date_created'] = json_response['quoted_status']['created_at']
        # parsed_tweet_data.data_structure['unique_id'] = build_hash(''.join(sorted(urls_list)))

        return parsed_tweet_data

    else:
        return None


def parse_tweet(json_response):
    parsed_tweet_data = structure_data.Datastructure()

    urls = [{'url': url['expanded_url'], 'unshort_url': None} for url in json_response['entities']['urls']]
    urls_list = [u for u in json_response['entities']['urls']]

    if len(urls) > 0:
        parsed_tweet_data.data_structure['source'] = 'twitter'
        parsed_tweet_data.data_structure['author'] = json_response['user']['screen_name'] + ': ' + json_response['user']['name']
        parsed_tweet_data.data_structure['title'] = None
        parsed_tweet_data.data_structure['description'] = json_response['full_text']
        parsed_tweet_data.data_structure['direct_link'] = 'https://twitter.com/' + json_response['user']['screen_name'] + '/status/' + json_response['id_str']
        parsed_tweet_data.data_structure['urls'] = urls
        parsed_tweet_data.data_structure['date_created'] = json_response['created_at']
        # parsed_tweet_data.data_structure['unique_id'] = build_hash(''.join(sorted(urls_list)))

        return parsed_tweet_data

    else:
        return None


# TODO: PULL THIS FROM DB
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


# TODO: STORE THIS IN DB
def set_last_tweet_id(last_write):
    try:
        with open(LAST_ACCESSED_FILE, 'w') as f:
            f.write(str(last_write))

    except IOError:
        # logger.info('Error writing to file ./LAST_ACCESSED.txt')
        print('Error writing to file ./LAST_ACCESSED.txt')


def build_unique_ids(all_tweets):
    for tweet in all_tweets:
        url_list = []
        for urls in tweet.data_structure['urls']:
            if urls['unshort_url'] is None:
                url_list.append(urls['url'])
            else:
                url_list.append(urls['unshort_url'])

        tweet.data_structure['unique_id'] = build_hash(''.join(sorted(url_list)))

    return


def get_tweets(since_id=1, first_last=None):
    tweets = []
    keep_going = False

    if first_last:
        req_path = HOME_TIMELINE_URL + '&since_id=' + str(since_id) + '&max_id=' + str(first_last['oldest_id'])
    else:
        req_path = HOME_TIMELINE_URL + '&since_id=' + str(since_id)

    r = requests.get(req_path, auth=AUTH)

    json_resp = r.json()

    if (r.status_code == 200) and (len(json_resp) > 0):
        new_first_last_ids = {
            'latest_id': max([j['id'] for j in r.json()]),
            'oldest_id': min([j['id'] for j in r.json()])
        }
        # print(new_first_last_ids)

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

        if since_id < new_first_last_ids['oldest_id'] - 1 and \
                int(r.headers['x-rate-limit-remaining']) > 0 and \
                new_first_last_ids['latest_id'] > new_first_last_ids['oldest_id']:
            keep_going = True

        else:
            keep_going = False

        return tweets, new_first_last_ids, keep_going

    else:
        return None, None, None


def go(api_calls_limit):
    all_tweets = []
    temp_tweets = []
    keep_going = False

    last_tweet_id = get_last_tweet_id()

    temp_tweets, first_last, keep_going = get_tweets(last_tweet_id)

    if temp_tweets:
        last_tweet_id_new = first_last['latest_id']
        all_tweets.extend(temp_tweets)
    else:
        return

    if keep_going:
        for i in range(api_calls_limit - 1):
            temp_tweets, first_last, keep_going = get_tweets(last_tweet_id, first_last)
            if keep_going is True:
                all_tweets.extend(temp_tweets)
            else:
                break

    set_last_tweet_id(last_tweet_id_new)

    all_tweets_unshort = unshorten_links.unshorten_start(all_tweets)

    build_unique_ids(all_tweets_unshort)

    # print(json.dumps([x.data_structure for x in all_tweets_unshort]))

    return all_tweets_unshort


if __name__ == '__main__':
    go(1)

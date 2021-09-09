#!/usr/bin/python3

import requests
import json
import datetime
import re
import logging

# Custom
import utils
from utils import TwitterPost
from auth import TwitterAuth
import db_utils
import unshorten_links

logger = logging.getLogger(__name__)


def convert_date_twitter_to_mysql(twitter_date):
    date_object = datetime.datetime.strptime(twitter_date, '%a %b %d %H:%M:%S %z %Y')
    return datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')


def parse_retweet(json_response):
    parsed_tweet_data = utils.Post()

    urls = [{'url': url['expanded_url'], 'unshort_url': None, 'unique_id': utils.build_hash(url['expanded_url']),
             'unshort_unique_id': None} for url in json_response['retweeted_status']['entities']['urls']]

    if len(urls) > 0:
        parsed_tweet_data.source = 'https://twitter.com/'\
                                                     + json_response['retweeted_status']['user']['screen_name']
        parsed_tweet_data.author = json_response['retweeted_status']['user']['name']
        parsed_tweet_data.description = re.sub(r"http(s)?://t\.co/[a-z0-9A-Z]+", '', json_response['retweeted_status']['full_text'])
        parsed_tweet_data.direct_link = 'https://twitter.com/' + json_response['retweeted_status']['user']['screen_name'] + '/status/' + json_response['retweeted_status']['id_str']
        parsed_tweet_data.urls = urls
        parsed_tweet_data.date_created = convert_date_twitter_to_mysql(json_response['retweeted_status']['created_at'])

        return parsed_tweet_data

    else:
        return None


def parse_quoted_tweet(json_response):
    parsed_tweet_data = utils.Post()

    urls = [{'url': url['expanded_url'], 'unshort_url': None, 'unique_id': utils.build_hash(url['expanded_url']),
             'unshort_unique_id': None} for url in json_response['quoted_status']['entities']['urls']]

    if len(urls) > 0:

        parsed_tweet_data.source = 'https://twitter.com/'\
                                                     + json_response['quoted_status']['user']['screen_name']
        parsed_tweet_data.author = json_response['quoted_status']['user']['name']
        parsed_tweet_data.description = re.sub(r"http(s)?://t\.co/[a-z0-9A-Z]+", '', json_response['quoted_status']['full_text'])
        parsed_tweet_data.direct_link = 'https://twitter.com/' + json_response['quoted_status']['user']['screen_name'] + '/status/' + json_response['quoted_status']['id_str']
        parsed_tweet_data.urls = urls
        parsed_tweet_data.date_created = convert_date_twitter_to_mysql(json_response['quoted_status']['created_at'])

        return parsed_tweet_data

    else:
        return None


def parse_tweet(json_response):
    parsed_tweet_data = utils.Post()

    urls = [{'url': url['expanded_url'], 'unshort_url': None, 'unique_id': utils.build_hash(url['expanded_url']),
             'unshort_unique_id': None} for url in json_response['entities']['urls']]

    if len(urls) > 0:
        parsed_tweet_data.source = 'https://twitter.com/' + json_response['user']['screen_name']
        parsed_tweet_data.author = json_response['user']['name']
        parsed_tweet_data.description = re.sub(r"http(s)?://t\.co/[a-z0-9A-Z]+", '', json_response['full_text'])
        parsed_tweet_data.direct_link = 'https://twitter.com/' + json_response['user']['screen_name'] + '/status/' + json_response['id_str']
        parsed_tweet_data.urls = urls
        parsed_tweet_data.date_created = convert_date_twitter_to_mysql(json_response['created_at'])

        return parsed_tweet_data

    else:
        return None


def build_unique_id_string(all_tweets):
    for tweet in all_tweets:
        unique_id_list = []
        for urls in tweet.urls:
            if urls['unshort_url'] is None:
                unique_id_list.append(urls['unique_id'])
            else:
                unique_id_list.append(urls['unshort_unique_id'])

        tweet.unique_id_string = ','.join(unique_id_list)

    return


def parse_json_resp(all_tweets_json):
    tweets = []

    for tweet_json in all_tweets_json:
        # Is a retweet
        if 'retweeted_status' in tweet_json:
            t = parse_retweet(tweet_json)
            if t:
                tweets.append(t)
        # Is a quoted tweet
        elif 'quoted_status' in tweet_json:
            t = parse_quoted_tweet(tweet_json)
            if t:
                tweets.append(t)
        # Is standard tweet
        else:
            t = parse_tweet(tweet_json)
            if t:
                tweets.append(t)

    return tweets


def get_tweets(authentication, since_id=1, first_last=None):
    tweets = []
    keep_going = False

    host = 'https://api.twitter.com'
    endpoint = '/1.1/statuses/home_timeline.json'
    url_params = {'tweet_mode': 'extended',
                  'count': '200',
                  'include_rts': 'True',
                  'include_entities': 'True'
                  }

    if first_last:
        url_params['since_id'] = str(since_id)
        url_params['max_id'] = str(first_last['oldest_id'])
    else:
        url_params['since_id'] = str(since_id)

    r = requests.get(host + endpoint, params=url_params, auth=authentication)

    json_resp = r.json()

    return json_resp, r.status_code, int(r.headers['x-rate-limit-remaining'])


def run(twitter_config, db_info):
    all_tweets = []
    all_tweets_json = []
    tweets_json = []

    twitter_api_calls_max = 15

    # authentication = auth(twitter_config['credential_location'])
    twitter_auth = TwitterAuth(twitter_config['credential_location'])
    authentication = twitter_auth.get_auth()

    previous_run_newest_tweet_id = db_utils.db_get_last_tweet_id(db_info['db_location'] + db_info['db_name'])

    logger.debug('Making {} API calls. Starting with {} tweet id.'.format(twitter_api_calls_max, previous_run_newest_tweet_id))

    while True:
        tweets_json, status_code, api_calls_remaining = get_tweets(authentication, previous_run_newest_tweet_id)
        logger.info(f'Got {len(tweets_json)} tweets.\nStatus Code: {status_code}.\nAPI calls remaining: {api_calls_remaining}')

        # We have made an API call, substract by one
        twitter_api_calls_max = twitter_api_calls_max - 1

        if status_code == 200 and len(tweets_json) > 0:
            all_tweets_json.extend(tweets_json)
            newest_id = max([j['id'] for j in tweets_json])
            oldest_id = min([j['id'] for j in tweets_json])
        else:
            break

        if not (previous_run_newest_tweet_id < oldest_id - 1 and
                api_calls_remaining > 0 and
                twitter_api_calls_max > 0 and
                newest_id > oldest_id
                ):
            break

    if len(all_tweets_json) > 0:
        all_tweets = parse_json_resp(all_tweets_json)
    else:
        logger.info('No tweets retrieved. Returning.')
        return

    all_tweets_unshort = unshorten_links.unshorten_start(all_tweets)

    build_unique_id_string(all_tweets_unshort)

    db_utils.db_set_last_tweet_id(newest_id, db_info['db_full_path'])

    logger.info('Returning {} entries.'.format(len(all_tweets_unshort)))

    return all_tweets_unshort

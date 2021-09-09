# Standard libraries
import logging

# External libraries
import tweepy

# Custom libraries
from auth import TwitterAuth
import db_utils
from utils import TwitterPost

logger = logging.getLogger(__name__)


def get_twitter_api(twitter_config):
    twitter_secrets = TwitterAuth(twitter_config['credential_location'])

    auth = tweepy.OAuthHandler(twitter_secrets.consumer_key, twitter_secrets.consumer_secret)
    auth.set_access_token(twitter_secrets.access_token, twitter_secrets.access_token_secret)

    return tweepy.API(auth)


def get_tweets(api, last_id):
    posts = list()

    try:
        for tweet in tweepy.Cursor(api.home_timeline, count=200, since_id=last_id,
                                   exclude_replies=True, tweet_mode='extended').items():
            temp = tweet
            posts.append(TwitterPost(tweet))
    except tweepy.RateLimitError as e:
        logger.error(f'Out of api calls: {e}')

    return posts


def run(twitter_config, db_info):
    api = get_twitter_api(twitter_config)
    last_id = db_utils.db_get_last_tweet_id(db_info['db_location'] + db_info['db_name'])

    posts = get_tweets(api, last_id)


    print()
import requests
import datetime
import logging

# Custom libraries
from utils import RedditPost
import db_utils
from auth import RedditAuth

logger = logging.getLogger(__name__)


def get_subreddits(reddit_config):
    subreddit_posts = list()

    reddit_auth = RedditAuth(reddit_config['credential_location'])
    token = reddit_auth.get_auth()

    for subreddit in reddit_config['subreddits']:
        subreddit_posts.extend(get_subreddit(subreddit, token))

    return subreddit_posts


def get_subreddit(subreddit, token):
    subreddit_url = f'https://oauth.reddit.com/r/{subreddit}/new/.json'
    params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '100'}
    user_agent = 'Get_Links Agent'
    headers = {'authorization': f'Bearer {token}', 'User-agent': user_agent}

    try:
        subreddit_resp = requests.get(url=subreddit_url, params=params, headers=headers)
        subreddit_resp = subreddit_resp.json()
        subreddit_posts = subreddit_resp['data']['children']
    except requests.exceptions.HTTPError as errh:
        logger.error("Http Error:", errh)
    except requests.exceptions.ConnectionError as errc:
        logger.error("Error Connecting:", errc)
    except requests.exceptions.Timeout as errt:
        logger.error("Timeout Error:", errt)
    except requests.exceptions.RequestException as err:
        logger.error("OOps: Something Else", err)

    logger.debug(f'{subreddit} returned {len(subreddit_posts)} entries')

    return subreddit_posts


def parse_posts(posts):
    parsed_posts = list()

    for post in posts:
        parsed_post = RedditPost(post)
        if len(parsed_post.urls) > 0:
            parsed_posts.append(parsed_post)
    return parsed_posts


def run(reddit_config, config):
    subreddit_posts = get_subreddits(reddit_config)
    parsed_posts = parse_posts(subreddit_posts)

    if parsed_posts is not None:
        db_full_path = config['db_info']['db_location'] + config['db_info']['db_name']
        db_utils.db_insert(parsed_posts, db_full_path)
        logger.info(f'Inserted {len(parsed_posts)} Rss posts into DB')
    else:
        logging.info('No new Rss posts found')

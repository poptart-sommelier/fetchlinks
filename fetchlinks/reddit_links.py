import requests
import logging
from pathlib import Path
from typing import List

# Custom libraries
from utils import RedditPost
import db_utils
from auth import RedditAuth

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 20


def get_subreddits(reddit_config: dict) -> List[dict]:
    subreddit_posts = list()

    reddit_auth = RedditAuth(reddit_config['credential_location'])
    token = reddit_auth.get_auth()

    for subreddit in reddit_config['subreddits']:
        subreddit_posts.extend(get_subreddit(subreddit, token, reddit_auth.user_agent))

    return subreddit_posts


def get_subreddit(subreddit: str, token: str, user_agent: str) -> List[dict]:
    subreddit_url = f'https://oauth.reddit.com/r/{subreddit}/new/.json'
    params = {'sort': 'new', 'show': 'all', 't': 'all', 'limit': '100'}
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': user_agent}

    try:
        response = requests.get(
            url=subreddit_url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        subreddit_resp = response.json()
    except ValueError as err:
        logger.error('Invalid JSON while retrieving r/%s: %s', subreddit, err)
        return []
    except requests.exceptions.HTTPError as errh:
        logger.error('HTTP error while retrieving r/%s: %s', subreddit, errh)
        return []
    except requests.exceptions.ConnectionError as errc:
        logger.error('Connection error while retrieving r/%s: %s', subreddit, errc)
        return []
    except requests.exceptions.Timeout as errt:
        logger.error('Timeout while retrieving r/%s: %s', subreddit, errt)
        return []
    except requests.exceptions.RequestException as err:
        logger.error('Request error while retrieving r/%s: %s', subreddit, err)
        return []

    subreddit_posts = subreddit_resp.get('data', {}).get('children', [])
    if not isinstance(subreddit_posts, list):
        logger.error('Unexpected Reddit payload shape for r/%s', subreddit)
        return []

    logger.debug('%s returned %s entries', subreddit, len(subreddit_posts))

    return subreddit_posts


def parse_posts(posts: List[dict]) -> List[RedditPost]:
    parsed_posts = list()

    for post in posts:
        parsed_post = RedditPost(post)
        if parsed_post.post_has_urls:
            parsed_posts.append(parsed_post)
    return parsed_posts


def run(reddit_config: dict, db_info: dict):
    subreddit_posts = get_subreddits(reddit_config)
    parsed_posts = parse_posts(subreddit_posts)

    if parsed_posts:
        db_full_path = Path(db_info['db_location']) / db_info['db_name']
        inserted_count = db_utils.db_insert(parsed_posts, db_full_path)
        logger.info('Inserted %s Reddit posts into DB', inserted_count)
    else:
        logger.info('No new Reddit posts found')

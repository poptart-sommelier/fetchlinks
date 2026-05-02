import requests
import logging
from pathlib import Path

# Custom libraries
from utils import RedditPost
import db_utils
import ingest_limits
import url_filters
from auth import RedditAuth

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_LISTING_LIMIT = 100
MAX_LISTING_LIMIT = 100
MAX_PAGES = 5


def _normalize_subreddit_name(subreddit: str) -> str:
    return subreddit.strip().removeprefix('r/').strip('/').lower()


def _post_fullname(post: dict) -> str:
    if not isinstance(post, dict):
        return ''
    post_data = post.get('data')
    if not isinstance(post_data, dict):
        return ''

    fullname = post_data.get('name')
    if isinstance(fullname, str) and fullname:
        return fullname
    post_id = post_data.get('id')
    if isinstance(post_id, str) and post_id:
        return f't3_{post_id}'
    return ''


def _listing_limit(reddit_config: dict) -> int:
    limit = int(reddit_config.get('listing_limit', DEFAULT_LISTING_LIMIT))
    return max(1, min(limit, MAX_LISTING_LIMIT))


def _max_pages(reddit_config: dict) -> int:
    return max(1, int(reddit_config.get('max_pages', MAX_PAGES)))


def _log_rate_limit(response):
    remaining = response.headers.get('X-Ratelimit-Remaining')
    reset = response.headers.get('X-Ratelimit-Reset')
    if remaining or reset:
        logger.debug('Reddit rate limit remaining=%s reset=%s', remaining, reset)


def get_subreddits(reddit_config: dict, db_path: Path) -> tuple[list[dict], list[tuple[str, str]]]:
    subreddit_posts = []
    state_updates = []
    reddit_states = db_utils.db_get_reddit_states(db_path)

    reddit_auth = RedditAuth(reddit_config['credential_location'])
    token = reddit_auth.get_auth()
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': reddit_auth.user_agent}
    limit = _listing_limit(reddit_config)
    max_pages = _max_pages(reddit_config)

    with requests.Session() as session:
        session.headers.update(headers)
        for subreddit in reddit_config['subreddits']:
            subreddit_name = _normalize_subreddit_name(subreddit)
            posts, newest_fullname = get_subreddit(
                session,
                subreddit_name,
                reddit_states.get(subreddit_name),
                limit=limit,
                max_pages=max_pages,
            )
            subreddit_posts.extend(posts)
            if newest_fullname:
                state_updates.append((subreddit_name, newest_fullname))

    return subreddit_posts, state_updates


def get_subreddit(
    session: requests.Session,
    subreddit: str,
    last_seen_fullname: str | None,
    limit: int = DEFAULT_LISTING_LIMIT,
    max_pages: int = MAX_PAGES,
) -> tuple[list[dict], str | None]:
    subreddit_url = f'https://oauth.reddit.com/r/{subreddit}/new/.json'
    fetched_posts = []
    newest_fullname = None
    after = None

    for page_num in range(1, max_pages + 1):
        params = {'show': 'all', 'limit': limit, 'raw_json': 1}
        if after:
            params['after'] = after

        try:
            response = session.get(
                url=subreddit_url,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            subreddit_resp = response.json()
        except ValueError as err:
            logger.error('Invalid JSON while retrieving r/%s: %s', subreddit, err)
            return fetched_posts, newest_fullname
        except requests.exceptions.HTTPError as errh:
            logger.error('HTTP error while retrieving r/%s: %s', subreddit, errh)
            return fetched_posts, newest_fullname
        except requests.exceptions.ConnectionError as errc:
            logger.error('Connection error while retrieving r/%s: %s', subreddit, errc)
            return fetched_posts, newest_fullname
        except requests.exceptions.Timeout as errt:
            logger.error('Timeout while retrieving r/%s: %s', subreddit, errt)
            return fetched_posts, newest_fullname
        except requests.exceptions.RequestException as err:
            logger.error('Request error while retrieving r/%s: %s', subreddit, err)
            return fetched_posts, newest_fullname

        _log_rate_limit(response)
        response_data = subreddit_resp.get('data') if isinstance(subreddit_resp.get('data'), dict) else {}
        subreddit_posts = response_data.get('children', [])
        if not isinstance(subreddit_posts, list):
            logger.error('Unexpected Reddit payload shape for r/%s', subreddit)
            return fetched_posts, newest_fullname

        if page_num == 1 and subreddit_posts:
            newest_fullname = _post_fullname(subreddit_posts[0]) or None

        logger.debug(
            'r/%s page %s/%s returned %s entries; after=%s',
            subreddit,
            page_num,
            max_pages,
            len(subreddit_posts),
            response_data.get('after'),
        )

        for post in subreddit_posts:
            if _post_fullname(post) == last_seen_fullname:
                return fetched_posts, newest_fullname
            fetched_posts.append(post)

        after = response_data.get('after')
        if not subreddit_posts or not after:
            break

    if last_seen_fullname and fetched_posts:
        logger.info('r/%s previous state %s was not reached in %s page(s)', subreddit, last_seen_fullname, max_pages)

    return fetched_posts, newest_fullname


def parse_posts(posts: list[dict]) -> list[RedditPost]:
    parsed_posts = []

    for post in posts:
        parsed_post = RedditPost(post)
        if parsed_post.post_has_urls:
            parsed_posts.append(parsed_post)
    return parsed_posts


def run(
    reddit_config: dict,
    db_info: dict,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
    excluded_url_host_keywords: list[str] | None = None,
):
    db_full_path = Path(db_info['db_location']) / db_info['db_name']
    subreddit_posts, state_updates = get_subreddits(reddit_config, db_full_path)
    parsed_posts = parse_posts(subreddit_posts)
    recent_posts = ingest_limits.filter_posts_by_age(parsed_posts, max_post_age_months, 'Reddit')
    recent_posts = url_filters.filter_posts_by_url_host_keywords(
        recent_posts,
        excluded_url_host_keywords or [],
        'Reddit',
    )

    if recent_posts:
        inserted_count = db_utils.db_insert(recent_posts, db_full_path)
        logger.info('Inserted %s Reddit posts into DB', inserted_count)
    else:
        logger.info('No new Reddit posts found')

    db_utils.db_set_reddit_states(state_updates, db_full_path)

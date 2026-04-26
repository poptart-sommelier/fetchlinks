"""RSS ingestion using requests + ETag/Last-Modified caching.

Improvements over the prior feedparser-only implementation:
- Uses requests with explicit timeouts so a single slow feed can't hang a worker.
- Sends If-None-Match / If-Modified-Since headers; 304 responses skip parsing.
- Per-feed state persisted in rss_feed_state table.
- Connection pooling via shared requests.Session.
- Hands raw bytes to feedparser.parse() (no second network round-trip).
"""
import concurrent.futures
import logging
from pathlib import Path

import feedparser
import requests

import db_utils
import ingest_limits
from utils import RssPost

logger = logging.getLogger(__name__)

THREADS = 50
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = 'fetchlinks-rss/0.1 (+https://github.com/poptart-sommelier/fetchlinks)'

# What we pass between fetch and parse:
#   (feed_url, parsed_feed_or_none, new_etag, new_last_modified, status_code)
FetchResult = tuple[str, feedparser.FeedParserDict | None, str, str, int]


def _fetch_one(
    session: requests.Session,
    url: str,
    cached: tuple[str, str],
) -> FetchResult:
    """Fetch a single feed using cached ETag/Last-Modified if present.

    Returns (url, feed_or_none, etag, last_modified, status_code).
    feed_or_none is None for 304/error cases (no parsing needed/possible).
    """
    cached_etag, cached_last_mod = cached
    headers = {}
    if cached_etag:
        headers['If-None-Match'] = cached_etag
    if cached_last_mod:
        headers['If-Modified-Since'] = cached_last_mod

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS, headers=headers)
    except requests.RequestException as exc:
        logger.warning('Failed to fetch %s: %s', url, type(exc).__name__)
        # Preserve cached values so we keep retrying with conditional headers.
        return (url, None, cached_etag, cached_last_mod, 0)

    new_etag = resp.headers.get('ETag', cached_etag)
    new_last_mod = resp.headers.get('Last-Modified', cached_last_mod)

    if resp.status_code == 304:
        logger.debug('Feed unchanged (304): %s', url)
        return (url, None, new_etag, new_last_mod, 304)

    if resp.status_code != 200:
        logger.warning('Feed %s returned HTTP %s', url, resp.status_code)
        return (url, None, new_etag, new_last_mod, resp.status_code)

    try:
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        logger.error('Failed to parse %s: %s', url, exc)
        return (url, None, new_etag, new_last_mod, 200)

    if feed.bozo and not feed.entries:
        logger.warning('Feed %s parse error with no entries: %s', url, feed.bozo_exception)
        return (url, None, new_etag, new_last_mod, 200)

    return (url, feed, new_etag, new_last_mod, 200)


def fetch_feeds(urls: list[str], cached_states: dict[str, tuple[str, str]]) -> list[FetchResult]:
    results: list[FetchResult] = []
    with requests.Session() as session:
        session.headers['User-Agent'] = USER_AGENT
        session.headers['Accept-Encoding'] = 'gzip, deflate'
        with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as pool:
            futures = [
                pool.submit(_fetch_one, session, url, cached_states.get(url, ('', '')))
                for url in urls
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    return results


def parse_posts(fetch_results: list[FetchResult]) -> list[RssPost]:
    posts: list[RssPost] = []
    for url, feed, _etag, _lm, _status in fetch_results:
        if feed is None:
            continue
        feed_meta = feed.feed if hasattr(feed, 'feed') else {}
        source = feed_meta.get('link') or url
        author = feed_meta.get('title') or source

        for post in feed.entries:
            try:
                parsed = RssPost(source, author, post)
            except Exception as exc:
                logger.warning('Skipping malformed entry from %s: %s', url, exc)
                continue
            if parsed.post_has_urls:
                posts.append(parsed)
    return posts


def run(
    rss_feed_links: list,
    db_info: dict,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
):
    db_full_path = Path(db_info['db_location']) / db_info['db_name']
    cached_states = db_utils.db_get_rss_feed_states(db_full_path)

    fetch_results = fetch_feeds(rss_feed_links, cached_states)

    state_rows = [(url, etag, lm, status) for (url, _f, etag, lm, status) in fetch_results]
    db_utils.db_set_rss_feed_states(state_rows, db_full_path)

    parsed_posts = parse_posts(fetch_results)
    recent_posts = ingest_limits.filter_posts_by_age(parsed_posts, max_post_age_months, 'RSS')

    counts = {200: 0, 304: 0, 'error': 0}
    for _u, _f, _e, _l, status in fetch_results:
        if status == 200:
            counts[200] += 1
        elif status == 304:
            counts[304] += 1
        else:
            counts['error'] += 1

    if recent_posts:
        inserted_count = db_utils.db_insert(recent_posts, db_full_path)
        logger.info(
            'RSS: %s feeds (200=%s, 304=%s, errors=%s); %s posts parsed, %s age-eligible, %s inserted',
            len(fetch_results), counts[200], counts[304], counts['error'],
            len(parsed_posts), len(recent_posts), inserted_count,
        )
    else:
        logger.info(
            'RSS: %s feeds (200=%s, 304=%s, errors=%s); %s posts parsed, no age-eligible new posts',
            len(fetch_results), counts[200], counts[304], counts['error'], len(parsed_posts),
        )

import feedparser
import concurrent.futures
import logging
from pathlib import Path
from typing import List

# Custom libraries.
from utils import RssPost
import db_utils

logger = logging.getLogger(__name__)

THREADS = 25


def get_feed(url: str) -> feedparser.FeedParserDict:
    logger.debug('Parsing: %s', url)

    try:
        feed = feedparser.parse(url)

        # Some feeds are malformed but still return usable entries.
        if feed.bozo:
            if len(feed.entries) == 0:
                logger.warning(
                    'Feed parser warning for %s: %s (entries=%s)',
                    url,
                    feed.bozo_exception,
                    len(feed.entries),
                )
            else:
                logger.debug(
                    'Non-fatal feed parser warning for %s: %s (entries=%s)',
                    url,
                    feed.bozo_exception,
                    len(feed.entries),
                )
        if len(feed.feed) == 0:
            logger.debug('Feed has no metadata: %s', url)

    except Exception as exc:
        logger.error('Failed to parse feed %s: %s', url, exc)
        return None

    return feed


def parse_posts(feeds: list) -> List[RssPost]:
    posts = list()
    for feed in feeds:
        source = feed.feed.get('link', '')
        author = feed.feed.get('title', '')
        if not source:
            source = getattr(feed, 'href', '')
        if not author:
            author = source or 'Unknown feed'

        if not source:
            logger.warning('Skipping RSS feed with missing source link')
            continue

        for post in feed.entries:
            parsed_post = RssPost(source, author, post)
            posts.append(parsed_post)

    return posts


def get_feeds(feeds: list) -> list:
    results = list()
    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(get_feed, feed) for feed in feeds]
        for future in concurrent.futures.as_completed(futures):
            if future.result() is not None:
                results.append(future.result())
    return results


def run(rss_feed_links: list, db_info: dict):
    fetched_feeds = get_feeds(rss_feed_links)
    parsed_posts = parse_posts(fetched_feeds)

    if parsed_posts:
        db_full_path = Path(db_info['db_location']) / db_info['db_name']
        inserted_count = db_utils.db_insert(parsed_posts, db_full_path)
        logger.info(
            'Parsed %s RSS posts, inserted %s new rows',
            len(parsed_posts),
            inserted_count,
        )
    else:
        logger.info('No new RSS posts found')

import feedparser
import concurrent.futures
import logging

# Custom libraries
from utils import RssPost
import db_utils

logger = logging.getLogger(__name__)

THREADS = 25


def get_feed(url):
    logging.debug(f'Parsing: {url}')
    feed = feedparser.parse(url)

    # Problems
    if feed.bozo:
        logger.error(f'Feedparser has issues with: {url}: {feed.bozo_exception}.\nReturned {len(feed.entries)} posts.')
    if len(feed.feed) == 0:
        logger.error(f'Feed has no contents: {url}')

    return feed


def parse_posts(feeds):
    posts = list()
    for feed in feeds:
        source = feed.feed['link']
        author = feed.feed['title']
        for post in feed.entries:
            parsed_post = RssPost(source, author, post)
            posts.append(parsed_post)

    return posts


def get_feeds(feeds):
    results = list()
    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(get_feed, feed) for feed in feeds]
        for future in concurrent.futures.as_completed(futures):
            if future.result() is not None:
                results.append(future.result())
    return results


def run(rss_feed_links, config):
    fetched_feeds = get_feeds(rss_feed_links)
    parsed_posts = parse_posts(fetched_feeds)

    if parsed_posts is not None:
        db_full_path = config['db_info']['db_location'] + config['db_info']['db_name']
        db_utils.db_insert(parsed_posts, db_full_path)
        logger.info(f'Inserted {len(parsed_posts)} Rss posts into DB')
    else:
        logging.info('No new Rss posts found')

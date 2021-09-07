import feedparser
import datetime
from dateutil.parser import *
import concurrent.futures

# Custom libraries
from utils import RssPost, build_hash

import logging

logger = logging.getLogger(__name__)

THREADS = 25


def parse_feed(url):
    logging.info(f'Parsing: {url}')
    feed = feedparser.parse(url)

    # Problematic return values
    if len(feed.feed) == 0:
        logger.error(f'Url is unresponsive:{url}')
        return None
    if feed.status == 304:
        logger.info(f'Url returned 304: {url}')
        return None

    # TODO: THIS SHOULD BE A CLASS RssPost()
    result_dict = build_dict_from_feed(feed)

    return result_dict


def convert_date_rss_to_mysql(rss_date):
    try:
        date_object = parse(rss_date)
        return datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error(e)
        logger.error('Could not parse date using dateutil: {}'.format(rss_date))
        return datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')


def build_dict_from_feed(feed):
    parsed_feed_entries_list = []

    for entry in feed.entries:
        parsed_rss_feed_data = RssPost()

        parsed_rss_feed_data.source = feed.feed['link']
        parsed_rss_feed_data.author = feed.feed['title']
        parsed_rss_feed_data.description = entry.title
        parsed_rss_feed_data.direct_link = None
        parsed_rss_feed_data.urls = [{'url': entry.link, 'unshort_url': None,
                                      'unique_id': build_hash(entry.link), 'unshort_unique_id': None}]

        if 'published' in entry:
            try:
                parsed_rss_feed_data.date_created = convert_date_rss_to_mysql(entry.published)
            except AttributeError as e:
                parsed_rss_feed_data.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
                logger.error(e)
                logger.error('Missing entry.published from {} - {}'.format(feed.feed['title'], entry.title))
        elif 'updated' in entry:
            try:
                parsed_rss_feed_data.date_created = convert_date_rss_to_mysql(entry.updated)
            except AttributeError as e:
                parsed_rss_feed_data.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
                logger.error(e)
                logger.error('Missing entry.updated from {} - {}'.format(feed.feed['title'], entry.title))
        else:
            parsed_rss_feed_data.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
            logger.error('Missing published/updated info, setting published date to NOW.\n{} - {}'.format(feed.feed['title'], entry.title))

        parsed_rss_feed_data.unique_id_string = ','.join(
            sorted([url['unique_id'] for url in parsed_rss_feed_data.urls]))

        parsed_feed_entries_list.append(parsed_rss_feed_data)

    return parsed_feed_entries_list


def get_feeds(feeds):
    results = list()
    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = [executor.submit(parse_feed, feed) for feed in feeds]
        for future in concurrent.futures.as_completed(futures):
            if future.result() is not None and len(future.result()) > 0:
                results.extend(future.result())
    return results


def main(config):
    if config.get('feeds', False):
        results = get_feeds(config.get('feeds'))
        logger.info('Returning {} entries.'.format(len(results)))
        return results
    else:
        logger.error('No RSS feeds to parse.')
        return []


if __name__ == '__main__':
    test_feeds = ['https://www.endgame.com/blog-rss.xml',
                  'https://isc.sans.edu/rssfeed.xml']

    fetched_results = main(test_feeds)

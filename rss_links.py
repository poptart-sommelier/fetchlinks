import feedparser
import multiprocessing
import itertools
import hashlib
import datetime
from dateutil.parser import *

# Importing Post class
import utils

import logging
logger = logging.getLogger(__name__)

THREADS = 10
# THIS SHOULD BE READ FROM A CONFIG


def parsefeed(url):
    logging.info(f'Parsing: {url}')
    feed = feedparser.parse(url, etag=None, modified=None)

    # Bad status or empty result i.e. feed was down
    if len(feed.feed) == 0:
        logger.error(f'Url is unresponsive:{url}')
        return None
    if feed.status == 304:
        logger.info(f'Url returned 304: {url}')
        return None

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
        parsed_rss_feed_data = utils.Post()

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


def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def main(config):
    pool = multiprocessing.Pool(processes=THREADS)

    results = pool.map(parsefeed, config['feeds'])

    # Strip any None values from the list
    results = filter(None, results)

    # results is a list of lists which all contain dictionaries.
    # we want one list with all the dicts, so we use itertools.chain.from_iterable to join/flatten all the lists
    processed_results = list(itertools.chain.from_iterable(results))

    logger.info('Returning {} entries.'.format(len(processed_results)))

    return processed_results


if __name__ == '__main__':
    fetched_results = main(['https://www.endgame.com/blog-rss.xml', 'https://isc.sans.edu/rssfeed.xml'])

    print()

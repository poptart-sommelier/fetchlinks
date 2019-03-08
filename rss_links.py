import feedparser
import multiprocessing
import itertools
import hashlib
import datetime

# Importing Datastructure class
import structure_data

import logging
logger = logging.getLogger(__name__)

# TODO: to ensure we don't pull data we already have, use etag and modified (see below code example)
# TODO: list of rss feeds should be stored in a config file
# TODO: dump all rss from feedly, create config file
# TODO: error handling
# TODO: proper logging

THREADS = 10
# THIS SHOULD BE READ FROM A CONFIG


def parsefeed(url):
    feed = feedparser.parse(url, etag=None, modified=None)

    if feed.status == 304:
        return None

    result_dict = build_dict_from_feed(feed)

    return result_dict


def convert_date_rss_to_mysql(rss_date):
    date_object = datetime.datetime.strptime(rss_date[0:25], '%a, %d %b %Y %H:%M:%S')
    return datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')


def build_dict_from_feed(feed):
    parsed_feed_entries_list = []

    for entry in feed.entries:
        parsed_rss_feed_data = structure_data.Datastructure()

        parsed_rss_feed_data.data_structure['source'] = 'rss'
        parsed_rss_feed_data.data_structure['author'] = feed.href
        parsed_rss_feed_data.data_structure['description'] = entry.title
        parsed_rss_feed_data.data_structure['direct_link'] = None
        parsed_rss_feed_data.data_structure['urls'] = [{'url': entry.link, 'unshort_url': None,
                                                        'unique_id': build_hash(entry.link), 'unshort_unique_id': None}]
        parsed_rss_feed_data.data_structure['date_created'] = convert_date_rss_to_mysql(entry.published)
        parsed_rss_feed_data.data_structure['unique_id'] = build_hash(''.join(
            sorted([url['url'] for url in parsed_rss_feed_data.data_structure['urls']])))

        parsed_feed_entries_list.append(parsed_rss_feed_data)

    return parsed_feed_entries_list


def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def main(config):
    pool = multiprocessing.Pool(processes=THREADS)

    results = pool.map(parsefeed, config['feeds'])

    # results is a list of lists which all contain dictionaries.
    # we want one list with all the dicts, so we use itertools.chain.from_iterable to join/flatten all the lists
    processed_results = list(itertools.chain.from_iterable(results))

    logger.info('Returning {} entries.'.format(len(processed_results)))

    return processed_results


if __name__ == '__main__':
    fetched_results = main(['https://www.endgame.com/blog-rss.xml', 'https://isc.sans.edu/rssfeed.xml'])

    print()

import feedparser
import json
import multiprocessing
import itertools
import hashlib
# import db_load

# Importing Datastructure class
# from structure_data import Datastructure

# TODO: to ensure we don't pull data we already have, use etag and modified (see below code example)
# TODO: list of rss feeds should be stored in a config file
# TODO: dump all rss from feedly, create config file
# TODO: error handling
# TODO: proper logging

THREADS = 10
# THIS SHOULD BE READ FROM A CONFIG
FEED_LIST = ['https://www.endgame.com/blog-rss.xml', 'https://isc.sans.edu/rssfeed.xml']


def go(rssfeedlist=FEED_LIST):
    pool = multiprocessing.Pool(processes=THREADS)

    results = pool.map(parsefeed, rssfeedlist)

    # results is a list of lists which all contain dictionaries.
    # we want one list with all the dicts, so we use itertools.chain.from_iterable to join/flatten all the lists
    processed_results = list(itertools.chain.from_iterable(results))

    return processed_results


def parsefeed(url):
    feed = feedparser.parse(url, etag=None, modified=None)

    print(feed.status)

    if feed.status == 304:
        return None

    result_dict = build_dict_from_feed(feed)

    return result_dict


def build_dict_from_feed(feed):
    feed_dict_list = []

    feed_dict = {
        'source': '',
        'author': '',
        'title': '',
        'description': '',
        'direct_link': '',
        'urls': [],
        'date_created': '',
        'unique_id': ''
    }

    for entry in feed.entries:
        feed_dict['source'] = 'RSS'
        feed_dict['author'] = feed.href,
        feed_dict['title'] = entry.title,
        feed_dict['urls'] = [entry.link],
        feed_dict['date_created'] = entry.published,
        feed_dict['unique_id'] = build_hash(entry.link)

        feed_dict_list.append(feed_dict)

    return feed_dict_list


def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


if __name__ == '__main__':
    fetched_results = go(FEED_LIST)

    print(json.dumps(fetched_results))
    print()

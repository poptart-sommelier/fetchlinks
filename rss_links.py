import feedparser
import json
import multiprocessing
import itertools
import hashlib

# TODO: design as module
# TODO: read DB unique guids and pull back list, then check against that list for all entries
# TODO: to ensure we don't pull data we already have, use etag and modified (see below code example)
# TODO: all state written to database
# TODO: should we be taking the first paragraph of the article or something? (matches with twitter...)
# TODO: dump all rss from feedly, create config file

THREADS = 10

def getfeeds(rssfeedlist):
    pool = multiprocessing.Pool(processes=THREADS)

    results = pool.map(parsefeed, rssfeedlist)

    return results


def get_last_status(url):
    # TODO: Read etag/lastmodified for url from file or database and return
    return None, None


def set_last_status(url, etag, modified):
    # store the etag and modified
    # last_etag = feed.etag
    # last_modified = feed.modified
    pass


def parsefeed(url):
    try:
        etag, modified = get_last_status(url)
    except:
        # TODO: write to log file - could not connect to DB
        etag = None
        modified = None

    feed = feedparser.parse(url, etag=etag, modified=modified)

    set_last_status(url, feed.etag, feed.modified)

    print(feed.status)

    if feed.status == 304:
        return None

    json_result = build_json_from_feed(feed)

    return json_result


def build_json_from_feed(feed):
    json_list = []

    for entry in feed.entries:
        f_dict = {
            'source': 'RSS',
            'author': feed.href,
            'title': entry.title,
            'url': entry.link,
            'date_created': entry.published,
            'unique_id': build_hash(entry.link)
        }

        json_list.append(f_dict)

    return json_list


def build_hash(link):
    hash = hashlib.sha256(link.encode())
    return hash.hexdigest()

# getfeeds() returns a list of lists which all contain json.
# we want one list with all the json, so we use itertools.chain.from_iterable to join/flatten all the lists
res = list(itertools.chain.from_iterable(getfeeds(['https://www.endgame.com/blog-rss.xml', 'https://isc.sans.edu/rssfeed.xml'])))

print(json.dumps(res))
print()

#!/usr/bin/python3.5

# TODO: This should be a method in utils.py, and should use async_io

import requests
import multiprocessing
import collections
import urllib3
import hashlib

import logging
logger = logging.getLogger(__name__)

THREADS = 10


def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def is_shortened(url):
    parsed_url = urllib3.util.parse_url(url)

    cntr = collections.Counter(parsed_url.path)

    url_whitelist = [
        'sans.org/u/',
        'tinyurl.com'
    ]

    if any(wlurl in url for wlurl in url_whitelist):
        return True
    if cntr['/'] > 1:
        return False
    if len(parsed_url.host) > 12:
        return False
    if parsed_url.path and len(parsed_url.path) > 12:
        return False
    if not parsed_url.path or parsed_url.path == '/':
        return False

    return True


def unshorten(post):
    for index, url in enumerate(post.urls):
        if is_shortened(url['url']):
            try:
                r = requests.get(url['url'])
            except Exception as e:
                logger.error(e)
                logger.error('Could not unshorten: {}'.format(url['url']))
                continue
            if r.status_code == 200:
                unshortened_url = r.url
                post.urls[index]['unshort_url'] = unshortened_url
                post.urls[index]['unshort_unique_id'] = build_hash(unshortened_url)
        else:
            continue

    return post


def unshorten_start(all_posts_dict_list):
    # parse tweet for urls key
    # go through each url and make sure it's not shortened.
    # if it is, unshorten it
    # replace that url with the unshortened url in the dictionary
    pool = multiprocessing.Pool(processes=THREADS)

    results = pool.map(unshorten, all_posts_dict_list)

    return results

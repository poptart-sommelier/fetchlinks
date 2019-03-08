#!/usr/bin/python3.5

import requests
import multiprocessing
import collections
import urllib3
import hashlib
import structure_data

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


def unshorten(tweet):
	for index, url in enumerate(tweet.data_structure['urls']):
		if is_shortened(url['url']):
			try:
				r = requests.get(url['url'])
				if r.status_code == 200:
					unshortened_url = r.url
					tweet.data_structure['urls'][index]['unshort_url'] = unshortened_url
					tweet.data_structure['urls'][index]['unshort_unique_id'] = build_hash(unshortened_url)
			except Exception as e:
				print(e)
				print("Url:" + url)
		else:
			continue

	return tweet


def unshorten_start(all_tweets_dict_list):
	# parse tweet for urls key
	# go through each url and make sure it's not shortened.
	# if it is, unshorten it
	# replace that url with the unshortened url in the dictionary
	pool = multiprocessing.Pool(processes=THREADS)

	results = pool.map(unshorten, all_tweets_dict_list)

	return results

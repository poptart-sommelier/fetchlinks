#!/usr/bin/python3.5

import requests
import sys
import multiprocessing
import collections
import urllib3

#   TODO:
#   test this to make sure that it follows both normal redirects (r.status_code OR r.history) as well as check BODY for any meta tag redirects/refreshes

THREADS = 10

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
	for index, url in enumerate(tweet['urls']):
		if is_shortened(url):
			r = requests.get(url)
			if r.status_code == 200:
				unshortened_url = r.url
				tweet['unshort_urls'][index] = unshortened_url
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


# we only want this to run if it is called directly off the command line
if __name__ == "__main__":
	if len(sys.argv) < 2:
		print("Please enter URL to unshorten")
		exit()
	else:
		try:
			unshorturlinfo = unshorten(sys.argv[1])
		except:
			print("Something went wrong...")
			pass

	print("Unshortened URL:")
	print(unshorturlinfo)
	print("")



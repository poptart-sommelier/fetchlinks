#!/usr/bin/python3.5

import requests
import sys
import queue
import threading
import time

#   references:
#   https://unshorten.it/api/documentation
#   http://docs.python-requests.org/en/master/user/quickstart/ (see redirect portion)
#   threading:
#   http://www.craigaddyman.com/python-queues-and-multi-threading/

#   TODO:
#   test this to make sure that it follows both normal redirects (r.status_code OR r.history) as well as check BODY for any meta tag redirects/refreshes

def unshorten(shorturl):
	# requests does all the work for us, following all redirects
	# TODO: THIS SHOULD BE THREADED
	r = requests.get(shorturl)
	return r.url

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



# TODO: PRIORITY:
# TODO: WHAT TO DO NOW
# TODO: #2 IF TWITTER HAS NO NEW LINKS, FAILS. FAIL GRACEFULLY ON ALL THREE MODULES WHEN NO RESULTS
# TODO: #3 LINK ENTRIES TO LINKS WITH UNIQUE KEY (HASH OF URL)
# TODO: #4 PREVENT DUPLICATE ENTRIES BY LOOKING UP UNIQUE HASH OF KEY FIRST

# TODO: CONFIG: all rss feeds to pull
# TODO: CLONE TWITTER ACCOUNT

# Standard libraries
import json
import os
import logging
import logging.config

# Custom libraries
import twitter_links
import reddit_links
import rss_links
import db_interact

DATA = 'data/'
APP_CONFIG_LOCATION = 'data/config/config.json'
LOG_CONFIG_LOCATION = 'data/config/log_config.json'
LOG_LOCATION = 'data/logs/fetch_links.log'

# Read DB for state from previous run - last twitter id, etc...

# Call each module, log object, and config data
# Write returned data to DB


def parse_config():
    with open(APP_CONFIG_LOCATION, 'r') as config_file:
        config = config_file.read()

    return json.loads(config)


def main():
    links = []

    # Sanity checks
    if not os.path.exists(DATA) or not os.path.exists(APP_CONFIG_LOCATION) or not os.path.exists(LOG_CONFIG_LOCATION):
        print('missing some or all of the following directories: \n'
              'data/, data/config/, data/logs/')
        exit()

    # load log_config.json file
    with open(LOG_CONFIG_LOCATION) as json_log_file:
        log_config = json.load(json_log_file)

    # configure logging
    logging.config.dictConfig(log_config['logging'])

    # get a logger
    logger = logging.getLogger(__name__)

    config = parse_config()

    links.extend(reddit_links.main(config['reddit']))
    # CHANGE THE API CALL LIMIT BELOW, SET TO 1 FOR TESTING
    links.extend(twitter_links.main(config['twitter'], 1))
    links.extend(rss_links.main(config['rss']))

    db_interact.db_insert(links)

    print()


main()

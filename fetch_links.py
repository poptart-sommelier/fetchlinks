# TODO: SERVER SECURITY (ENSURE ONLY KEYS FOR SSH, AUTOUPDATES)
# TODO: SET UP IN GOOGLE - CRON JOB USING CONFIG?
# TODO: LIMIT RESULTS (INDEX ON DATE? FILTER CRITERIA = DATE < 15 DAYS?
# TODO: FULLY DOCUMENT DEPLOYMENT IN SETUP.MD
# TODO: IMPLEMENT 'ignored_sources' IN CONFIG - non-infosec related feeds
# TODO: STATS - USER POSTS, MOST CLICKED, ETC...
# TODO: CUSTOM STREAM/TOP USER STREAM
# TODO: DB MAINTENANCE TASKS - stats, clean-up (drop older than XX days?), partitioning (by day?)

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

    tmp_result = reddit_links.main(config['reddit'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: reddit')

    # CHANGE THE API CALL LIMIT BELOW, SET TO 1 FOR TESTING
    tmp_result = twitter_links.main(config['twitter'], 15)
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: twitter')

    tmp_result = rss_links.main(config['rss'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: rss')

    db_interact.db_insert(links)


main()

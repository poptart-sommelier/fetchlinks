# TODO: CONVERT TO SQLite AND REMOVE MYSQL REQUIREMENT
# TODO: CREATE SETUP SCRIPT SO FULL DEPLOYMENT IS AUTOMATIC
# TODO: CONVERT FROM DICT TO CLASS PROPERTIES
# TODO: SCRIPT FULL DEPLOYMENT
# TODO: UPDATE REQUIREMENTS
# TODO: LAST LINKS FETCHED LOAD TIME ON MAIN PAGE
# TODO: SERVER SECURITY (ENSURE ONLY KEYS FOR SSH, AUTOUPDATES)
# TODO: UNSHORTENING ON REDDIT/RSS
# TODO: STRIP THE SHORTENED URL OUT OF THE TWEET (IN TWITTER DATA)
# TODO: TRUNCATE DB AFTER X-DAYS (30?)
# TODO: FOR SPECIFIC USERS, GRAB EVERYTHING THEY POST, NOT JUST IF IT HAS LINKS (SBOUASS, etc...)
# TODO: STATS - USER POSTS, MOST CLICKED, ETC...
# TODO: CUSTOM STREAM/TOP USER STREAM

# Standard libraries
import json
import logging
import logging.config
from pathlib import Path

# Custom libraries
# import twitter_links
import reddit_links
import rss_links
# import db_utils

DB_LOCATION = 'db/'
DB_NAME = 'fetchlinks.db'
APP_CONFIG_LOCATION = 'data/config/config.json'
LOG_CONFIG_LOCATION = 'data/config/log_config.json'
LOG_LOCATION = 'data/logs/fetch_links.log'


def parse_config():
    with open(APP_CONFIG_LOCATION, 'r') as config_file:
        config = config_file.read()

    return json.loads(config)


def sanity_check():
    if not Path(LOG_CONFIG_LOCATION).exists() or not Path(APP_CONFIG_LOCATION).exists():
        print(f'Missing config files: \n{LOG_CONFIG_LOCATION}\n{APP_CONFIG_LOCATION}\n Cannot continue. Exiting.')
        exit()

    if not Path(LOG_LOCATION).exists():
        Path(LOG_LOCATION).parent.mkdir(parents=True, exist_ok=True)

    # if not Path(DB).exists():
    #     db_utils.db_setup()


def configure_logging():
    # load log_config.json file
    with open(LOG_CONFIG_LOCATION) as json_log_file:
        log_config = json.load(json_log_file)
    # configure logging
    logging.config.dictConfig(log_config['logging'])
    # get a logger
    logger = logging.getLogger(__name__)
    return logger


def main():
    links = []

    # Sanity checks
    sanity_check()

    # Log setup
    logger = configure_logging()

    # Config setup
    config = parse_config()

    # tmp_result = reddit_links.main(config['reddit'])
    # if tmp_result is not None:
    #     links.extend(tmp_result)
    # else:
    #     logger.info('No results returned from: reddit')

    tmp_result = rss_links.main(config['rss'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: rss')

    # CHANGE THE API CALL LIMIT BELOW, SET TO 1 FOR TESTING
    # tmp_result = twitter_links.main(config['twitter'], 15)
    # if tmp_result is not None:
    #     links.extend(tmp_result)
    # else:
    #     logger.info('No results returned from: twitter')

    #
    # db_utils.db_insert(links)


main()

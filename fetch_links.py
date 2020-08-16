# TODO: CONVERT FLASK TO USE SQLITE3

# TODO: CREATE SETUP SCRIPT SO FULL DEPLOYMENT IS AUTOMATIC
# TODO: UPDATE REQUIREMENTS
# TODO: LAST LINKS FETCHED LOAD TIME ON MAIN PAGE
# TODO: SERVER SECURITY (ENSURE ONLY KEYS FOR SSH, AUTOUPDATES)
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
import twitter_links
import reddit_links
import rss_links
import db_utils
import db_setup

DB_LOCATION = 'db/'
DB_NAME = 'fetchlinks.db'
APP_CONFIG_LOCATION = 'data/config/config.json'
LOG_CONFIG_LOCATION = 'data/config/log_config.json'
LOG_LOCATION = 'data/logs/fetch_links.log'


def configure_logging():
    # load log_config.json file
    with open(LOG_CONFIG_LOCATION) as json_log_file:
        log_config = json.load(json_log_file)
    # configure logging
    logging.config.dictConfig(log_config['logging'])
    # get a logger
    logger = logging.getLogger(__name__)
    return logger


# Log setup
logger = configure_logging()


def parse_config():
    with open(APP_CONFIG_LOCATION, 'r') as config_file:
        config = config_file.read()

    return json.loads(config)


def sanity_check(config):
    if not Path(LOG_CONFIG_LOCATION).exists() or not Path(APP_CONFIG_LOCATION).exists():
        print(f'Missing config files: \n{LOG_CONFIG_LOCATION}\n{APP_CONFIG_LOCATION}\n Cannot continue. Exiting.')
        exit()

    if not Path(LOG_LOCATION).exists():
        Path(LOG_LOCATION).parent.mkdir(parents=True, exist_ok=True)

    if not Path(config['db_info']['db_full_path']).exists():
        logger.info(f'DB does not exist. Creating one: {config["db_info"]["db_full_path"]}')
        db_setup.db_initial_setup(config['db_info']['db_location'], config['db_info']['db_name'])


def main():
    links = []

    # Config setup
    config = parse_config()

    # Sanity checks
    sanity_check(config)

    tmp_result = reddit_links.main(config['reddit'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: reddit')

    tmp_result = rss_links.main(config['rss'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: rss')

    # TODO: CHANGE THIS BACK TO 15!
    # CHANGE THE API CALL LIMIT BELOW, SET TO LOW NUMBER FOR TESTING, 15 FOR PROD
    tmp_result = twitter_links.main(config['twitter'], config['db_info'], api_calls_limit=15)
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logger.info('No results returned from: twitter')

    db_utils.db_insert(links, config['db_info']['db_full_path'])


if __name__ == '__main__':
    main()

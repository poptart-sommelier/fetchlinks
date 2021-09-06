# TODO: CREATE TESTS

# TODO: CREATE SETUP SCRIPT SO FULL DEPLOYMENT IS AUTOMATIC
# TODO: UPDATE REQUIREMENTS
# TODO: LAST LINKS FETCHED LOAD TIME ON MAIN PAGE
# TODO: SERVER SECURITY (ENSURE ONLY KEYS FOR SSH, AUTOUPDATES)
# TODO: TRUNCATE DB AFTER X-DAYS (30?)
# TODO: FOR SPECIFIC USERS, GRAB EVERYTHING THEY POST, NOT JUST IF IT HAS LINKS (SBOUASS, etc...)
# TODO: BETTER LOG CONFIG

# Standard libraries
import json
import logging
import logging.config
from pathlib import Path
import argparse

# Custom libraries
import twitter_links
import reddit_links
import rss_links
import db_utils
import db_setup

# TODO: IMPROVE LOGGING SETUP
LOG_CONFIG_LOCATION = 'data/config/log_config.json'


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


def parse_config(app_config_location):
    if Path(app_config_location).exists():
        with open(app_config_location, 'r') as config_file:
            config = config_file.read()

        return json.loads(config)
    else:
        raise FileNotFoundError('Config file does not exist.')


def initial_setup(config):
    if not Path(config.log_location).exists():
        Path(config.log_location).parent.mkdir(parents=True, exist_ok=True)

    if not Path(config['db_info']['db_full_path']).exists():
        logger.info(f'DB does not exist. Creating one: {config["db_info"]["db_full_path"]}')
        db_setup.db_initial_setup(config['db_info']['db_location'], config['db_info']['db_name'])


def fetch_links(config):
    links = list()

    # TODO: PROVIDE LIST OF DATA SOURCES, READ THROUGH THEM
    # TODO: Spin up threads to run these in parallel
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


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-config', help='Config location', type=str)
    parser.add_argument('-log_level', help='Set logging verbosity', type=str)

    return parser.parse_args()


def main():
    # Parse arguments
    args = parse_arguments()

    # Config setup
    config = parse_config(args.config)

    # Sanity checks
    initial_setup(config)

    # Actually do stuff
    fetch_links(config)


if __name__ == '__main__':
    main()

# TODO: CREATE TESTS
#  CREATE SETUP SCRIPT SO FULL DEPLOYMENT IS AUTOMATIC
#  UPDATE REQUIREMENTS
#  LAST LINKS FETCHED LOAD TIME ON MAIN PAGE
#  SERVER SECURITY (ENSURE ONLY KEYS FOR SSH, AUTOUPDATES)
#  TRUNCATE DB AFTER X-DAYS (30?)
#  FOR SPECIFIC USERS, GRAB EVERYTHING THEY POST, NOT JUST IF IT HAS LINKS (SBOUASS, etc...)
#  BETTER LOG CONFIG
#  DOCUMENT CREATION OF API CREDS FOR TWITTER/REDDIT

# Standard libraries
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Custom libraries
import twitter_links
import reddit_links
import rss_links
import db_utils
import startup_and_validate


def configure_logging(config):
    logging_levels = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}

    log_path = Path(config['log_info']['log_location'])
    log_level = logging_levels[config['log_info'].get('log_level', 20)]
    logging.basicConfig(handlers=[RotatingFileHandler(log_path, maxBytes=1000000, backupCount=5, encoding="utf8"),
                                  logging.StreamHandler()],
                        level=log_level,
                        format="%(asctime)s (%(module)s) %(levelname)s - %(message)s",
                        datefmt="%d/%m/%Y %I:%M:%S %p")


def fetch_links(config):
    links = list()

    # TODO: PROVIDE LIST OF DATA SOURCES, READ THROUGH THEM
    # TODO: Spin up threads to run these in parallel
    tmp_result = reddit_links.main(config['reddit'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logging.info('No results returned from: reddit')
    tmp_result = rss_links.main(config['rss'])
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logging.info('No results returned from: rss')
    # TODO: CHANGE THIS BACK TO 15!
    # CHANGE THE API CALL LIMIT BELOW, SET TO LOW NUMBER FOR TESTING, 15 FOR PROD
    tmp_result = twitter_links.main(config['twitter'], config['db_info'], api_calls_limit=15)
    if tmp_result is not None:
        links.extend(tmp_result)
    else:
        logging.info('No results returned from: twitter')
    db_utils.db_insert(links, config['db_info']['db_full_path'])


def main():
    # Sanity checks
    config = startup_and_validate.do_startup()

    # Setup logging
    configure_logging(config)

    # Actually do stuff
    fetch_links(config)


if __name__ == '__main__':
    main()

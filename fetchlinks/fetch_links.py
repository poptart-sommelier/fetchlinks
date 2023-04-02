# Standard libraries
import logging
from logging import StreamHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Custom libraries
import rss_links
import reddit_links
import twitter_links
import startup_and_validate


def configure_logging(config):
    logging_levels = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}

    log_path = Path(config['log_info']['log_location'])
    log_level = logging_levels[config['log_info'].get('log_level', 20)]
    logging.basicConfig(handlers=[RotatingFileHandler(log_path, maxBytes=1000000, backupCount=5, encoding="utf8"),
                                  StreamHandler()],
                        level=log_level,
                        format="%(asctime)s (%(module)s) %(levelname)s - %(message)s",
                        datefmt="%d/%m/%Y %I:%M:%S %p")


def fetch_links(config: dict, sources: dict):
    """
    Call all our fetch_links modules
    :param config: app config info, mainly database stuff
    :param sources: rss links, subreddits, etc...
    :return: Nothing
    """
    rss_links.run(sources['rss']['feeds'], config['db_info'])
    reddit_links.run(sources['reddit'], config['db_info'])
    # twitter now charges for API access
    # twitter_links.run(sources['twitter'], config['db_info'])


def main():
    # Sanity checks
    config, sources = startup_and_validate.do_startup()

    # Setup logging
    configure_logging(config)

    # Actually do stuff
    fetch_links(config, sources)


if __name__ == '__main__':
    main()

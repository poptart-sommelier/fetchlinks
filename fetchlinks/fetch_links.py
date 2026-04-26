# Standard libraries
import logging
from logging import StreamHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Custom libraries
import rss_links
import reddit_links
import bluesky_links
import startup_and_validate


def configure_logging(config):
    logging_levels = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}

    log_path = Path(config['log_info']['log_location'])
    log_level_name = str(config['log_info'].get('log_level', 'INFO')).upper()
    log_level = logging_levels.get(log_level_name, logging.INFO)
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
    if sources.get('rss', {}).get('enabled', True):
        rss_links.run(sources['rss']['feeds'], config['db_info'])

    if sources.get('reddit', {}).get('enabled', True):
        reddit_links.run(sources['reddit'], config['db_info'])

    if sources.get('bluesky', {}).get('enabled', False):
        bluesky_links.run(sources['bluesky'], config['db_info'])


def main():
    try:
        # Parse args + config first so we know where to log to.
        args = startup_and_validate.parse_arguments()
        config = startup_and_validate.parse_config(args.config)

        # Setup logging BEFORE further validation so any errors below
        # (e.g. bad sources file, missing credentials) hit the log file.
        configure_logging(config)

        # Validate sources now that logging is up.
        sources = startup_and_validate.parse_sources(args.sources)

        # Actually do stuff
        fetch_links(config, sources)
    except Exception as exc:
        logging.exception('Fetch links failed: %s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()

# Standard libraries
import logging
from logging import StreamHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Custom libraries
import rss_links
import reddit_links
import bluesky_links
import mastodon_links
import db_setup
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
    rss_config = sources.get('rss')
    if rss_config and rss_config.get('enabled', True):
        rss_links.run(rss_config['feeds'], config['db_info'])

    reddit_config = sources.get('reddit')
    if reddit_config and reddit_config.get('enabled', True):
        reddit_links.run(reddit_config, config['db_info'])

    bluesky_config = sources.get('bluesky')
    if bluesky_config and bluesky_config.get('enabled', False):
        bluesky_links.run(bluesky_config, config['db_info'])

    mastodon_config = sources.get('mastodon')
    if mastodon_config and mastodon_config.get('enabled', False):
        mastodon_links.run(mastodon_config, config['db_info'])


def main():
    try:
        # Parse args + config first so we know where to log to.
        args = startup_and_validate.parse_arguments()
        config = startup_and_validate.parse_config(args.config)

        # Setup logging BEFORE further validation so any errors below
        # (e.g. bad sources file, missing credentials) hit the log file.
        configure_logging(config)

        # Ensure DB schema exists. db_initial_setup is idempotent
        # (CREATE TABLE IF NOT EXISTS), so it's safe to run every time
        # and means new tables added later get created automatically.
        db_setup.db_initial_setup(config['db_info']['db_location'], config['db_info']['db_name'])

        # Validate sources now that logging is up.
        sources = startup_and_validate.parse_sources(args.sources)

        # Actually do stuff
        fetch_links(config, sources)
    except Exception as exc:
        logging.exception('Fetch links failed: %s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()

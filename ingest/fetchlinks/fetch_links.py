# Standard libraries
import logging
from logging import StreamHandler
from logging.handlers import RotatingFileHandler

# Custom libraries
import rss_links
import reddit_links
import bluesky_links
import mastodon_links
import db_setup
import config as app_config


_LOG_LEVEL_VALUES = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}


def configure_logging(cfg: app_config.AppConfig) -> None:
    log_level = _LOG_LEVEL_VALUES.get(cfg.paths.log_level, logging.INFO)
    logging.basicConfig(
        handlers=[
            RotatingFileHandler(cfg.paths.log_file, maxBytes=1_000_000, backupCount=5, encoding="utf8"),
            StreamHandler(),
        ],
        level=log_level,
        format="%(asctime)s (%(module)s) %(levelname)s - %(message)s",
        datefmt="%d/%m/%Y %I:%M:%S %p",
    )


def fetch_links(cfg: app_config.AppConfig) -> None:
    """Run every enabled ingest source."""
    db_path = cfg.paths.db
    max_age = cfg.ingest.max_post_age_months
    host_kw = list(cfg.ingest.excluded_url_host_keywords)
    desc_kw = list(cfg.ingest.excluded_url_or_description_keywords)

    if cfg.sources.rss and cfg.sources.rss.enabled:
        rss_links.run(cfg.sources.rss, db_path, max_age, host_kw, desc_kw)

    if cfg.sources.reddit and cfg.sources.reddit.enabled:
        reddit_links.run(cfg.sources.reddit, db_path, max_age, host_kw, desc_kw)

    if cfg.sources.bluesky and cfg.sources.bluesky.enabled:
        bluesky_links.run(cfg.sources.bluesky, db_path, max_age, host_kw, desc_kw)

    if cfg.sources.mastodon and cfg.sources.mastodon.enabled:
        mastodon_links.run(cfg.sources.mastodon, db_path, max_age, host_kw, desc_kw)


def main() -> None:
    try:
        args = app_config.parse_arguments()
        cfg = app_config.load_config(args.config)

        # Set up logging before doing anything else so failures get logged.
        configure_logging(cfg)

        # Idempotent schema setup.
        db_setup.db_initial_setup(cfg.paths.db)

        fetch_links(cfg)
    except Exception as exc:
        logging.exception('Fetch links failed: %s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()

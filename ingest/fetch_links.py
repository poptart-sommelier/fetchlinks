"""Collect from every enabled source and queue one batch.

This is the collector entry point. It reads configuration, credentials, the
catalog snapshot, and its own resume state -- all local files -- and produces a
single validated batch in the spool. It opens no database and holds no database
credentials, which is what allows it to keep running on the Raspberry Pi from a
residential connection while the destination lives somewhere else entirely.

The whole cycle is one batch on purpose. If Mastodon fails halfway through,
nothing at all is queued and nothing at all is checkpointed, so the next run
repeats the cycle rather than resuming from a position that only some sources
reached.
"""

# Standard libraries
import logging
import os
from logging import StreamHandler
from logging.handlers import RotatingFileHandler

# Custom libraries
import rss_links
import reddit_links
import bluesky_links
import mastodon_links
import config as app_config
from pipeline.catalog import Catalog
from pipeline.collection import CollectionResult
from pipeline.layout import RuntimeLayout

logger = logging.getLogger(__name__)

_LOG_LEVEL_VALUES = {"CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10}

COLLECTOR_VERSION = 'fetchlinks-collector/1'
COLLECTOR_COMMIT_ENV = 'FETCHLINKS_COLLECTOR_COMMIT'


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


def collect(cfg: app_config.AppConfig, catalog, state) -> CollectionResult:
    """Run every enabled source and return everything they produced."""
    max_age = cfg.ingest.max_post_age_months
    host_kw = list(cfg.ingest.excluded_url_host_keywords)
    desc_kw = list(cfg.ingest.excluded_url_or_description_keywords)

    result = CollectionResult()

    if cfg.sources.rss and cfg.sources.rss.enabled:
        result.extend(rss_links.run(cfg.sources.rss, catalog, state,
                                    max_age, host_kw, desc_kw))

    if cfg.sources.reddit and cfg.sources.reddit.enabled:
        result.extend(reddit_links.run(cfg.sources.reddit, catalog, state,
                                       max_age, host_kw, desc_kw))

    if cfg.sources.bluesky and cfg.sources.bluesky.enabled:
        result.extend(bluesky_links.run(cfg.sources.bluesky, state,
                                        max_age, host_kw, desc_kw))
        result.extend(bluesky_links.sync_follows(cfg.sources.bluesky))

    if cfg.sources.mastodon and cfg.sources.mastodon.enabled:
        result.extend(mastodon_links.run(cfg.sources.mastodon, state,
                                         max_age, host_kw, desc_kw))
        result.extend(mastodon_links.sync_follows(cfg.sources.mastodon))

    return result


def advance_state(state, catalog, result: CollectionResult) -> None:
    """Move the collector's resume position forward after a batch is queued.

    Deliberately last. State is what stops the next run re-reading the same
    posts, so advancing it before the batch is durable would trade duplicate
    work -- which the publisher removes -- for lost posts, which nothing can
    recover.
    """
    state.apply_rss_observations(result.rss_observations)
    state.apply_checkpoints(result.checkpoints)

    # Drop resume data for sources that left the catalog, so an unsubscribed
    # feed does not keep its entry forever.
    state.retain_feeds(catalog.normalized_feed_urls)
    state.retain_streams(reddit_links.CHECKPOINT_SOURCE_TYPE,
                         catalog.normalized_subreddit_names)


def collect_once(cfg: app_config.AppConfig) -> str | None:
    """Run one collection cycle. Returns the queued batch id, or None."""
    layout = RuntimeLayout.resolve(cfg.paths.runtime_dir)
    layout.initialize()
    logger.info('Collecting into %s', layout.root)

    catalog = Catalog.load(layout.catalog_path)
    logger.info('Catalog %s: %s feeds, %s subreddits',
                catalog.revision[:12], len(catalog.feeds), len(catalog.subreddits))

    state = layout.load_state()
    result = collect(cfg, catalog, state)
    logger.info('Collected %s', result.summary())

    with layout.spool().new_batch(
        collector_version=COLLECTOR_VERSION,
        collector_commit=os.environ.get(COLLECTOR_COMMIT_ENV) or None,
        catalog_revision=catalog.revision,
    ) as batch:
        result.write_to(batch)

    if batch.discarded:
        logger.info('Nothing collected this cycle; no batch queued')
        return None

    advance_state(state, catalog, result)
    layout.save_state(state)
    return batch.batch_id


def main() -> None:
    try:
        args = app_config.parse_arguments()
        cfg = app_config.load_config(args.config)

        # Set up logging before doing anything else so failures get logged.
        configure_logging(cfg)

        collect_once(cfg)
    except Exception as exc:
        logging.exception('Collection failed: %s', exc)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()

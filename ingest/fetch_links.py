"""Collect from every enabled source and queue one batch.

This is the collector entry point. It reads configuration, credentials, the
catalog snapshot, and its own resume state -- all local files -- and produces a
single validated batch in the spool. It opens no database and holds no database
credentials, which is what allows it to keep running on the Raspberry Pi from a
residential connection while the destination lives somewhere else entirely.

The whole cycle is one batch on purpose: every source's records are merged and
written once, and resume state advances only after that batch is durable. A
source that fails is skipped rather than fatal -- it simply contributes nothing
and keeps its old resume position, so the next run retries it -- but a failure
partway through writing the batch queues nothing and checkpoints nothing.

Every cycle queues a batch, including one that collected nothing, because each
batch also carries a record of the run that produced it. That is what lets the
site distinguish a collector that has stopped from a genuinely quiet hour: no
run record is the symptom of the former, and the two used to look identical.
"""

# Standard libraries
import logging
import os
import time
from logging import StreamHandler
from logging.handlers import RotatingFileHandler

# Custom libraries
import rss_links
import reddit_links
import bluesky_links
import mastodon_links
import error_kinds
import config as app_config
from pipeline.catalog import Catalog
from pipeline.collection import CollectionResult, Subtask
from pipeline.contract import (
    ERROR_KIND_NONE,
    RESULT_FAILED,
    RESULT_OK,
    CollectionRunRecord,
    overall_result,
    utc_now,
)
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


def _collect_source(result: CollectionResult, name: str, produce) -> None:
    """Run one source, containing a failure to that source alone.

    Sources hold independent resume positions, so one unreachable API is no
    reason to throw away what the others just fetched. Before this, an
    exception anywhere aborted the cycle: a network outage that broke the
    Reddit login discarded 700-odd feeds' worth of work every half hour for
    thirteen hours, and left no batch behind to show for it.
    """
    started = time.monotonic()
    try:
        result.extend(produce())
    except Exception as exc:
        logger.exception('Source %s failed; continuing without it: %s', name, exc)
        result.record_failure(name)
        # A source that leaves by raising never reached its own counting, so
        # the failure is recorded here instead. Without this a login that
        # never completes would report zero of zero channels and look quiet.
        result.tally(name).fault(error_kinds.from_exception(exc),
                                 error_kinds.describe(exc))
    finally:
        result.tally(name).elapsed_ms += _elapsed_ms(started)


def _elapsed_ms(started: float) -> int:
    # Monotonic, so a clock correction mid-run cannot produce a negative or
    # wildly long duration.
    return int((time.monotonic() - started) * 1000)


def _collect_subtask(result: CollectionResult, name: str, scope: str, produce) -> None:
    """Run work that sits beside a source and can fail without failing it."""
    started = time.monotonic()
    try:
        result.extend(produce())
    except Exception as exc:
        logger.exception('Subtask %s failed; continuing without it: %s', name, exc)
        result.record_subtask(Subtask(
            name=name,
            scope=scope,
            result=RESULT_FAILED,
            elapsed_ms=_elapsed_ms(started),
            error_kind=error_kinds.from_exception(exc),
            error_message=error_kinds.describe(exc),
        ))
        return
    result.record_subtask(Subtask(
        name=name, scope=scope, result=RESULT_OK, elapsed_ms=_elapsed_ms(started)))


def collect(cfg: app_config.AppConfig, catalog, state) -> CollectionResult:
    """Run every enabled source and return everything they produced."""
    max_age = cfg.ingest.max_post_age_months
    host_kw = list(cfg.ingest.excluded_url_host_keywords)
    desc_kw = list(cfg.ingest.excluded_url_or_description_keywords)

    result = CollectionResult()

    def attempt(name, produce):
        result.record_attempt(name)
        _collect_source(result, name, produce)

    def skip(name):
        # Recorded rather than omitted, so a source that was switched off
        # reads as off instead of vanishing from the report and looking like
        # a source that silently stopped working.
        result.tally(name).skipped = True

    if cfg.sources.rss and cfg.sources.rss.enabled:
        attempt('rss', lambda: rss_links.run(cfg.sources.rss, catalog, state,
                                             max_age, host_kw, desc_kw))
    else:
        skip('rss')

    if cfg.sources.reddit and cfg.sources.reddit.enabled:
        attempt('reddit', lambda: reddit_links.run(cfg.sources.reddit, catalog, state,
                                                   max_age, host_kw, desc_kw))
    else:
        skip('reddit')

    if cfg.sources.bluesky and cfg.sources.bluesky.enabled:
        attempt('bluesky', lambda: bluesky_links.run(cfg.sources.bluesky, state,
                                                     max_age, host_kw, desc_kw))
        _collect_subtask(result, 'follows', 'bluesky',
                         lambda: bluesky_links.sync_follows(cfg.sources.bluesky))
    else:
        skip('bluesky')

    if cfg.sources.mastodon and cfg.sources.mastodon.enabled:
        attempt('mastodon', lambda: mastodon_links.run(cfg.sources.mastodon, state,
                                                       max_age, host_kw, desc_kw))
        _collect_subtask(result, 'follows', 'mastodon',
                         lambda: mastodon_links.sync_follows(cfg.sources.mastodon))
    else:
        skip('mastodon')

    return result


def build_run_record(result: CollectionResult, started_at: str, finished_at: str,
                     elapsed_ms: int) -> CollectionRunRecord:
    """Turn a finished cycle into the single record that describes it."""
    sources = tuple(
        tally.to_report()
        for _name, tally in sorted(result.tallies.items())
    )
    subtasks = tuple(subtask.to_report() for subtask in result.subtasks)
    run_result = overall_result([report.result for report in sources])

    error_kind = ERROR_KIND_NONE
    error_message = ''
    for report in sources:
        if report.error_kind:
            error_kind = report.error_kind
            error_message = report.error_message
            break

    return CollectionRunRecord(
        started_at=started_at,
        finished_at=finished_at,
        elapsed_ms=max(0, int(elapsed_ms)),
        result=run_result,
        posts_collected=len(result.posts),
        error_kind=error_kind,
        error_message=error_message,
        sources=sources,
        subtasks=subtasks,
    )


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
    started_at = utc_now()
    started = time.monotonic()
    result = collect(cfg, catalog, state)
    run = build_run_record(result, started_at, utc_now(), _elapsed_ms(started))
    logger.info('Collected %s', result.summary())

    # The run record goes in the same batch as the content, which is what makes
    # a bad cycle survivable: the report of the failure travels the same
    # durable path as the posts, so a database that is down loses neither.
    with layout.spool().new_batch(
        collector_version=COLLECTOR_VERSION,
        collector_commit=os.environ.get(COLLECTOR_COMMIT_ENV) or None,
        catalog_revision=catalog.revision,
    ) as batch:
        result.write_to(batch)
        batch.set_collection_run(run)

    # Everything failing is a different animal from one source failing, and the
    # usual cause is the machine itself having no network. Raise so the unit
    # exits non-zero and the run is visibly bad, rather than reporting a
    # successful cycle that happened to collect nothing. Deliberately after the
    # batch is queued: the summary explaining the failure is the one thing
    # worth keeping from a run like this, so it must be durable first.
    if result.every_source_failed:
        raise RuntimeError(
            'Every enabled source failed: ' + ', '.join(result.failed_sources)
        )

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

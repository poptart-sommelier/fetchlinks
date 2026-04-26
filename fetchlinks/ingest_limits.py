import logging
from datetime import UTC, datetime

import dateutil.parser
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

DEFAULT_MAX_POST_AGE_MONTHS = 3


def max_post_age_months_from_sources(sources: dict) -> int:
    ingest_config = sources.get('ingest', {})
    if not isinstance(ingest_config, dict):
        return DEFAULT_MAX_POST_AGE_MONTHS
    return ingest_config.get('max_post_age_months', DEFAULT_MAX_POST_AGE_MONTHS)


def _parse_post_datetime(date_value: str) -> datetime | None:
    if not date_value:
        return None

    try:
        parsed = dateutil.parser.parse(date_value)
    except (TypeError, ValueError, OverflowError) as exc:
        logger.warning('Could not parse post date for ingest age limit: %s', exc)
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def post_is_within_age_limit(post, max_post_age_months: int, now: datetime | None = None) -> bool:
    post_dt = _parse_post_datetime(getattr(post, 'date_created', ''))
    if post_dt is None:
        return True

    now = now or datetime.now(UTC)
    cutoff = now - relativedelta(months=max_post_age_months)
    return post_dt >= cutoff


def filter_posts_by_age(
    posts: list,
    max_post_age_months: int,
    source_name: str,
    now: datetime | None = None,
) -> list:
    recent_posts = []
    skipped_count = 0
    for post in posts:
        if post_is_within_age_limit(post, max_post_age_months, now):
            recent_posts.append(post)
        else:
            skipped_count += 1

    if skipped_count:
        logger.info(
            'Skipped %s %s posts older than %s month(s)',
            skipped_count,
            source_name,
            max_post_age_months,
        )
    return recent_posts
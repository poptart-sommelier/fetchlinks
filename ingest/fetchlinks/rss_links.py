"""RSS ingestion using requests + ETag/Last-Modified caching.

- Active feeds are read from the ``rss_feeds`` DB table (enabled = 1 and
  not tombstoned). The seed file is only consulted at first bootstrap by
  ``rss_feed_import.py --seed-if-empty``.
- Uses requests with explicit timeouts so a single slow feed can't hang a worker.
- Sends If-None-Match / If-Modified-Since headers; 304 responses skip parsing.
- Per-feed health (etag, last_modified, last_status, consecutive_failures)
  is persisted back into ``rss_feeds``; feeds whose failure count crosses
  ``auto_disable_after_failures`` are auto-disabled.
- Connection pooling via shared requests.Session.
- Hands raw bytes to feedparser.parse() (no second network round-trip).
"""
import concurrent.futures
import logging
from pathlib import Path

import feedparser
import requests

import db_utils
import ingest_limits
import url_filters
from utils import RssPost

logger = logging.getLogger(__name__)

THREADS = 50
USER_AGENT = 'fetchlinks-rss/0.1 (+https://github.com/poptart-sommelier/fetchlinks)'

# What we pass between fetch and parse:
#   (feed_id, feed_url, parsed_feed_or_none, new_etag, new_last_modified,
#    status_code, error_message_or_none)
FetchResult = tuple[
    int, str, feedparser.FeedParserDict | None, str, str, int, str | None,
]


def _fetch_one(
    session: requests.Session,
    feed_id: int,
    url: str,
    cached_etag: str,
    cached_last_mod: str,
    timeout: int,
) -> FetchResult:
    """Fetch a single feed using cached ETag/Last-Modified if present.

    Returns ``(feed_id, url, feed_or_none, etag, last_modified, status, error)``.
    ``feed_or_none`` is None for 304 / error cases (no parsing needed/possible).
    ``error`` is None on 200/304 and a short label otherwise.
    """
    headers = {}
    if cached_etag:
        headers['If-None-Match'] = cached_etag
    if cached_last_mod:
        headers['If-Modified-Since'] = cached_last_mod

    try:
        resp = session.get(url, timeout=timeout, headers=headers)
    except requests.RequestException as exc:
        logger.warning('Failed to fetch %s: %s', url, type(exc).__name__)
        # Preserve cached values so we keep retrying with conditional headers.
        return (feed_id, url, None, cached_etag, cached_last_mod, 0,
                type(exc).__name__)

    new_etag = resp.headers.get('ETag', cached_etag)
    new_last_mod = resp.headers.get('Last-Modified', cached_last_mod)

    if resp.status_code == 304:
        logger.debug('Feed unchanged (304): %s', url)
        return (feed_id, url, None, new_etag, new_last_mod, 304, None)

    if resp.status_code != 200:
        logger.warning('Feed %s returned HTTP %s', url, resp.status_code)
        return (feed_id, url, None, new_etag, new_last_mod, resp.status_code,
                f'HTTP {resp.status_code}')

    try:
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        logger.error('Failed to parse %s: %s', url, exc)
        return (feed_id, url, None, new_etag, new_last_mod, 200,
                f'parse error: {exc}')

    if feed.bozo and not feed.entries:
        logger.warning('Feed %s parse error with no entries: %s',
                       url, feed.bozo_exception)
        return (feed_id, url, None, new_etag, new_last_mod, 200,
                f'parse error: {feed.bozo_exception}')

    return (feed_id, url, feed, new_etag, new_last_mod, 200, None)


def fetch_feeds(feeds, timeout):
    """Fetch every feed in parallel.

    ``feeds`` is an iterable of ``(feed_id, feed_url, etag, last_modified)``
    tuples (typically from ``db_utils.db_get_active_rss_feeds``).
    """
    results: list[FetchResult] = []
    with requests.Session() as session:
        session.headers['User-Agent'] = USER_AGENT
        session.headers['Accept-Encoding'] = 'gzip, deflate'
        with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as pool:
            futures = [
                pool.submit(_fetch_one, session, feed_id, url,
                            etag, last_mod, timeout)
                for (feed_id, url, etag, last_mod) in feeds
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    return results


def parse_posts(fetch_results):
    posts: list[RssPost] = []
    for _fid, url, feed, _etag, _lm, _status, _err in fetch_results:
        if feed is None:
            continue
        feed_meta = feed.feed if hasattr(feed, 'feed') else {}
        source = feed_meta.get('link') or url
        author = feed_meta.get('title') or source

        for post in feed.entries:
            try:
                parsed = RssPost(source, author, post)
            except Exception as exc:
                logger.warning('Skipping malformed entry from %s: %s', url, exc)
                continue
            if parsed.post_has_urls:
                posts.append(parsed)
    return posts


def pick_site_link(parsed_feed) -> str | None:
    """Best-effort extraction of a feed's public website URL.

    ``parsed_feed`` is the object returned by ``feedparser.parse()``; the
    channel-level metadata lives under ``parsed_feed.feed`` (a FeedParserDict
    with ``link`` / ``links`` keys).

    Tries, in order:
      1. ``parsed_feed.feed.link`` if it looks like an absolute http(s) URL.
      2. The first entry in ``parsed_feed.feed.links`` whose ``rel`` is
         ``'alternate'`` and whose ``type`` starts with ``'text/html'``
         (or has no type), and whose ``href`` is an absolute http(s) URL.
      3. Otherwise ``None`` -- callers should leave the column untouched
         rather than fall back to the feed XML URL.
    """
    if parsed_feed is None:
        return None
    channel = parsed_feed.get('feed') if hasattr(parsed_feed, 'get') else None
    if channel is None:
        return None

    def _is_http_url(value):
        if not isinstance(value, str):
            return False
        v = value.strip()
        return v.startswith('http://') or v.startswith('https://')

    link = channel.get('link') if hasattr(channel, 'get') else None
    if _is_http_url(link):
        return link.strip()

    links = channel.get('links') if hasattr(channel, 'get') else None
    if isinstance(links, list):
        for entry in links:
            if not hasattr(entry, 'get'):
                continue
            rel = entry.get('rel')
            if rel and rel != 'alternate':
                continue
            etype = entry.get('type') or ''
            if etype and not etype.startswith('text/html'):
                continue
            href = entry.get('href')
            if _is_http_url(href):
                return href.strip()

    return None


def run(
    rss_source,
    db_path: Path,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
    excluded_url_host_keywords: list[str] | None = None,
    excluded_url_or_description_keywords: list[str] | None = None,
):
    """Fetch every active RSS feed, parse + filter posts, persist health.

    ``rss_source`` is a ``config.RssSource``. Active feeds come from the
    ``rss_feeds`` DB table; the seed file is *not* consulted here.
    """
    active = db_utils.db_get_active_rss_feeds(db_path)
    if not active:
        logger.info('RSS: no active feeds (rss_feeds table is empty or all disabled)')
        return

    fetch_results = fetch_feeds(active, rss_source.request_timeout_seconds)

    health_updates = [
        {
            'feed_id': fid,
            'status': status,
            'etag': etag,
            'last_modified': last_mod,
            'error': err,
            'site_link': pick_site_link(feed),
        }
        for (fid, _url, feed, etag, last_mod, status, err) in fetch_results
    ]
    auto_disabled = db_utils.db_update_rss_feed_after_fetch(
        health_updates, db_path, rss_source.auto_disable_after_failures,
    )
    if auto_disabled:
        logger.warning('RSS: auto-disabled %s feed(s) after consecutive failures',
                       auto_disabled)

    parsed_posts = parse_posts(fetch_results)
    recent_posts = ingest_limits.filter_posts_by_age(
        parsed_posts, max_post_age_months, 'RSS')
    recent_posts = url_filters.filter_posts_by_url_host_keywords(
        recent_posts, excluded_url_host_keywords or [], 'RSS')
    recent_posts = url_filters.filter_posts_by_url_or_description_keywords(
        recent_posts, excluded_url_or_description_keywords or [], 'RSS')

    counts = {200: 0, 304: 0, 'error': 0}
    for _fid, _u, _f, _e, _l, status, _err in fetch_results:
        if status == 200:
            counts[200] += 1
        elif status == 304:
            counts[304] += 1
        else:
            counts['error'] += 1

    if recent_posts:
        inserted_count = db_utils.db_insert(recent_posts, db_path)
        logger.info(
            'RSS: %s feeds (200=%s, 304=%s, errors=%s); '
            '%s posts parsed, %s age-eligible, %s inserted',
            len(fetch_results), counts[200], counts[304], counts['error'],
            len(parsed_posts), len(recent_posts), inserted_count,
        )
    else:
        logger.info(
            'RSS: %s feeds (200=%s, 304=%s, errors=%s); '
            '%s posts parsed, no age-eligible new posts',
            len(fetch_results), counts[200], counts[304], counts['error'],
            len(parsed_posts),
        )

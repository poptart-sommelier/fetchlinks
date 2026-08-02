"""RSS collection using requests + ETag/Last-Modified caching.

- Which feeds to fetch comes from the catalog snapshot; the cached validators
  for each feed come from local collector state. Neither involves a database.
- Uses requests with explicit timeouts so a single slow feed can't hang a worker.
- Sends If-None-Match / If-Modified-Since headers; 304 responses skip parsing.
- Every fetch attempt produces an observation record describing the outcome.
  Whether an outcome counts against a feed's health, and what that does to a
  failure counter, is left to the publisher so that replaying a batch cannot
  inflate the count.
- Connection pooling via shared requests.Session.
- Hands raw bytes to feedparser.parse() (no second network round-trip).
"""
import calendar
import concurrent.futures
import datetime
import logging

import feedparser
import requests

import ingest_limits
import url_filters
from pipeline.collection import CollectionResult
from pipeline.contract import RssObservationRecord, utc_now
from utils import RssPost

logger = logging.getLogger(__name__)

THREADS = 50
USER_AGENT = 'fetchlinks-rss/0.1 (+https://github.com/poptart-sommelier/fetchlinks)'

# What we pass between fetch and parse:
#   (normalized_url, feed_url, parsed_feed_or_none, new_etag,
#    new_last_modified, status_code, error_message_or_none)
# The natural key is ``normalized_url`` (not an autoincrement id), so an
# observation can be tied back to its feed without consulting any catalog.
FetchResult = tuple[
    str, str, feedparser.FeedParserDict | None, str, str, int, str | None,
]


def _fetch_one(
    session: requests.Session,
    normalized_url: str,
    url: str,
    cached_etag: str,
    cached_last_mod: str,
    timeout: int,
) -> FetchResult:
    """Fetch a single feed using cached ETag/Last-Modified if present.

    Returns ``(normalized_url, url, feed_or_none, etag, last_modified, status,
    error)``. ``feed_or_none`` is None for 304 / error cases (no parsing
    needed/possible). ``error`` is None on 200/304 and a short label
    otherwise.
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
        return (normalized_url, url, None, cached_etag, cached_last_mod, 0,
                type(exc).__name__)

    new_etag = resp.headers.get('ETag', cached_etag)
    new_last_mod = resp.headers.get('Last-Modified', cached_last_mod)

    if resp.status_code == 304:
        logger.debug('Feed unchanged (304): %s', url)
        return (normalized_url, url, None, new_etag, new_last_mod, 304, None)

    if resp.status_code != 200:
        logger.warning('Feed %s returned HTTP %s', url, resp.status_code)
        return (normalized_url, url, None, new_etag, new_last_mod,
                resp.status_code, f'HTTP {resp.status_code}')

    try:
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        logger.error('Failed to parse %s: %s', url, exc)
        return (normalized_url, url, None, new_etag, new_last_mod, 200,
                f'parse error: {exc}')

    if feed.bozo and not feed.entries:
        logger.warning('Feed %s parse error with no entries: %s',
                       url, feed.bozo_exception)
        return (normalized_url, url, None, new_etag, new_last_mod, 200,
                f'parse error: {feed.bozo_exception}')

    return (normalized_url, url, feed, new_etag, new_last_mod, 200, None)


def fetch_feeds(feeds, timeout):
    """Fetch every feed in parallel.

    ``feeds`` is an iterable of
    ``(normalized_url, feed_url, etag, last_modified)`` tuples, built from the
    catalog joined to the collector's cached validators.
    """
    results: list[FetchResult] = []
    with requests.Session() as session:
        session.headers['User-Agent'] = USER_AGENT
        session.headers['Accept-Encoding'] = 'gzip, deflate'
        with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as pool:
            futures = [
                pool.submit(_fetch_one, session, normalized_url, url,
                            etag, last_mod, timeout)
                for (normalized_url, url, etag, last_mod) in feeds
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    return results


def parse_posts(fetch_results):
    posts: list[RssPost] = []
    for _norm, url, feed, _etag, _lm, _status, _err in fetch_results:
        if feed is None:
            continue
        feed_meta = feed.feed if hasattr(feed, 'feed') else {}
        # `feed_source` is the URL used as the base for resolving relative
        # <link> values inside each entry. `site_link` is the feed's
        # advertised website -- preferred for posts.source so the UI can
        # group "all posts from this feed" without exposing the feed XML
        # URL. Falls back to `url` (the feed XML URL) when site_link is
        # not advertised.
        feed_source = feed_meta.get('link') or url
        site_link = pick_site_link(feed)
        author = feed_meta.get('title') or feed_source

        for post in feed.entries:
            try:
                parsed = RssPost(feed_source, author, post, site_link=site_link)
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


def latest_entry_at(parsed_feed) -> str | None:
    """Newest entry timestamp in a parsed feed, or None if undatable.

    Lets the admin distinguish a feed that is healthy but dormant from one
    that is genuinely broken, which a status code alone cannot show. Feedparser
    normalizes ``*_parsed`` to UTC, so no timezone guessing is needed.
    """
    if parsed_feed is None:
        return None
    entries = getattr(parsed_feed, 'entries', None) or []
    newest = None
    for entry in entries:
        if not hasattr(entry, 'get'):
            continue
        parsed = entry.get('published_parsed') or entry.get('updated_parsed')
        if not parsed:
            continue
        try:
            moment = datetime.datetime.fromtimestamp(calendar.timegm(parsed), datetime.UTC)
        except (TypeError, ValueError, OverflowError):
            continue
        if newest is None or moment > newest:
            newest = moment
    return newest.strftime('%Y-%m-%dT%H:%M:%SZ') if newest else None


def run(
    rss_source,
    catalog,
    state,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
    excluded_url_host_keywords: list[str] | None = None,
    excluded_url_or_description_keywords: list[str] | None = None,
) -> CollectionResult:
    """Fetch every catalogued RSS feed and return what was observed.

    ``catalog`` supplies feed identity and ``state`` supplies the cached
    validators for conditional requests. Nothing is written here: the caller
    decides when the whole cycle is durable.
    """
    result = CollectionResult()

    feeds = [
        (normalized_url, feed_url, *state.rss_headers(normalized_url))
        for normalized_url, feed_url in catalog.feed_urls
    ]
    if not feeds:
        logger.info('RSS: no active feeds in the catalog')
        return result

    fetch_results = fetch_feeds(feeds, rss_source.request_timeout_seconds)
    observed_at = utc_now()

    result.add_rss_observations(
        RssObservationRecord(
            normalized_url=normalized_url,
            feed_url=url,
            observed_at=observed_at,
            # 304 is a successful conditional request, not a miss: the cached
            # copy is confirmed current, so it must not count against health.
            success=status in (200, 304),
            # _fetch_one reports 0 when the request never got a response at
            # all. That is an absence of a status, not status zero.
            status=status or None,
            error=err,
            etag=etag or None,
            last_modified=last_mod or None,
            latest_entry_at=latest_entry_at(feed),
            site_link=pick_site_link(feed),
        )
        for (normalized_url, url, feed, etag, last_mod, status, err) in fetch_results
    )

    parsed_posts = parse_posts(fetch_results)
    recent_posts = ingest_limits.filter_posts_by_age(
        parsed_posts, max_post_age_months, 'RSS')
    recent_posts = url_filters.filter_posts_by_url_host_keywords(
        recent_posts, excluded_url_host_keywords or [], 'RSS')
    recent_posts = url_filters.filter_posts_by_url_or_description_keywords(
        recent_posts, excluded_url_or_description_keywords or [], 'RSS')

    result.add_posts(post.to_record() for post in recent_posts)

    counts = {200: 0, 304: 0, 'error': 0}
    for _norm, _u, _f, _e, _l, status, _err in fetch_results:

        if status == 200:
            counts[200] += 1
        elif status == 304:
            counts[304] += 1
        else:
            counts['error'] += 1

    logger.info(
        'RSS: %s feeds (200=%s, 304=%s, errors=%s); '
        '%s posts parsed, %s age-eligible collected',
        len(fetch_results), counts[200], counts[304], counts['error'],
        len(parsed_posts), len(recent_posts),
    )
    return result

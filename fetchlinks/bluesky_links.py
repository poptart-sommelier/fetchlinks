import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import db_utils
import ingest_limits
import url_filters
from auth import BlueskyAuth
from utils import BlueskyPost, extract_urls_from_text

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_LIMIT = 100
MAX_TIMELINE_LIMIT = 100
MAX_PAGES = 10

# Hosts to exclude from extracted links (we don't want to link back to Bluesky itself).
EXCLUDED_HOSTS = ('bsky.app', 'bsky.social')


def _is_excluded_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or '').lower()
    except ValueError:
        return False
    return any(host == h or host.endswith('.' + h) for h in EXCLUDED_HOSTS)


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        # by_alias=True keeps wire-format camelCase keys (createdAt, displayName, etc.)
        return obj.model_dump(exclude_none=True, by_alias=True)
    if hasattr(obj, 'dict'):
        return obj.dict(exclude_none=True, by_alias=True)
    return {}


def _call_timeline(client, limit: int, cursor: Optional[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {'limit': limit}
    if cursor:
        params['cursor'] = cursor

    if hasattr(client, 'app') and hasattr(client.app, 'bsky') and hasattr(client.app.bsky, 'feed'):
        response = client.app.bsky.feed.get_timeline(params=params)
    else:
        response = client.get_timeline(limit=limit, cursor=cursor)

    return _as_dict(response)


def _extract_links_from_embed(embed: Any) -> List[str]:
    links: List[str] = []

    def walk(node: Any):
        node_dict = _as_dict(node)
        if not node_dict:
            if isinstance(node, list):
                for item in node:
                    walk(item)
            return

        uri = node_dict.get('uri')
        if isinstance(uri, str) and uri.startswith('http'):
            links.append(uri)

        for value in node_dict.values():
            if isinstance(value, (dict, list)) or hasattr(value, 'model_dump'):
                walk(value)

    walk(embed)
    return links


def _extract_links_from_facets(record: Dict[str, Any]) -> List[str]:
    links: List[str] = []
    for facet in record.get('facets', []):
        for feature in facet.get('features', []):
            uri = feature.get('uri')
            if isinstance(uri, str) and uri.startswith('http'):
                links.append(uri)
    return links


def _build_direct_link(author: Dict[str, Any], post: Dict[str, Any]) -> str:
    handle = author.get('handle') or author.get('did', '')
    uri = post.get('uri', '')
    post_key = ''
    if isinstance(uri, str):
        uri_parts = uri.split('/')
        if uri_parts:
            post_key = uri_parts[-1]
    if handle and post_key:
        return f'https://bsky.app/profile/{handle}/post/{post_key}'
    return ''


def _build_source(author: Dict[str, Any]) -> str:
    handle = author.get('handle') or author.get('did', '')
    if handle:
        return f'https://bsky.app/profile/{handle}'
    return 'https://bsky.app'


def _parse_feed_item(item: Dict[str, Any]) -> Optional[BlueskyPost]:
    post = _as_dict(item.get('post'))
    if not post:
        return None

    author = _as_dict(post.get('author'))
    record = _as_dict(post.get('record'))

    text = record.get('text', '')
    created_at = record.get('createdAt', '')
    if not text or not created_at:
        return None

    links = []
    links.extend(_extract_links_from_facets(record))
    links.extend(_extract_links_from_embed(post.get('embed')))
    links.extend(extract_urls_from_text(text))

    external_links = [
        url for url in links
        if isinstance(url, str) and url.startswith('http') and not _is_excluded_host(url)
    ]
    if not external_links:
        return None

    author_name = author.get('displayName') or author.get('handle') or 'Unknown'

    return BlueskyPost(
        source=_build_source(author),
        author=author_name,
        description=text,
        direct_link=_build_direct_link(author, post),
        created_at=created_at,
        urls=external_links,
    )


def _fetch_timeline_page(client, cursor: Optional[str], limit: int) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    response = _call_timeline(client, limit=limit, cursor=cursor)
    feed_items = response.get('feed', [])
    next_cursor = response.get('cursor')
    if not isinstance(feed_items, list):
        return [], None
    return feed_items, next_cursor


def run(
    bluesky_config: dict,
    db_info: dict,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
    excluded_url_host_keywords: List[str] | None = None,
):
    if not bluesky_config.get('enabled', False):
        logger.info('Bluesky source is disabled; skipping')
        return

    db_full_path = Path(db_info['db_location']) / db_info['db_name']
    timeline_limit = int(bluesky_config.get('timeline_limit', DEFAULT_TIMELINE_LIMIT))
    timeline_limit = max(1, min(timeline_limit, MAX_TIMELINE_LIMIT))

    auth_client = BlueskyAuth(bluesky_config['credential_location'])
    client = auth_client.get_client()

    previous_cursor = db_utils.db_get_bluesky_cursor(db_full_path)

    feed_items: List[Dict[str, Any]] = []
    cursor = previous_cursor
    next_cursor: Optional[str] = previous_cursor
    pages_fetched = 0
    for page_num in range(1, MAX_PAGES + 1):
        page_items, page_cursor = _fetch_timeline_page(client, cursor, timeline_limit)
        pages_fetched += 1
        logger.debug(
            'Bluesky page %s/%s: cursor=%s, items=%s, next_cursor=%s',
            page_num,
            MAX_PAGES,
            cursor,
            len(page_items),
            page_cursor,
        )

        if not page_items:
            next_cursor = page_cursor or cursor
            break

        feed_items.extend(page_items)
        next_cursor = page_cursor

        # No more pages available, or cursor did not advance (guard against loops).
        if not page_cursor or page_cursor == cursor:
            break

        cursor = page_cursor

    logger.info(
        'Bluesky timeline fetch: starting_cursor=%s, pages=%s/%s, items_returned=%s, next_cursor=%s',
        previous_cursor,
        pages_fetched,
        MAX_PAGES,
        len(feed_items),
        next_cursor,
    )

    parsed_posts: List[BlueskyPost] = []
    skipped_no_links = 0
    skipped_missing_fields = 0
    for item in feed_items:
        parsed = _parse_feed_item(item)
        if parsed is None:
            # Distinguish missing-field skips from no-link skips for diagnostics.
            post_dict = _as_dict(item.get('post') if isinstance(item, dict) else getattr(item, 'post', None))
            record_dict = _as_dict(post_dict.get('record'))
            if not record_dict.get('text') or not record_dict.get('createdAt'):
                skipped_missing_fields += 1
            else:
                skipped_no_links += 1
            continue
        if parsed.post_has_urls:
            parsed_posts.append(parsed)

    recent_posts = ingest_limits.filter_posts_by_age(parsed_posts, max_post_age_months, 'Bluesky')
    recent_posts = url_filters.filter_posts_by_url_host_keywords(
        recent_posts,
        excluded_url_host_keywords or [],
        'Bluesky',
    )
    inserted_count = db_utils.db_insert(recent_posts, db_full_path)
    db_utils.db_set_bluesky_cursor(next_cursor, db_full_path)

    logger.info(
        'Parsed %s Bluesky posts (skipped %s no-links, %s missing-fields), %s age-eligible, inserted %s new rows, cursor advanced=%s',
        len(parsed_posts),
        skipped_no_links,
        skipped_missing_fields,
        len(recent_posts),
        inserted_count,
        bool(next_cursor),
    )

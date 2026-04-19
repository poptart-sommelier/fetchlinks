import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import db_utils
from auth import BlueskyAuth
from utils import BlueskyPost, extract_urls_from_text

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_LIMIT = 50
MAX_TIMELINE_LIMIT = 100


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(exclude_none=True)
    if hasattr(obj, 'dict'):
        return obj.dict(exclude_none=True)
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

    external_links = [url for url in links if isinstance(url, str) and url.startswith('http')]
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


def run(bluesky_config: dict, db_info: dict):
    if not bluesky_config.get('enabled', False):
        logger.info('Bluesky source is disabled; skipping')
        return

    db_full_path = Path(db_info['db_location']) / db_info['db_name']
    timeline_limit = int(bluesky_config.get('timeline_limit', DEFAULT_TIMELINE_LIMIT))
    timeline_limit = max(1, min(timeline_limit, MAX_TIMELINE_LIMIT))

    auth_client = BlueskyAuth(bluesky_config['credential_location'])
    client = auth_client.get_client()

    previous_cursor = db_utils.db_get_bluesky_cursor(db_full_path)
    feed_items, next_cursor = _fetch_timeline_page(client, previous_cursor, timeline_limit)

    parsed_posts: List[BlueskyPost] = []
    for item in feed_items:
        parsed = _parse_feed_item(item)
        if parsed is not None and parsed.post_has_urls:
            parsed_posts.append(parsed)

    inserted_count = db_utils.db_insert(parsed_posts, db_full_path)
    db_utils.db_set_bluesky_cursor(next_cursor, db_full_path)

    logger.info(
        'Parsed %s Bluesky posts, inserted %s new rows, cursor advanced=%s',
        len(parsed_posts),
        inserted_count,
        bool(next_cursor),
    )

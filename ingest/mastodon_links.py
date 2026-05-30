import html
import logging
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests

import db_utils
import ingest_limits
import url_filters
from auth import MastodonAuth
from config import MastodonInstance, MastodonSource
from utils import MastodonPost, extract_urls_from_text

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_LIMIT = 40
MAX_TIMELINE_LIMIT = 80
MAX_PAGES = 5
REQUEST_TIMEOUT_SECONDS = 20
SUPPORTED_TIMELINES = {'home'}
# Cap pages when mirroring the following list so a huge graph can't stall ingest.
MAX_FOLLOWING_PAGES = 40

class _StatusContentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text_parts = []
        self.non_anchor_text_parts = []
        self._anchor_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self._anchor_depth += 1
            attrs_dict = dict(attrs)
            href = attrs_dict.get('href')
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag == 'a' and self._anchor_depth > 0:
            self._anchor_depth -= 1

    def handle_data(self, data):
        if data:
            self.text_parts.append(data)
            if self._anchor_depth == 0:
                self.non_anchor_text_parts.append(data)


def _normalize_instance_url(instance_url: str) -> str:
    return instance_url.rstrip('/')


def _build_timeline_url(instance_url: str, timeline: str) -> str:
    if timeline not in SUPPORTED_TIMELINES:
        raise ValueError(f'Unsupported Mastodon timeline: {timeline}')
    return urljoin(_normalize_instance_url(instance_url) + '/', 'api/v1/timelines/home')


def _join_text_parts(parts: list[str]) -> str:
    return html.unescape(' '.join(part.strip() for part in parts if part.strip()))


def _parse_content(content: str) -> tuple[str, list[str], str]:
    parser = _StatusContentParser()
    parser.feed(content or '')
    return _join_text_parts(parser.text_parts), parser.links, _join_text_parts(parser.non_anchor_text_parts)


def _is_tag_url(url: str) -> bool:
    try:
        path = urlparse(url).path.rstrip('/').lower()
    except ValueError:
        return False
    return path == '/tags' or path.startswith('/tags/')


def _filter_links(links: list[str]) -> list[str]:
    return [link for link in links if not _is_tag_url(link)]


def _extract_links_from_status(status: dict) -> list[str]:
    links = []
    _text, content_links, non_anchor_text = _parse_content(status.get('content', ''))
    links.extend(content_links)
    links.extend(extract_urls_from_text(non_anchor_text))

    card = status.get('card')
    if isinstance(card, dict) and isinstance(card.get('url'), str):
        links.append(card['url'])

    return _filter_links(links)


def _highest_status_id(statuses: list[dict]) -> str | None:
    ids = [status.get('id') for status in statuses if isinstance(status.get('id'), str)]
    if not ids:
        return None
    try:
        return max(ids, key=int)
    except ValueError:
        return ids[0]


def _next_max_id_from_link_header(link_header: str) -> str | None:
    if not link_header:
        return None

    for link in requests.utils.parse_header_links(link_header):
        if link.get('rel') != 'next':
            continue
        parsed_url = urlparse(link.get('url', ''))
        max_id = parse_qs(parsed_url.query).get('max_id')
        if max_id and max_id[0]:
            return max_id[0]
    return None


def _fetch_timeline_page(
    session: requests.Session,
    instance_config: MastodonInstance,
    since_id: str | None,
    max_id: str | None = None,
) -> tuple[list[dict], str | None]:
    timeline_url = _build_timeline_url(instance_config.instance_url, instance_config.timeline)
    limit = max(1, min(instance_config.timeline_limit, MAX_TIMELINE_LIMIT))
    params = {'limit': limit}
    if since_id:
        params['since_id'] = since_id
    if max_id:
        params['max_id'] = max_id

    try:
        response = session.get(timeline_url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except ValueError as exc:
        logger.error('Invalid JSON while retrieving Mastodon %s: %s', instance_config.name, exc)
        return [], None
    except requests.RequestException as exc:
        logger.error('Request error while retrieving Mastodon %s: %s', instance_config.name, exc)
        return [], None
    if not isinstance(payload, list):
        logger.error('Unexpected Mastodon payload shape for %s', instance_config.name)
        return [], None
    return payload, _next_max_id_from_link_header(response.headers.get('Link', ''))


def _fetch_timeline_pages(session: requests.Session, instance_config: MastodonInstance, since_id: str | None) -> list[dict]:
    statuses = []
    max_id = None
    for page_num in range(1, MAX_PAGES + 1):
        page_statuses, next_max_id = _fetch_timeline_page(session, instance_config, since_id, max_id)
        logger.debug(
            'Mastodon %s page %s/%s: since_id=%s, max_id=%s, statuses=%s, next_max_id=%s',
            instance_config.name,
            page_num,
            MAX_PAGES,
            since_id,
            max_id,
            len(page_statuses),
            next_max_id,
        )
        if not page_statuses:
            break
        statuses.extend(page_statuses)
        if not next_max_id or next_max_id == max_id:
            break
        max_id = next_max_id
    return statuses


def _parse_status(status: dict) -> MastodonPost | None:
    account = status.get('account') if isinstance(status.get('account'), dict) else {}
    created_at = status.get('created_at')
    status_url = status.get('url') or status.get('uri') or ''
    if not created_at:
        return None

    description, _content_links, _non_anchor_text = _parse_content(status.get('content', ''))
    links = _extract_links_from_status(status)
    if not links:
        return None

    source = account.get('url') or ''
    author = account.get('display_name') or account.get('acct') or account.get('username') or 'Unknown'

    return MastodonPost(
        source=source,
        author=author,
        description=description,
        direct_link=status_url,
        created_at=created_at,
        urls=links,
    )


def _run_instance(
    instance_config: MastodonInstance,
    db_path: Path,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
    excluded_url_host_keywords: list[str] | None = None,
    excluded_url_or_description_keywords: list[str] | None = None,
) -> int:
    if not instance_config.enabled:
        logger.info('Mastodon source %s is disabled; skipping', instance_config.name)
        return 0

    source_name = instance_config.name
    instance_url = _normalize_instance_url(instance_config.instance_url)
    auth_client = MastodonAuth(str(instance_config.credential_location))
    last_seen_id = db_utils.db_get_mastodon_last_seen_id(source_name, db_path)

    with requests.Session() as session:
        session.headers.update(auth_client.headers)
        statuses = _fetch_timeline_pages(session, instance_config, last_seen_id)

    parsed_posts = []
    skipped_no_links = 0
    skipped_missing_fields = 0
    for status in statuses:
        parsed = _parse_status(status)
        if parsed is None:
            if not status.get('created_at'):
                skipped_missing_fields += 1
            else:
                skipped_no_links += 1
            continue
        if parsed.post_has_urls:
            parsed_posts.append(parsed)

    recent_posts = ingest_limits.filter_posts_by_age(parsed_posts, max_post_age_months, f'Mastodon {source_name}')
    recent_posts = url_filters.filter_posts_by_url_host_keywords(
        recent_posts,
        excluded_url_host_keywords or [],
        f'Mastodon {source_name}',
    )
    recent_posts = url_filters.filter_posts_by_url_or_description_keywords(
        recent_posts,
        excluded_url_or_description_keywords or [],
        f'Mastodon {source_name}',
    )
    inserted_count = db_utils.db_insert(recent_posts, db_path)
    highest_id = _highest_status_id(statuses)
    if highest_id:
        db_utils.db_set_mastodon_last_seen_id(source_name, instance_url, highest_id, db_path)

    logger.info(
        'Mastodon %s: fetched %s statuses, parsed %s posts (skipped %s no-links, %s missing-fields), %s age-eligible, inserted %s',
        source_name,
        len(statuses),
        len(parsed_posts),
        skipped_no_links,
        skipped_missing_fields,
        len(recent_posts),
        inserted_count,
    )
    return inserted_count


def run(
    mastodon_config: MastodonSource,
    db_path: Path,
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
    excluded_url_host_keywords: list[str] | None = None,
    excluded_url_or_description_keywords: list[str] | None = None,
):
    if not mastodon_config.enabled:
        logger.info('Mastodon source is disabled; skipping')
        return

    total_inserted = 0
    for instance_config in mastodon_config.instances:
        total_inserted += _run_instance(
            instance_config,
            db_path,
            max_post_age_months,
            excluded_url_host_keywords or [],
            excluded_url_or_description_keywords or [],
        )

    logger.info('Inserted %s Mastodon posts into DB', total_inserted)


def _verify_credentials_account_id(session: requests.Session, instance_url: str) -> str | None:
    url = urljoin(_normalize_instance_url(instance_url) + '/', 'api/v1/accounts/verify_credentials')
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except ValueError as exc:
        logger.error('Invalid JSON verifying Mastodon credentials for %s: %s', instance_url, exc)
        return None
    except requests.RequestException as exc:
        logger.error('Request error verifying Mastodon credentials for %s: %s', instance_url, exc)
        return None
    account_id = payload.get('id') if isinstance(payload, dict) else None
    return str(account_id) if account_id else None


def _next_following_url_from_link_header(link_header: str) -> str | None:
    if not link_header:
        return None
    for link in requests.utils.parse_header_links(link_header):
        if link.get('rel') == 'next':
            url = link.get('url')
            return url or None
    return None


def _fetch_following_pages(session: requests.Session, instance_url: str, account_id: str) -> list[dict]:
    following: list[dict] = []
    next_url: str | None = urljoin(
        _normalize_instance_url(instance_url) + '/',
        f'api/v1/accounts/{account_id}/following',
    )
    params: dict | None = {'limit': 80}
    for _page in range(1, MAX_FOLLOWING_PAGES + 1):
        if not next_url:
            break
        try:
            response = session.get(next_url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
        except ValueError as exc:
            logger.error('Invalid JSON listing Mastodon following for %s: %s', instance_url, exc)
            break
        except requests.RequestException as exc:
            logger.error('Request error listing Mastodon following for %s: %s', instance_url, exc)
            break
        if not isinstance(payload, list) or not payload:
            break
        following.extend(payload)
        # Subsequent pages come from the Link header's `next` URL (which
        # already carries its own max_id query), so drop our initial params.
        next_url = _next_following_url_from_link_header(response.headers.get('Link', ''))
        params = None
    return following


def sync_follows(mastodon_config: MastodonSource, db_path: Path) -> None:
    """Refresh the read-only follows snapshot for each enabled instance.

    Best-effort: a failure for one instance is logged and does not abort the
    others, and never raises into the ingest run.
    """
    if not mastodon_config.enabled:
        return

    for instance_config in mastodon_config.instances:
        if not instance_config.enabled:
            continue
        source_name = instance_config.name
        instance_url = _normalize_instance_url(instance_config.instance_url)
        try:
            auth_client = MastodonAuth(str(instance_config.credential_location))
            with requests.Session() as session:
                session.headers.update(auth_client.headers)
                account_id = _verify_credentials_account_id(session, instance_url)
                if not account_id:
                    logger.warning(
                        'Mastodon %s: could not resolve own account id; skipping follows sync',
                        source_name,
                    )
                    continue
                accounts = _fetch_following_pages(session, instance_url, account_id)
            follows = [
                (
                    str(acc.get('id') or ''),
                    acc.get('acct') or acc.get('username') or '',
                    acc.get('display_name') or '',
                    acc.get('url') or '',
                )
                for acc in accounts
                if isinstance(acc, dict)
            ]
            written = db_utils.db_replace_mastodon_follows(source_name, follows, db_path)
            logger.info('Mastodon %s: synced %s follows', source_name, written)
        except Exception as exc:  # noqa: BLE001 - best-effort snapshot
            logger.error('Mastodon %s: follows sync failed: %s', source_name, exc)

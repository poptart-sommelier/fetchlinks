"""Import RSS feeds from a text file of URLs into sources.json.

Default mode validates candidates and appends active feeds to sources.json.
Use --dry-run to write a reusable .pruned file without editing sources.json.
Use --pruned to apply a previously reviewed one-URL-per-line file without
network checks.
"""
import argparse
import calendar
import concurrent.futures
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import feedparser
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES = SCRIPT_DIR / 'data' / 'config' / 'sources.json'
DEFAULT_ABANDONED_DAYS = 365
REQUEST_TIMEOUT_SECONDS = 12
MAX_WORKERS = 20
USER_AGENT = 'fetchlinks-rss-import/0.1 (+https://github.com/poptart-sommelier/fetchlinks)'

URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
TRAILING_PUNCTUATION = '.,;:!?)]}\''


@dataclass(frozen=True)
class FeedCheck:
    input_url: str
    feed_url: str
    final_url: str
    status: str
    title: str = ''
    feed_link: str = ''
    entry_links: tuple[str, ...] = ()
    entry_titles: tuple[str, ...] = ()
    latest_entry: datetime | None = None
    entry_count: int = 0
    reason: str = ''


class FeedDiscoveryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feed_links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'link':
            return

        attr_map = {name.lower(): value for name, value in attrs if value is not None}
        rel_values = set(attr_map.get('rel', '').lower().split())
        type_value = attr_map.get('type', '').lower()
        href = attr_map.get('href', '')
        if not href or 'alternate' not in rel_values:
            return
        if type_value in {'application/rss+xml', 'application/atom+xml', 'application/feed+json'}:
            self.feed_links.append((href, type_value))


def extract_urls(text: str) -> list[str]:
    urls = []
    seen = set()
    for match in URL_RE.findall(text):
        cleaned = clean_candidate_url(match)
        if not cleaned:
            continue
        key = normalize_feed_url(cleaned)
        if key in seen:
            continue
        seen.add(key)
        urls.append(cleaned)
    return urls


def clean_candidate_url(url: str) -> str:
    cleaned = url.strip().rstrip(TRAILING_PUNCTUATION)
    while cleaned.endswith(')') and cleaned.count('(') < cleaned.count(')'):
        cleaned = cleaned[:-1]
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in {'http', 'https'} or not parts.netloc:
        return ''
    return cleaned


def normalize_feed_url(url: str) -> str:
    cleaned, _fragment = urldefrag(url.strip())
    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or '/'
    return urlunsplit((scheme, netloc, path, parts.query, ''))


def canonical_hostname(url: str) -> str:
    hostname = (urlsplit(url).hostname or '').lower()
    return hostname[4:] if hostname.startswith('www.') else hostname


def feed_site_key(url: str) -> str:
    return canonical_hostname(url)


def normalize_content_url(url: str) -> str:
    cleaned, _fragment = urldefrag(str(url).strip())
    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    path = (parts.path or '/').rstrip('/') or '/'
    return urlunsplit((scheme, netloc, path, parts.query, ''))


def normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text).strip().lower())


def load_sources(sources_path: Path) -> dict:
    with sources_path.open('r', encoding='utf-8') as sources_file:
        return json.load(sources_file)


def load_existing_feeds(sources_path: Path) -> list[str]:
    sources = load_sources(sources_path)
    rss_config = sources.get('rss', {})
    feeds = rss_config.get('feeds', []) if isinstance(rss_config, dict) else []
    if not isinstance(feeds, list):
        raise ValueError('sources.json rss.feeds must be a list')
    return [feed for feed in feeds if isinstance(feed, str)]


def dedupe_against_existing(candidates: list[str], existing_feeds: list[str]) -> tuple[list[str], list[str], list[str]]:
    existing_keys = {normalize_feed_url(feed) for feed in existing_feeds}
    seen_candidate_keys = set()
    new_candidates = []
    already_present = []
    duplicate_in_input = []

    for candidate in candidates:
        key = normalize_feed_url(candidate)
        if key in seen_candidate_keys:
            duplicate_in_input.append(candidate)
            continue
        seen_candidate_keys.add(key)
        if key in existing_keys:
            already_present.append(candidate)
            continue
        new_candidates.append(candidate)

    return new_candidates, already_present, duplicate_in_input


def latest_entry_datetime(feed) -> datetime | None:
    latest = None
    for entry in getattr(feed, 'entries', []):
        parsed = getattr(entry, 'published_parsed', None) or getattr(entry, 'updated_parsed', None) or getattr(entry, 'created_parsed', None)
        if not parsed:
            continue
        candidate = datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
        if latest is None or candidate > latest:
            latest = candidate
    return latest


def feed_entry_links(feed, limit: int = 10) -> tuple[str, ...]:
    links = []
    for entry in getattr(feed, 'entries', [])[:limit]:
        link = getattr(entry, 'link', '')
        if link:
            links.append(normalize_content_url(link))
    return tuple(links)


def feed_entry_titles(feed, limit: int = 10) -> tuple[str, ...]:
    titles = []
    for entry in getattr(feed, 'entries', [])[:limit]:
        title = normalize_text(getattr(entry, 'title', ''))
        if title:
            titles.append(title)
    return tuple(titles)


def feed_link(feed) -> str:
    if not hasattr(feed, 'feed'):
        return ''
    link = feed.feed.get('link', '')
    return normalize_content_url(link) if link else ''


def feed_title(feed) -> str:
    feed_meta = getattr(feed, 'feed', {})
    return feed_meta.get('title', '') if hasattr(feed_meta, 'get') else ''


def check_feed(
    url: str,
    session: requests.Session,
    abandoned_days: int = DEFAULT_ABANDONED_DAYS,
    now: datetime | None = None,
) -> FeedCheck:
    now = now or datetime.now(UTC)
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return FeedCheck(url, url, url, 'dead', reason=type(exc).__name__)

    final_url = response.url or url
    if response.status_code >= 400:
        return FeedCheck(url, final_url, final_url, 'dead', reason=f'HTTP {response.status_code}')

    parsed_feed = feedparser.parse(response.content)
    if parsed_feed.bozo and not parsed_feed.entries:
        discovered = discover_feed_url(final_url, response.text)
        if discovered and normalize_feed_url(discovered) != normalize_feed_url(url):
            return check_feed(discovered, session, abandoned_days, now)
        return FeedCheck(url, final_url, final_url, 'dead', reason='parse error with no entries')

    if not parsed_feed.entries:
        discovered = discover_feed_url(final_url, response.text)
        if discovered and normalize_feed_url(discovered) != normalize_feed_url(url):
            return check_feed(discovered, session, abandoned_days, now)
        return FeedCheck(url, final_url, final_url, 'dead', reason='no entries')

    latest = latest_entry_datetime(parsed_feed)
    title = feed_title(parsed_feed)
    parsed_feed_link = feed_link(parsed_feed)
    entry_links = feed_entry_links(parsed_feed)
    entry_titles = feed_entry_titles(parsed_feed)
    feed_url = final_url
    if latest is None:
        return FeedCheck(
            url,
            feed_url,
            final_url,
            'unknown_date',
            title=title,
            feed_link=parsed_feed_link,
            entry_links=entry_links,
            entry_titles=entry_titles,
            entry_count=len(parsed_feed.entries),
            reason='no entry dates',
        )

    if latest < now - timedelta(days=abandoned_days):
        return FeedCheck(
            url,
            feed_url,
            final_url,
            'abandoned',
            title=title,
            feed_link=parsed_feed_link,
            entry_links=entry_links,
            entry_titles=entry_titles,
            latest_entry=latest,
            entry_count=len(parsed_feed.entries),
        )

    return FeedCheck(
        url,
        feed_url,
        final_url,
        'active',
        title=title,
        feed_link=parsed_feed_link,
        entry_links=entry_links,
        entry_titles=entry_titles,
        latest_entry=latest,
        entry_count=len(parsed_feed.entries),
    )


def checks_are_duplicate_feed(candidate: FeedCheck, existing: FeedCheck) -> tuple[bool, str]:
    if normalize_feed_url(candidate.feed_url) == normalize_feed_url(existing.feed_url):
        return True, f'final feed URL matches existing {existing.input_url}'

    if candidate.feed_link and existing.feed_link and candidate.feed_link == existing.feed_link:
        return True, f'feed link matches existing {existing.input_url}'

    candidate_links = {normalize_content_url(link) for link in candidate.entry_links}
    existing_links = {normalize_content_url(link) for link in existing.entry_links}
    shared_links = candidate_links & existing_links
    if candidate.entry_links and existing.entry_links and normalize_content_url(candidate.entry_links[0]) == normalize_content_url(existing.entry_links[0]):
        return True, f'latest entry link matches existing {existing.input_url}'
    if len(shared_links) >= 3:
        return True, f'{len(shared_links)} recent entry link(s) match existing {existing.input_url}'

    candidate_titles = set(candidate.entry_titles)
    existing_titles = set(existing.entry_titles)
    shared_titles = candidate_titles & existing_titles
    if normalize_text(candidate.title) and normalize_text(candidate.title) == normalize_text(existing.title) and len(shared_titles) >= 2:
        return True, f'feed title and {len(shared_titles)} recent entry title(s) match existing {existing.input_url}'

    return False, ''


def mark_same_site_content_duplicates(
    checks: list[FeedCheck],
    existing_feeds: list[str],
    abandoned_days: int,
) -> list[FeedCheck]:
    existing_by_site: dict[str, list[str]] = {}
    for feed in existing_feeds:
        site_key = feed_site_key(feed)
        if site_key:
            existing_by_site.setdefault(site_key, []).append(feed)

    existing_check_cache: dict[str, FeedCheck] = {}
    updated_checks = []
    with requests.Session() as session:
        session.headers['User-Agent'] = USER_AGENT
        session.headers['Accept-Encoding'] = 'gzip, deflate'
        for check in checks:
            if check.status != 'active':
                updated_checks.append(check)
                continue

            site_key = feed_site_key(check.feed_url) or feed_site_key(check.input_url)
            same_site_existing = existing_by_site.get(site_key, [])
            duplicate_reason = ''
            for existing_feed in same_site_existing:
                existing_check = existing_check_cache.get(existing_feed)
                if existing_check is None:
                    existing_check = check_feed(existing_feed, session, abandoned_days)
                    existing_check_cache[existing_feed] = existing_check
                if existing_check.status not in {'active', 'abandoned', 'unknown_date'}:
                    continue
                is_duplicate, reason = checks_are_duplicate_feed(check, existing_check)
                if is_duplicate:
                    duplicate_reason = reason
                    break

            if duplicate_reason:
                updated_checks.append(replace(check, status='duplicate_content', reason=duplicate_reason))
            else:
                updated_checks.append(check)

    return updated_checks


def discover_feed_url(base_url: str, html: str) -> str | None:
    parser = FeedDiscoveryParser()
    try:
        parser.feed(html)
    except Exception:
        return None

    if not parser.feed_links:
        return None

    rss_links = [href for href, type_value in parser.feed_links if type_value == 'application/rss+xml']
    href = rss_links[0] if rss_links else parser.feed_links[0][0]
    return urljoin(base_url, href)


def check_candidates(candidates: list[str], abandoned_days: int) -> list[FeedCheck]:
    results: list[FeedCheck] = []
    with requests.Session() as session:
        session.headers['User-Agent'] = USER_AGENT
        session.headers['Accept-Encoding'] = 'gzip, deflate'
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(check_feed, candidate, session, abandoned_days) for candidate in candidates]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    return sorted(results, key=lambda result: result.input_url.lower())


def read_pruned(pruned_path: Path) -> list[str]:
    feeds = []
    seen = set()
    for line in pruned_path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        cleaned = clean_candidate_url(stripped)
        if not cleaned:
            continue
        key = normalize_feed_url(cleaned)
        if key in seen:
            continue
        seen.add(key)
        feeds.append(cleaned)
    return feeds


def unique_feed_urls(feeds: list[str]) -> list[str]:
    unique_feeds = []
    seen = set()
    for feed in feeds:
        key = normalize_feed_url(feed)
        if key in seen:
            continue
        seen.add(key)
        unique_feeds.append(feed)
    return unique_feeds


def write_pruned(input_path: Path, feeds: list[str]) -> Path:
    pruned_path = input_path.with_name(f'{input_path.name}.pruned')
    pruned_path.write_text(''.join(f'{feed}\n' for feed in feeds), encoding='utf-8')
    return pruned_path


def append_feeds_to_sources(sources_path: Path, feeds_to_add: list[str]) -> int:
    if not feeds_to_add:
        return 0

    sources = load_sources(sources_path)
    rss_config = sources.setdefault('rss', {})
    if not isinstance(rss_config, dict):
        raise ValueError('sources.json rss section must be an object')
    feeds = rss_config.setdefault('feeds', [])
    if not isinstance(feeds, list):
        raise ValueError('sources.json rss.feeds must be a list')

    existing_keys = {normalize_feed_url(feed) for feed in feeds if isinstance(feed, str)}
    unique_new_feeds = []
    for feed in feeds_to_add:
        key = normalize_feed_url(feed)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        unique_new_feeds.append(feed)

    if not unique_new_feeds:
        return 0

    backup_path = sources_path.with_name(f'{sources_path.name}.bak')
    shutil.copy2(sources_path, backup_path)
    feeds.extend(unique_new_feeds)
    sources_path.write_text(json.dumps(sources, indent=4) + '\n', encoding='utf-8')
    return len(unique_new_feeds)


def summarize_checks(checks: list[FeedCheck]) -> dict[str, list[FeedCheck]]:
    statuses = {'active': [], 'abandoned': [], 'unknown_date': [], 'dead': [], 'duplicate_content': []}
    for check in checks:
        statuses.setdefault(check.status, []).append(check)
    return statuses


def print_report(
    extracted_count: int,
    already_present: list[str],
    duplicate_in_input: list[str],
    checks: list[FeedCheck],
    accepted_feeds: list[str],
    added_count: int,
    dry_run: bool,
    pruned_path: Path | None = None,
):
    statuses = summarize_checks(checks)
    print(f'Extracted URLs: {extracted_count}')
    print(f'Already present: {len(already_present)}')
    print(f'Duplicate in input: {len(duplicate_in_input)}')
    print(f'Checked: {len(checks)}')
    print(f'Active: {len(statuses.get("active", []))}')
    print(f'Abandoned: {len(statuses.get("abandoned", []))}')
    print(f'Unknown date: {len(statuses.get("unknown_date", []))}')
    print(f'Dead/invalid: {len(statuses.get("dead", []))}')
    print(f'Duplicate content: {len(statuses.get("duplicate_content", []))}')
    print(f'Accepted: {len(accepted_feeds)}')
    print(f'Added: {added_count}')

    if accepted_feeds:
        print('\nAccepted:')
        active_checks = statuses.get('active', [])
        if active_checks:
            for check in active_checks:
                latest = check.latest_entry.date().isoformat() if check.latest_entry else 'unknown'
                title = f'  {check.title}' if check.title else ''
                print(f'  {check.feed_url} latest={latest}{title}')
        else:
            for feed in accepted_feeds:
                print(f'  {feed}')

    skipped_checks = [check for check in checks if check.status != 'active']
    if already_present or duplicate_in_input or skipped_checks:
        print('\nSkipped:')
        for feed in already_present:
            print(f'  {feed} already present')
        for feed in duplicate_in_input:
            print(f'  {feed} duplicate in input')
        for check in skipped_checks:
            reason = f' {check.reason}' if check.reason else ''
            if check.latest_entry:
                print(f'  {check.input_url} {check.status} latest={check.latest_entry.date().isoformat()}{reason}')
            else:
                print(f'  {check.input_url} {check.status}{reason}'.rstrip())

    if dry_run and pruned_path:
        print(f'\nWrote accepted feeds to {pruned_path}')
        print('No changes made to sources.json')


def import_from_input(input_path: Path, sources_path: Path, dry_run: bool, abandoned_days: int) -> int:
    text = input_path.read_text(encoding='utf-8')
    extracted = extract_urls(text)
    existing_feeds = load_existing_feeds(sources_path)
    candidates, already_present, duplicate_in_input = dedupe_against_existing(extracted, existing_feeds)
    checks = check_candidates(candidates, abandoned_days)
    checks = mark_same_site_content_duplicates(checks, existing_feeds, abandoned_days)
    accepted_feeds = unique_feed_urls([check.feed_url for check in checks if check.status == 'active'])

    pruned_path = write_pruned(input_path, accepted_feeds) if dry_run else None
    added_count = 0 if dry_run else append_feeds_to_sources(sources_path, accepted_feeds)
    print_report(
        len(extracted),
        already_present,
        duplicate_in_input,
        checks,
        accepted_feeds,
        added_count,
        dry_run,
        pruned_path,
    )
    return added_count


def import_from_pruned(pruned_path: Path, sources_path: Path, dry_run: bool) -> int:
    feeds = read_pruned(pruned_path)
    existing_feeds = load_existing_feeds(sources_path)
    candidates, already_present, duplicate_in_input = dedupe_against_existing(feeds, existing_feeds)
    added_count = 0 if dry_run else append_feeds_to_sources(sources_path, candidates)
    print_report(
        len(feeds),
        already_present,
        duplicate_in_input,
        [],
        candidates,
        added_count,
        dry_run,
    )
    return added_count


def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(description='Import RSS feeds into sources.json from a URL list file.')
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--input', type=Path, help='Text file containing RSS/feed/homepage URLs')
    input_group.add_argument('--pruned', type=Path, help='Previously validated one-feed-URL-per-line file')
    parser.add_argument('--sources', type=Path, default=DEFAULT_SOURCES, help='Path to sources.json')
    parser.add_argument('--dry-run', action='store_true', help='Do not modify sources.json; with --input, write INPUT.pruned')
    parser.add_argument(
        '--abandoned-days',
        type=int,
        default=DEFAULT_ABANDONED_DAYS,
        help='Reject feeds whose latest post is older than this many days (default: 365)',
    )
    args = parser.parse_args(argv)

    if args.abandoned_days < 1:
        parser.error('--abandoned-days must be a positive integer')
    if args.pruned and args.abandoned_days != DEFAULT_ABANDONED_DAYS:
        parser.error('--abandoned-days cannot be used with --pruned because network checks are skipped')

    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.input:
        import_from_input(args.input, args.sources, args.dry_run, args.abandoned_days)
    else:
        import_from_pruned(args.pruned, args.sources, args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
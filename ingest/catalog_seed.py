"""Parsing of the seed files that describe what to collect.

These are the one-time bootstrap lists: RSS feed URLs and subreddit names,
reviewed and committed as plain text. Both the collector-side tooling and the
destination-specific bootstrap need to read them, so the parsing and the key
normalization live here rather than inside either importer.

Deliberately free of any database code. The normalization rules are the
important part: ``normalized_url`` and ``normalized_name`` are the natural keys
that join a catalog entry to everything observed about it, so two components
disagreeing about them would silently split one feed into two.
"""

from pathlib import Path
from urllib.parse import urldefrag, urlsplit, urlunsplit

# Trailing characters that a URL picked out of prose almost never really ends
# with, because seed lists are frequently pasted from articles and chat.
TRAILING_PUNCTUATION = '.,;:!?)]}\''


def read_list_file(path) -> list[str]:
    """Read one entry per line, skipping blanks and ``#`` comments."""
    entries: list[str] = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        entries.append(stripped)
    return entries


# --- RSS feeds ------------------------------------------------------------


def normalize_feed_url(url: str) -> str:
    """Return the natural key for a feed URL.

    Lowercases scheme and host, drops the fragment, and keeps path and query,
    because a query string frequently *is* the feed selector. An empty path
    becomes ``/`` so ``https://example.com`` and ``https://example.com/`` are
    one feed rather than two.
    """
    cleaned, _fragment = urldefrag((url or '').strip())
    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or '/'
    return urlunsplit((scheme, netloc, path, parts.query, ''))


def read_feeds_file(path) -> list[str]:
    return read_list_file(path)


def clean_candidate_url(url: str) -> str:
    """Trim prose punctuation off a URL, or return '' if it isn't usable."""
    cleaned = (url or '').strip()
    while cleaned:
        last = cleaned[-1]
        if last == ')':
            # A closing bracket that the URL itself opened is part of the URL,
            # which is common for encyclopedia-style paths.
            if cleaned.count('(') >= cleaned.count(')'):
                break
            cleaned = cleaned[:-1]
        elif last in TRAILING_PUNCTUATION:
            cleaned = cleaned[:-1]
        else:
            break
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in {'http', 'https'} or not parts.netloc:
        return ''
    return cleaned


def seed_feed_pairs(path) -> list[tuple[str, str]]:
    """Read a feed seed file into de-duplicated ``(normalized, feed)`` pairs.

    This is the shape a catalog bootstrap needs regardless of where it is
    writing to, which is why it lives here instead of inside an importer that
    also opens a database.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in read_feeds_file(path):
        cleaned = clean_candidate_url(raw)
        if not cleaned:
            continue
        key = normalize_feed_url(cleaned)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((key, cleaned))
    return pairs


def resolve_seed_feed_urls(rss_config) -> list[str]:
    """Feed URLs from the configured seed file, or none when there isn't one."""
    if rss_config is None:
        return []
    seed_file = getattr(rss_config, 'seed_file', None)
    if seed_file and Path(seed_file).exists():
        return read_feeds_file(seed_file)
    return []


# --- Subreddits -----------------------------------------------------------


def clean_subreddit_name(subreddit: str) -> str:
    """Strip an ``r/`` prefix and surrounding slashes, preserving case."""
    # Strip the prefix before the trailing slashes, so a bare "r/" collapses to
    # nothing instead of surviving as a subreddit literally named "r".
    value = (subreddit or '').strip().lstrip('/')
    if value[:2].lower() == 'r/':
        value = value[2:]
    return value.strip('/')


def normalize_subreddit_name(subreddit: str) -> str:
    """Lowercase key used for de-duplication and the unique constraint."""
    return clean_subreddit_name(subreddit).lower()


def read_subreddits_file(path) -> list[str]:
    return read_list_file(path)


def resolve_seed_subreddit_names(reddit_config) -> list[str]:
    """Subreddit names from the seed file, falling back to the inline list."""
    if reddit_config is None:
        return []
    seed_file = getattr(reddit_config, 'seed_file', None)
    if seed_file and Path(seed_file).exists():
        return read_subreddits_file(seed_file)
    return list(getattr(reddit_config, 'subreddits', ()) or ())


def seed_subreddit_pairs(reddit_config) -> list[tuple[str, str]]:
    """Resolve seed names into de-duplicated ``(normalized, display)`` pairs."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in resolve_seed_subreddit_names(reddit_config):
        name = clean_subreddit_name(raw)
        key = normalize_subreddit_name(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        pairs.append((key, name))
    return pairs

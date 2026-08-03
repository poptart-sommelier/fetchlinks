"""The collection catalog: what the collector should be collecting.

Feed and subreddit identity is edited in the web admin, so it is canonical in
the destination database, not on the collecting machine. Handing the collector
a database connection to read it would defeat the whole boundary, so instead a
publisher exports this small file and the collector reads only the file.

That indirection buys a useful property: when the destination is unreachable,
the collector keeps working from the last good snapshot instead of stopping.
Only a machine that has never synced has nothing to fall back to.

The catalog carries identity and nothing else -- no health, no counters, no
cursors. Anything the collector could derive itself stays in collector state,
so a stale catalog can never roll back a resume position.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .atomic import atomic_write_text
from .contract import (
    ContractError,
    dumps_document,
    to_timestamp,
    utc_now,
    validate_against,
)

CATALOG_VERSION = 1
CATALOG_SCHEMA_FILE = 'catalog.v1.json'


class CatalogError(ContractError):
    """The catalog snapshot is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class CatalogFeed:
    feed_url: str
    normalized_url: str

    def to_dict(self) -> dict:
        return {'feed_url': self.feed_url, 'normalized_url': self.normalized_url}


@dataclass(frozen=True)
class CatalogSubreddit:
    name: str
    normalized_name: str

    def to_dict(self) -> dict:
        return {'name': self.name, 'normalized_name': self.normalized_name}


def compute_revision(feeds, subreddits) -> str:
    """Digest the entries so an unchanged catalog keeps the same revision.

    Derived from content rather than assigned at export time, so a batch's
    ``catalog_revision`` answers "which subscription list produced this?"
    instead of "when was the exporter last run?". Two exporters that agree on
    the list will agree on the revision.
    """
    digest = hashlib.sha256()
    digest.update(f'catalog.v{CATALOG_VERSION}\n'.encode('utf-8'))
    digest.update(b'rss_feeds\n')
    for feed in sorted(feeds, key=lambda item: item.normalized_url):
        digest.update(f'{feed.normalized_url}\t{feed.feed_url}\n'.encode('utf-8'))
    digest.update(b'subreddits\n')
    for subreddit in sorted(subreddits, key=lambda item: item.normalized_name):
        digest.update(f'{subreddit.normalized_name}\t{subreddit.name}\n'.encode('utf-8'))
    return digest.hexdigest()


class Catalog:
    """An immutable snapshot of what to collect."""

    def __init__(self, feeds=(), subreddits=(), *, generated_at=None,
                 source='unknown', revision=None):
        self.feeds = tuple(sorted(feeds, key=lambda item: item.normalized_url))
        self.subreddits = tuple(sorted(subreddits, key=lambda item: item.normalized_name))
        self.generated_at = to_timestamp(generated_at) if generated_at else utc_now()
        self.source = source
        self.revision = revision or compute_revision(self.feeds, self.subreddits)

    def __repr__(self) -> str:
        return (f'<Catalog revision={self.revision[:12]} feeds={len(self.feeds)} '
                f'subreddits={len(self.subreddits)}>')

    @property
    def is_empty(self) -> bool:
        return not self.feeds and not self.subreddits

    @property
    def feed_urls(self) -> tuple:
        """``(normalized_url, feed_url)`` pairs, in stable order."""
        return tuple((feed.normalized_url, feed.feed_url) for feed in self.feeds)

    @property
    def normalized_feed_urls(self) -> frozenset:
        return frozenset(feed.normalized_url for feed in self.feeds)

    @property
    def normalized_subreddit_names(self) -> frozenset:
        return frozenset(item.normalized_name for item in self.subreddits)

    def to_dict(self) -> dict:
        return {
            'catalog_version': CATALOG_VERSION,
            'revision': self.revision,
            'generated_at': self.generated_at,
            'source': self.source,
            'rss_feeds': [feed.to_dict() for feed in self.feeds],
            'subreddits': [item.to_dict() for item in self.subreddits],
        }

    @classmethod
    def from_dict(cls, document, *, context='catalog') -> 'Catalog':
        try:
            validate_against(CATALOG_SCHEMA_FILE, document, context=context)
        except CatalogError:
            raise
        except ContractError as exc:
            # Callers catch CatalogError; a schema failure is just another way
            # for the snapshot to be unusable.
            raise CatalogError(str(exc)) from exc
        feeds = [CatalogFeed(**entry) for entry in document['rss_feeds']]
        subreddits = [CatalogSubreddit(**entry) for entry in document['subreddits']]

        seen_feeds = {feed.normalized_url for feed in feeds}
        if len(seen_feeds) != len(feeds):
            raise CatalogError(f'{context}: duplicate normalized_url in rss_feeds')
        seen_subreddits = {item.normalized_name for item in subreddits}
        if len(seen_subreddits) != len(subreddits):
            raise CatalogError(f'{context}: duplicate normalized_name in subreddits')

        expected = compute_revision(feeds, subreddits)
        if document['revision'] != expected:
            # A mismatch means the file was hand-edited or truncated between
            # export and read. Trusting it would stamp batches with a revision
            # that never described this list.
            raise CatalogError(
                f'{context}: revision {document["revision"][:12]} does not match '
                f'its contents (expected {expected[:12]})'
            )

        return cls(
            feeds,
            subreddits,
            generated_at=document['generated_at'],
            source=document['source'],
            revision=document['revision'],
        )

    @classmethod
    def load(cls, path) -> 'Catalog':
        """Read a catalog snapshot, raising if it is absent or invalid."""
        path = Path(path)
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise CatalogError(
                f'No catalog snapshot at {path}. Run the publisher catalog sync '
                'before collecting for the first time.'
            ) from None
        except OSError as exc:
            raise CatalogError(f'Could not read catalog at {path}: {exc}') from exc

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CatalogError(f'Malformed catalog at {path}: {exc.msg}') from exc
        if not isinstance(document, dict):
            raise CatalogError(f'Malformed catalog at {path}: expected a JSON object')

        return cls.from_dict(document, context=str(path))

    def save(self, path) -> None:
        """Write the snapshot atomically so a collector never sees a half file."""
        document = self.to_dict()
        validate_against(CATALOG_SCHEMA_FILE, document, context='catalog')
        atomic_write_text(path, dumps_document(document))


def build_catalog(feed_pairs=(), subreddit_pairs=(), *, source='unknown',
                  generated_at=None) -> Catalog:
    """Build a catalog from ``(normalized, display)`` pairs.

    Both exporters and the seed bootstrap produce pairs in this shape, so the
    de-duplication rule lives here once: first entry wins, which keeps the
    result deterministic when a source list contains the same feed twice.
    """
    feeds: dict[str, CatalogFeed] = {}
    for normalized_url, feed_url in feed_pairs:
        if normalized_url and normalized_url not in feeds:
            feeds[normalized_url] = CatalogFeed(feed_url=feed_url,
                                               normalized_url=normalized_url)
    subreddits: dict[str, CatalogSubreddit] = {}
    for normalized_name, name in subreddit_pairs:
        if normalized_name and normalized_name not in subreddits:
            subreddits[normalized_name] = CatalogSubreddit(name=name,
                                                           normalized_name=normalized_name)
    return Catalog(feeds.values(), subreddits.values(), source=source,
                   generated_at=generated_at)

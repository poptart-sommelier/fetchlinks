"""Collector-owned local state: where each source was last read from.

This is deliberately not part of the batch contract. It is private to the
collecting host and exists so a collector can resume without asking any
database where it got to -- which is what allows the collector to run with no
database credentials and to keep working while the destination is offline.

The publisher's copy of the same cursors, carried in batches as checkpoint
records, is what lets a destination be rebuilt from scratch. Neither is
derived from the other, and that redundancy is intentional.
"""

import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import atomic, contract
from .contract import ContractError

logger = logging.getLogger(__name__)

STATE_VERSION = 1
STATE_FILENAME = 'collector-state.v1.json'


class StateError(ContractError):
    """The collector state file is unusable."""


class CollectorState:
    """Resume cursors and HTTP cache headers for the local collector."""

    def __init__(
        self,
        rss_cache: Mapping[str, Mapping[str, Any]] | None = None,
        checkpoints: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    ):
        self.rss_cache: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in (rss_cache or {}).items()
        }
        self.checkpoints: dict[str, dict[str, dict[str, Any]]] = {
            source_type: {key: dict(entry) for key, entry in streams.items()}
            for source_type, streams in (checkpoints or {}).items()
        }

    # -- persistence --------------------------------------------------------

    @classmethod
    def load(cls, path) -> 'CollectorState':
        """Read state from disk, or start empty when there is none yet.

        A missing file is a first run. A corrupt file is not: silently
        starting over would re-fetch every source from the beginning and
        re-publish months of posts, so it fails loudly instead.
        """
        path = Path(path)
        if not path.exists():
            logger.info('No collector state at %s; starting fresh', path)
            return cls()
        try:
            raw = path.read_text(encoding='utf-8')
        except OSError as exc:
            raise StateError(f'Could not read collector state at {path}: {exc}') from exc
        try:
            document = contract.loads_line(raw, context=str(path))
        except ContractError as exc:
            raise StateError(
                f'Collector state at {path} is corrupt: {exc}. Refusing to '
                'discard it; move it aside deliberately to start over.'
            ) from exc
        return cls.from_dict(document, source=str(path))

    def save(self, path) -> None:
        atomic.atomic_write_text(path, contract.dumps_document(self.to_dict()))

    @classmethod
    def from_dict(cls, document: Mapping[str, Any], *, source: str = 'collector state') -> 'CollectorState':
        try:
            contract.validate_against(
                contract.COLLECTOR_STATE_SCHEMA_FILE, document, context=source
            )
        except ContractError as exc:
            raise StateError(str(exc)) from exc
        return cls(
            rss_cache=document.get('rss_cache') or {},
            checkpoints=document.get('checkpoints') or {},
        )

    def to_dict(self) -> dict:
        return {
            'state_version': STATE_VERSION,
            'updated_at': contract.utc_now(),
            'rss_cache': self.rss_cache,
            'checkpoints': self.checkpoints,
        }

    # -- RSS conditional-request headers ------------------------------------

    def rss_headers(self, normalized_url: str) -> tuple[str | None, str | None]:
        entry = self.rss_cache.get(normalized_url) or {}
        return entry.get('etag'), entry.get('last_modified')

    def set_rss_headers(
        self,
        normalized_url: str,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        if not normalized_url:
            raise StateError('Cannot cache headers without a normalized feed URL')
        if not etag and not last_modified:
            self.rss_cache.pop(normalized_url, None)
            return
        self.rss_cache[normalized_url] = {
            'etag': etag or None,
            'last_modified': last_modified or None,
        }

    def retain_feeds(self, normalized_urls: Iterable[str]) -> int:
        """Drop cache entries for feeds that left the catalog.

        Without this the state file grows forever with headers for feeds
        nobody subscribes to any more.
        """
        keep = set(normalized_urls)
        stale = [url for url in self.rss_cache if url not in keep]
        for url in stale:
            del self.rss_cache[url]
        return len(stale)

    # -- source checkpoints -------------------------------------------------

    def checkpoint(self, source_type: str, source_key: str) -> str | None:
        """The cursor to resume from, or None when the source is unread."""
        entry = self.checkpoints.get(source_type, {}).get(source_key)
        return entry['cursor'] if entry else None

    def checkpoint_entry(self, source_type: str, source_key: str) -> dict | None:
        entry = self.checkpoints.get(source_type, {}).get(source_key)
        return dict(entry) if entry else None

    def set_checkpoint(
        self,
        source_type: str,
        source_key: str,
        cursor: str,
        *,
        observed_at: str | None = None,
        source_url: str | None = None,
    ) -> None:
        if not source_type or not source_key:
            raise StateError('A checkpoint needs both a source type and a source key')
        if cursor is None or str(cursor) == '':
            raise StateError(
                f'Refusing to store an empty cursor for {source_type}/{source_key}; '
                'that would silently restart the source from the beginning'
            )
        self.checkpoints.setdefault(source_type, {})[source_key] = {
            'cursor': str(cursor),
            'observed_at': contract.to_timestamp(observed_at) if observed_at else contract.utc_now(),
            'source_url': source_url or None,
        }

    def apply_checkpoints(self, records: Iterable[Any]) -> int:
        """Advance state from the same checkpoint records a batch carries.

        Called only once a batch is safely in ``ready``. If the collector dies
        before this, the next run re-reads from the older cursor and produces
        overlapping posts, which the publisher deduplicates -- duplicated work
        is recoverable, a skipped cursor range is not.
        """
        applied = 0
        for record in records:
            document = contract.as_dict(record)
            self.set_checkpoint(
                document['source_type'],
                document['source_key'],
                document['cursor'],
                observed_at=document.get('observed_at'),
                source_url=document.get('source_url'),
            )
            applied += 1
        return applied

    def retain_streams(self, source_type: str, source_keys: Iterable[str]) -> int:
        """Drop checkpoints for streams that left the catalog."""
        streams = self.checkpoints.get(source_type)
        if not streams:
            return 0
        keep = set(source_keys)
        stale = [key for key in streams if key not in keep]
        for key in stale:
            del streams[key]
        if not streams:
            del self.checkpoints[source_type]
        return len(stale)

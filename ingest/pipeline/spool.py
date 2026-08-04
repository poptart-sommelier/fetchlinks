"""The on-disk batch spool that decouples collection from publication.

A batch moves through one directory per state:

    staging -> ready -> processing -> published
                            \\-> failed

Every transition is a directory rename within a single filesystem, which the
kernel performs atomically. That is what lets the collector keep working while
the destination is unreachable, and what lets a publisher crash at any point
without losing or double-applying a batch.
"""

import datetime
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import atomic, contract
from .contract import ContractError

logger = logging.getLogger(__name__)

STAGE_STAGING = 'staging'
STAGE_READY = 'ready'
STAGE_PROCESSING = 'processing'
STAGE_PUBLISHED = 'published'
STAGE_FAILED = 'failed'

STAGES = (STAGE_STAGING, STAGE_READY, STAGE_PROCESSING, STAGE_PUBLISHED, STAGE_FAILED)

FAILURE_FILENAME = 'failure.json'

# Files a batch directory may contain that are not listed in the manifest.
_NON_RECORD_FILES = frozenset({contract.MANIFEST_FILENAME, FAILURE_FILENAME})

_READ_CHUNK = 1024 * 256


class SpoolError(RuntimeError):
    """The spool could not perform a requested operation."""


class BatchValidationError(ContractError):
    """A batch on disk does not match its manifest or the contract."""


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def batch_id_created_at(batch_id: str) -> datetime.datetime:
    """Recover the creation instant encoded in a batch id."""
    contract.validate_batch_id(batch_id)
    stamp = batch_id.split('-', 1)[0]
    return datetime.datetime.strptime(stamp, '%Y%m%dT%H%M%S%fZ').replace(tzinfo=UTC)


class _RecordFile:
    """A single NDJSON file being streamed into a staging batch."""

    def __init__(self, path: Path, kind: str, scope: str | None, observed_at: str | None):
        self.path = path
        self.kind = kind
        self.scope = scope
        self.observed_at = observed_at
        self.count = 0
        self._digest = hashlib.sha256()
        # newline='' keeps Python from translating '\n' to '\r\n' on Windows,
        # so a batch written on a dev machine hashes identically to one
        # written on the Pi.
        self._handle = path.open('w', encoding='utf-8', newline='')

    def write(self, record: dict) -> None:
        line = contract.dumps_line(record) + '\n'
        self._handle.write(line)
        self._digest.update(line.encode('utf-8'))
        self.count += 1

    def close(self) -> None:
        if self._handle.closed:
            return
        atomic.fsync_file(self._handle)
        self._handle.close()

    def abort(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def entry(self) -> contract.FileEntry:
        return contract.FileEntry(
            name=self.path.name,
            kind=self.kind,
            record_count=self.count,
            sha256=self._digest.hexdigest(),
            scope=self.scope,
            observed_at=self.observed_at,
        )


class BatchWriter:
    """Builds one batch under ``staging`` and promotes it to ``ready``.

    Used as a context manager. Leaving the block normally publishes the batch
    to ``ready``; leaving it via an exception discards the staging directory,
    so a collector that dies halfway through a run never leaves a partial
    batch that a publisher could mistake for a complete one.
    """

    def __init__(
        self,
        spool: 'Spool',
        *,
        collector_version: str,
        collector_commit: str | None = None,
        catalog_revision: str | None = None,
        discard_if_empty: bool = True,
        batch_id: str | None = None,
    ):
        self._spool = spool
        self.batch_id = contract.validate_batch_id(batch_id or contract.new_batch_id())
        self.path = spool.batch_path(STAGE_STAGING, self.batch_id)
        self._collector_version = collector_version
        self._collector_commit = collector_commit
        self._catalog_revision = catalog_revision
        self._discard_if_empty = discard_if_empty
        self._files: dict[tuple[str, str | None], _RecordFile] = {}
        self._closed = False
        self.discarded = False
        self.path.mkdir(parents=True, exist_ok=False)

    # -- context management -------------------------------------------------

    def __enter__(self) -> 'BatchWriter':
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self.abort()
            return False
        self.commit()
        return False

    # -- adding records -----------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """True when nothing at all was recorded.

        An opened-but-empty snapshot file still counts as content: "this
        account now follows nobody" is a real observation the publisher must
        apply.
        """
        return not self._files

    @property
    def record_count(self) -> int:
        return sum(handle.count for handle in self._files.values())

    def add_posts(self, records: Iterable[Any]) -> int:
        return self._append(contract.KIND_POSTS, records)

    def add_rss_observations(self, records: Iterable[Any]) -> int:
        return self._append(contract.KIND_RSS_OBSERVATIONS, records)

    def add_checkpoints(self, records: Iterable[Any]) -> int:
        return self._append(contract.KIND_CHECKPOINTS, records)

    def set_bluesky_follows(self, records: Iterable[Any], *, observed_at: str | None = None) -> int:
        """Record the complete set of Bluesky follows at a point in time."""
        return self._append(
            contract.KIND_BLUESKY_FOLLOWS,
            records,
            observed_at=contract.to_timestamp(observed_at) if observed_at else contract.utc_now(),
            snapshot=True,
        )

    def set_mastodon_follows(
        self,
        scope: str,
        records: Iterable[Any],
        *,
        observed_at: str | None = None,
    ) -> int:
        """Record the complete set of follows for one Mastodon instance."""
        return self._append(
            contract.KIND_MASTODON_FOLLOWS,
            records,
            scope=contract.normalize_scope(scope),
            observed_at=contract.to_timestamp(observed_at) if observed_at else contract.utc_now(),
            snapshot=True,
        )

    def _append(
        self,
        kind: str,
        records: Iterable[Any],
        *,
        scope: str | None = None,
        observed_at: str | None = None,
        snapshot: bool = False,
    ) -> int:
        if self._closed:
            raise SpoolError(f'Batch {self.batch_id} is already closed')

        key = (kind, scope)
        handle = self._files.get(key)
        if handle is None:
            name = contract.file_name_for(kind, scope)
            handle = _RecordFile(self.path / name, kind, scope, observed_at)
            self._files[key] = handle
        elif snapshot:
            # Appending to a snapshot would turn a complete picture into an
            # ambiguous one, so refuse rather than silently produce a file the
            # publisher would apply as a full replacement.
            raise SpoolError(
                f'Snapshot {kind}'
                + (f' for scope {scope!r}' if scope else '')
                + f' was already recorded in batch {self.batch_id}'
            )

        written = 0
        for record in records:
            document = contract.as_dict(record)
            contract.validate_record(
                kind, document, context=f'{handle.path.name} record {handle.count + 1}'
            )
            handle.write(document)
            written += 1
        return written

    # -- completion ---------------------------------------------------------

    def commit(self) -> str | None:
        """Seal the batch and move it to ``ready``.

        Returns the batch id, or None when an empty batch was discarded.
        """
        if self._closed:
            raise SpoolError(f'Batch {self.batch_id} is already closed')
        self._closed = True

        for handle in self._files.values():
            handle.close()

        if self.is_empty and self._discard_if_empty:
            atomic.remove_directory(self.path)
            self.discarded = True
            logger.debug('Discarded empty batch %s', self.batch_id)
            return None

        manifest = contract.Manifest(
            batch_id=self.batch_id,
            created_at=contract.utc_now(),
            collector_version=self._collector_version,
            collector_commit=self._collector_commit,
            catalog_revision=self._catalog_revision,
            files=tuple(
                self._files[key].entry()
                for key in sorted(self._files, key=lambda k: (k[0], k[1] or ''))
            ),
        )
        document = manifest.to_dict()
        # Validate before promoting: a batch that reaches `ready` is a promise
        # that it is readable, and it is far cheaper to break that promise here
        # than in the publisher.
        contract.validate_against(
            contract.MANIFEST_SCHEMA_FILE, document, context=contract.MANIFEST_FILENAME
        )
        manifest.check_consistency()

        atomic.atomic_write_text(
            self.path / contract.MANIFEST_FILENAME, contract.dumps_document(document)
        )
        atomic.fsync_directory(self.path)

        destination = self._spool.batch_path(STAGE_READY, self.batch_id)
        atomic.atomic_move_directory(self.path, destination)
        self.path = destination
        logger.info('Queued batch %s with %d records', self.batch_id, manifest.total_records)
        return self.batch_id

    def abort(self) -> None:
        """Throw the staging directory away."""
        if self._closed:
            return
        self._closed = True
        self.discarded = True
        for handle in self._files.values():
            handle.abort()
        atomic.remove_directory(self.path)
        logger.warning('Discarded incomplete batch %s', self.batch_id)


@dataclass
class _FailureNote:
    reason: str
    failed_at: str


class ClaimedBatch:
    """A batch a publisher has exclusively taken ownership of.

    Ownership is established by the move into ``processing``: a second
    publisher attempting the same move simply fails and picks another batch.
    """

    def __init__(self, spool: 'Spool', batch_id: str):
        self._spool = spool
        self.batch_id = batch_id
        self.path = spool.batch_path(STAGE_PROCESSING, batch_id)
        self._manifest: contract.Manifest | None = None
        self._verified = False
        self._resolved = False

    def __repr__(self) -> str:
        return f'<ClaimedBatch {self.batch_id}>'

    @property
    def created_at(self) -> datetime.datetime:
        return batch_id_created_at(self.batch_id)

    @property
    def manifest(self) -> contract.Manifest:
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest

    def _load_manifest(self) -> contract.Manifest:
        path = self.path / contract.MANIFEST_FILENAME
        if not path.is_file():
            raise BatchValidationError(f'Batch {self.batch_id} has no {contract.MANIFEST_FILENAME}')
        try:
            document = contract.loads_line(
                path.read_text(encoding='utf-8'), context=contract.MANIFEST_FILENAME
            )
        except ContractError as exc:
            raise BatchValidationError(str(exc)) from exc
        try:
            manifest = contract.Manifest.from_dict(document)
        except ContractError as exc:
            raise BatchValidationError(str(exc)) from exc
        if manifest.batch_id != self.batch_id:
            raise BatchValidationError(
                f'Batch directory {self.batch_id} contains a manifest for '
                f'{manifest.batch_id}'
            )
        return manifest

    def verify(self) -> contract.Manifest:
        """Fully validate the batch: contents, counts, checksums, and schemas.

        Cheap enough to always run, and it is the only thing standing between a
        truncated or tampered file and the destination database.
        """
        if self._verified:
            return self.manifest
        manifest = self.manifest

        present = {
            item.name
            for item in self.path.iterdir()
            if item.is_file() and item.name not in _NON_RECORD_FILES
        }
        declared = {entry.name for entry in manifest.files}
        missing = sorted(declared - present)
        if missing:
            raise BatchValidationError(
                f'Batch {self.batch_id} is missing declared files: {", ".join(missing)}'
            )
        extra = sorted(present - declared)
        if extra:
            # An undeclared file means the batch is not what its manifest says
            # it is; publishing it would apply an unknown, unchecked payload.
            raise BatchValidationError(
                f'Batch {self.batch_id} contains undeclared files: {", ".join(extra)}'
            )

        for entry in manifest.files:
            path = self.path / entry.name
            digest = _sha256_of(path)
            if digest != entry.sha256:
                raise BatchValidationError(
                    f'Batch {self.batch_id} file {entry.name} failed its checksum '
                    f'(expected {entry.sha256}, found {digest})'
                )
            count = sum(1 for _ in self._iter_validated(entry))
            if count != entry.record_count:
                raise BatchValidationError(
                    f'Batch {self.batch_id} file {entry.name} holds {count} records '
                    f'but the manifest declares {entry.record_count}'
                )

        self._verified = True
        return manifest

    def _iter_validated(self, entry: contract.FileEntry) -> Iterator[dict]:
        path = self.path / entry.name
        with path.open('r', encoding='utf-8', newline='') as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    raise BatchValidationError(
                        f'Batch {self.batch_id} file {entry.name} line {number} is blank'
                    )
                context = f'{entry.name} line {number}'
                try:
                    record = contract.loads_line(stripped, context=context)
                    contract.validate_record(entry.kind, record, context=context)
                except ContractError as exc:
                    raise BatchValidationError(f'Batch {self.batch_id} {exc}') from exc
                yield record

    def records(self, kind: str, scope: str | None = None) -> Iterator[dict]:
        """Stream validated records of one kind, or nothing if absent."""
        self.verify()
        entry = self.manifest.entry_for(kind, scope)
        if entry is None:
            return iter(())
        return self._iter_validated(entry)

    def snapshots(self, kind: str) -> Iterator[tuple[contract.FileEntry, Iterator[dict]]]:
        """Yield every scoped snapshot of a kind with its manifest entry."""
        self.verify()
        for entry in self.manifest.entries_of_kind(kind):
            yield entry, self._iter_validated(entry)

    # -- resolution ---------------------------------------------------------

    def mark_published(self) -> None:
        """Archive a batch whose content is committed at the destination.

        If the archive already holds this batch id, the archive step has
        already run and only the local cleanup was left outstanding. Batch
        directories are immutable and ids are unique, so the archived copy is
        this batch; the redundant working copy is discarded rather than left
        in ``processing`` to stop the drain on every subsequent run.
        """
        destination = self._spool.batch_path(STAGE_PUBLISHED, self.batch_id)
        if destination.exists():
            if self._resolved:
                raise SpoolError(f'Batch {self.batch_id} has already been resolved')
            atomic.remove_directory(self.path)
            self.path = destination
            self._resolved = True
            logger.info('Batch %s was already archived; discarded the working copy', self.batch_id)
            return
        self._resolve(STAGE_PUBLISHED)
        logger.info('Archived published batch %s', self.batch_id)

    def mark_failed(self, reason: str) -> None:
        """Quarantine a batch that can never succeed.

        Reserved for permanent problems such as a malformed batch. Transient
        failures must leave the batch in ``processing`` so the next run retries
        it, because quarantining a batch the destination merely could not
        accept right now would lose data.
        """
        note = contract.dumps_document({
            'batch_id': self.batch_id,
            'reason': str(reason),
            'failed_at': contract.utc_now(),
        })
        try:
            atomic.atomic_write_text(self.path / FAILURE_FILENAME, note)
        except OSError:
            logger.exception('Could not record failure note for batch %s', self.batch_id)
        self._resolve(STAGE_FAILED)
        logger.error('Quarantined batch %s: %s', self.batch_id, reason)

    def release(self) -> None:
        """Return the batch to ``ready`` so it is retried from scratch."""
        self._resolve(STAGE_READY)
        logger.warning('Released batch %s back to the queue', self.batch_id)

    def _resolve(self, stage: str) -> None:
        if self._resolved:
            raise SpoolError(f'Batch {self.batch_id} has already been resolved')
        destination = self._spool.batch_path(stage, self.batch_id)
        atomic.atomic_move_directory(self.path, destination)
        self.path = destination
        self._resolved = True


class Spool:
    """The batch queue rooted at a single directory."""

    def __init__(self, root):
        self.root = Path(root)

    def __repr__(self) -> str:
        return f'<Spool {self.root}>'

    def initialize(self) -> 'Spool':
        for stage in STAGES:
            (self.root / stage).mkdir(parents=True, exist_ok=True)
        atomic.fsync_directory(self.root)
        return self

    def stage_dir(self, stage: str) -> Path:
        if stage not in STAGES:
            raise SpoolError(f'Unknown spool stage {stage!r}')
        return self.root / stage

    def batch_path(self, stage: str, batch_id: str) -> Path:
        # Validate before joining: a batch id reaches here from a manifest or a
        # directory listing, and neither is trustworthy enough to paste into a
        # path unchecked.
        return self.stage_dir(stage) / contract.validate_batch_id(batch_id)

    def batch_ids(self, stage: str) -> list[str]:
        """Batch ids in FIFO order, ignoring anything that is not a batch."""
        directory = self.stage_dir(stage)
        if not directory.is_dir():
            return []
        ids = []
        for item in directory.iterdir():
            if not item.is_dir():
                continue
            if not contract.BATCH_ID_RE.match(item.name):
                logger.warning('Ignoring unrecognized entry %s in %s', item.name, stage)
                continue
            ids.append(item.name)
        # Batch ids are time-ordered, so sorting names sorts by creation time.
        return sorted(ids)

    def new_batch(
        self,
        *,
        collector_version: str,
        collector_commit: str | None = None,
        catalog_revision: str | None = None,
        discard_if_empty: bool = True,
        batch_id: str | None = None,
    ) -> BatchWriter:
        self.initialize()
        return BatchWriter(
            self,
            collector_version=collector_version,
            collector_commit=collector_commit,
            catalog_revision=catalog_revision,
            discard_if_empty=discard_if_empty,
            batch_id=batch_id,
        )

    def claim_next(self) -> ClaimedBatch | None:
        """Take ownership of the oldest outstanding batch, if there is one.

        Anything already sitting in ``processing`` is claimed first: it is a
        batch a previous publisher died holding. Retrying it may re-apply work
        that already committed, which is exactly why the publisher records
        published batch ids at the destination and skips ones it has seen.
        """
        self.initialize()

        for batch_id in self.batch_ids(STAGE_PROCESSING):
            logger.warning('Recovering batch %s abandoned mid-publish', batch_id)
            return ClaimedBatch(self, batch_id)

        for batch_id in self.batch_ids(STAGE_READY):
            source = self.batch_path(STAGE_READY, batch_id)
            destination = self.batch_path(STAGE_PROCESSING, batch_id)
            try:
                atomic.atomic_move_directory(source, destination)
            except OSError:
                # Lost the race, or the batch vanished. Either way, move on.
                logger.debug('Could not claim batch %s; trying the next one', batch_id)
                continue
            return ClaimedBatch(self, batch_id)

        return None

    def claim_all(self) -> Iterator[ClaimedBatch]:
        """Claim outstanding batches one at a time, oldest first.

        The caller must resolve each batch before the next is claimed, which
        keeps publication strictly FIFO.
        """
        while True:
            batch = self.claim_next()
            if batch is None:
                return
            yield batch

    def prune_published(self, max_age_days: int, *, now: datetime.datetime | None = None) -> int:
        """Delete archived batches older than the retention window.

        Only ``published`` is pruned. Failed batches are kept indefinitely
        because they are the evidence needed to work out what went wrong.
        """
        if max_age_days <= 0:
            return 0
        cutoff = (now or datetime.datetime.now(UTC)) - datetime.timedelta(days=max_age_days)
        removed = 0
        for batch_id in self.batch_ids(STAGE_PUBLISHED):
            if batch_id_created_at(batch_id) >= cutoff:
                continue
            atomic.remove_directory(self.batch_path(STAGE_PUBLISHED, batch_id))
            removed += 1
        if removed:
            logger.info('Pruned %d published batches older than %d days', removed, max_age_days)
        return removed

    def queue_stats(self, *, now: datetime.datetime | None = None) -> dict:
        """Counts and queue age, for the monitoring the Pi deployment needs."""
        moment = now or datetime.datetime.now(UTC)
        counts = {stage: len(self.batch_ids(stage)) for stage in STAGES}
        outstanding = self.batch_ids(STAGE_READY) + self.batch_ids(STAGE_PROCESSING)
        oldest = min(outstanding) if outstanding else None
        return {
            'counts': counts,
            'oldest_outstanding_batch_id': oldest,
            'oldest_outstanding_age_seconds': (
                None if oldest is None
                else int((moment - batch_id_created_at(oldest)).total_seconds())
            ),
            'disk_bytes': self.disk_usage(),
        }

    def disk_usage(self) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
        return total

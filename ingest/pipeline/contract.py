"""Contract v1: the destination-neutral record types exchanged on disk.

The collector writes batches described by this contract; a publisher reads
them back and applies them to whatever storage it targets. Nothing in this
module may import a database driver, name a table, or accept a database URL --
that separation is the whole point of the contract.

The checked-in JSON Schemas in ``schemas/`` are normative. Validation here
runs the real schemas rather than re-implementing them, so the documented
contract and the enforced contract cannot drift apart.
"""

import datetime
import json
import re
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

# Contract versions are immutable. An incompatible change ships as version 2
# so a publisher can reject what it does not understand instead of guessing.
CONTRACT_VERSION = 1

SCHEMA_DIR = Path(__file__).resolve().parent / 'schemas'

# Record kinds. These are contract identifiers, not table names.
KIND_POSTS = 'posts'
KIND_RSS_OBSERVATIONS = 'rss_observations'
KIND_CHECKPOINTS = 'checkpoints'
KIND_BLUESKY_FOLLOWS = 'bluesky_follows'
KIND_MASTODON_FOLLOWS = 'mastodon_follows'

# Kinds whose files are complete snapshots rather than incremental records.
# A publisher must replace the whole scope, never merge.
SNAPSHOT_KINDS = frozenset({KIND_BLUESKY_FOLLOWS, KIND_MASTODON_FOLLOWS})

# Kinds that are partitioned per instance and therefore need a manifest scope.
SCOPED_KINDS = frozenset({KIND_MASTODON_FOLLOWS})

_SCHEMA_FILE_BY_KIND = {
    KIND_POSTS: 'post.v1.json',
    KIND_RSS_OBSERVATIONS: 'rss-observation.v1.json',
    KIND_CHECKPOINTS: 'checkpoint.v1.json',
    KIND_BLUESKY_FOLLOWS: 'bluesky-follow.v1.json',
    KIND_MASTODON_FOLLOWS: 'mastodon-follow.v1.json',
}

MANIFEST_FILENAME = 'manifest.json'
MANIFEST_SCHEMA_FILE = 'manifest.v1.json'
COLLECTOR_STATE_SCHEMA_FILE = 'collector-state.v1.json'

_FILENAME_BY_KIND = {
    KIND_POSTS: 'posts.ndjson',
    KIND_RSS_OBSERVATIONS: 'rss-observations.ndjson',
    KIND_CHECKPOINTS: 'checkpoints.ndjson',
    KIND_BLUESKY_FOLLOWS: 'bluesky-follows.ndjson',
}

# Scope tokens end up in file names, so keep them to a conservative,
# path-safe alphabet rather than trusting configuration.
_SCOPE_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')

TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


class ContractError(ValueError):
    """A record, manifest, or state document violates contract v1."""


# --- schema loading -------------------------------------------------------


def _load_all_schemas():
    schemas = {}
    for path in sorted(SCHEMA_DIR.glob('*.json')):
        with path.open('r', encoding='utf-8') as handle:
            schemas[path.name] = json.load(handle)
    return schemas


_SCHEMAS = _load_all_schemas()

# Resolve $ref locally. Schema ids are namespaced URLs purely for identity;
# nothing here should ever touch the network.
_REGISTRY = Registry().with_resources(
    (schema['$id'], Resource.from_contents(schema, default_specification=DRAFT202012))
    for schema in _SCHEMAS.values()
)

_VALIDATORS: dict[str, Draft202012Validator] = {}


def _validator(schema_file: str) -> Draft202012Validator:
    validator = _VALIDATORS.get(schema_file)
    if validator is None:
        try:
            schema = _SCHEMAS[schema_file]
        except KeyError:
            raise ContractError(f'Unknown schema {schema_file!r}') from None
        validator = Draft202012Validator(schema, registry=_REGISTRY)
        _VALIDATORS[schema_file] = validator
    return validator


def schema_file_for_kind(kind: str) -> str:
    try:
        return _SCHEMA_FILE_BY_KIND[kind]
    except KeyError:
        raise ContractError(f'Unknown record kind {kind!r}') from None


def validate_against(schema_file: str, document: Any, *, context: str = '') -> None:
    """Raise ContractError with the most specific message jsonschema can give."""
    errors = sorted(_validator(schema_file).iter_errors(document), key=lambda e: list(e.path))
    if not errors:
        return
    error = errors[0]
    location = '/'.join(str(part) for part in error.path)
    where = ': '.join(part for part in (context, location) if part)
    raise ContractError(f'{where}: {error.message}' if where else error.message)


def validate_record(kind: str, record: Mapping[str, Any], *, context: str = '') -> None:
    validate_against(schema_file_for_kind(kind), record, context=context)


# --- naming ---------------------------------------------------------------


def normalize_scope(scope: str) -> str:
    """Return a path-safe scope token or raise."""
    token = (scope or '').strip().lower()
    if not _SCOPE_RE.match(token):
        raise ContractError(
            f'Invalid scope {scope!r}; expected lowercase letters, digits, dot, '
            'dash or underscore'
        )
    return token


def file_name_for(kind: str, scope: str | None = None) -> str:
    if kind in SCOPED_KINDS:
        if not scope:
            raise ContractError(f'Record kind {kind!r} requires a scope')
        return f'mastodon-follows-{normalize_scope(scope)}.ndjson'
    try:
        return _FILENAME_BY_KIND[kind]
    except KeyError:
        raise ContractError(f'Unknown record kind {kind!r}') from None


# --- timestamps -----------------------------------------------------------


def to_timestamp(value: Any) -> str:
    """Normalize a datetime or date string to an RFC 3339 UTC second.

    Accepts aware and naive datetimes (naive is read as UTC, which is what the
    rest of the codebase already assumes), ISO 8601 strings, and the legacy
    ``YYYY-MM-DD HH:MM:SS`` form the SQLite schema used. Sub-second precision
    is truncated so that serialization is byte-for-byte deterministic.
    """
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ContractError('Empty timestamp')
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError:
            raise ContractError(f'Unparseable timestamp {value!r}') from None
    else:
        raise ContractError(f'Unsupported timestamp type {type(value).__name__}')

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).strftime(TIMESTAMP_FORMAT)


def utc_now() -> str:
    return datetime.datetime.now(UTC).replace(microsecond=0).strftime(TIMESTAMP_FORMAT)


# --- serialization --------------------------------------------------------


def dumps_line(record: Mapping[str, Any]) -> str:
    """Serialize one NDJSON record deterministically.

    Sorted keys and fixed separators mean the same record always produces the
    same bytes, which is what makes the manifest checksums meaningful.
    """
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def dumps_document(document: Mapping[str, Any]) -> str:
    """Serialize a whole-file JSON document so a human can read the diff."""
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def loads_line(line: str, *, context: str = '') -> dict:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ContractError(f'{context}: malformed JSON: {exc.msg}' if context
                            else f'malformed JSON: {exc.msg}') from exc
    if not isinstance(record, dict):
        raise ContractError(f'{context}: expected a JSON object' if context
                            else 'expected a JSON object')
    return record


def _clean_text(value: Any) -> str:
    return '' if value is None else str(value)


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


# --- record types ---------------------------------------------------------


@dataclass(frozen=True)
class PostRecord:
    """A post carrying external URLs, free of any storage-assigned identity."""

    unique_id: str
    source: str
    source_type: str
    posted_at: str
    urls: tuple[str, ...]
    author: str = ''
    description: str = ''
    direct_link: str = ''

    def to_dict(self) -> dict:
        return {
            'unique_id': self.unique_id,
            'source': self.source,
            'source_type': self.source_type,
            'author': _clean_text(self.author),
            'description': _clean_text(self.description),
            'direct_link': _clean_text(self.direct_link),
            'posted_at': to_timestamp(self.posted_at),
            'urls': list(self.urls),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'PostRecord':
        return cls(
            unique_id=record['unique_id'],
            source=record['source'],
            source_type=record['source_type'],
            posted_at=record['posted_at'],
            urls=tuple(record['urls']),
            author=record.get('author', ''),
            description=record.get('description', ''),
            direct_link=record.get('direct_link', ''),
        )


@dataclass(frozen=True)
class RssObservationRecord:
    """One fetch attempt against one feed.

    Carries the observation, not the consequence: whether this counts as a
    failure and what that does to a counter is the publisher's decision, which
    is what keeps replays from inflating health statistics.
    """

    normalized_url: str
    feed_url: str
    observed_at: str
    success: bool
    status: int | None = None
    error: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    latest_entry_at: str | None = None
    site_link: str | None = None

    def to_dict(self) -> dict:
        return {
            'normalized_url': self.normalized_url,
            'feed_url': self.feed_url,
            'observed_at': to_timestamp(self.observed_at),
            'success': bool(self.success),
            'status': None if self.status is None else int(self.status),
            'error': _clean_optional(self.error),
            'etag': _clean_optional(self.etag),
            'last_modified': _clean_optional(self.last_modified),
            'latest_entry_at': (None if self.latest_entry_at is None
                                else to_timestamp(self.latest_entry_at)),
            'site_link': _clean_optional(self.site_link),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'RssObservationRecord':
        return cls(**{key: record.get(key) for key in (
            'normalized_url', 'feed_url', 'observed_at', 'success', 'status',
            'error', 'etag', 'last_modified', 'latest_entry_at', 'site_link',
        )})


@dataclass(frozen=True)
class CheckpointRecord:
    """How far one stream of one source has been read."""

    source_type: str
    source_key: str
    cursor: str
    observed_at: str
    source_url: str | None = None

    def to_dict(self) -> dict:
        return {
            'source_type': self.source_type,
            'source_key': self.source_key,
            # Providers disagree about whether cursors are numbers or strings.
            # The contract settles it: always a string.
            'cursor': str(self.cursor),
            'observed_at': to_timestamp(self.observed_at),
            'source_url': _clean_optional(self.source_url),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'CheckpointRecord':
        return cls(
            source_type=record['source_type'],
            source_key=record['source_key'],
            cursor=record['cursor'],
            observed_at=record['observed_at'],
            source_url=record.get('source_url'),
        )


@dataclass(frozen=True)
class BlueskyFollowRecord:
    did: str
    handle: str
    display_name: str | None = None

    def to_dict(self) -> dict:
        return {
            'did': self.did,
            'handle': self.handle,
            'display_name': _clean_optional(self.display_name),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'BlueskyFollowRecord':
        return cls(
            did=record['did'],
            handle=record['handle'],
            display_name=record.get('display_name'),
        )


@dataclass(frozen=True)
class MastodonFollowRecord:
    account_id: str
    acct: str
    display_name: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return {
            'account_id': str(self.account_id),
            'acct': self.acct,
            'display_name': _clean_optional(self.display_name),
            'url': _clean_optional(self.url),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'MastodonFollowRecord':
        return cls(
            account_id=record['account_id'],
            acct=record['acct'],
            display_name=record.get('display_name'),
            url=record.get('url'),
        )


RECORD_CLASS_BY_KIND = {
    KIND_POSTS: PostRecord,
    KIND_RSS_OBSERVATIONS: RssObservationRecord,
    KIND_CHECKPOINTS: CheckpointRecord,
    KIND_BLUESKY_FOLLOWS: BlueskyFollowRecord,
    KIND_MASTODON_FOLLOWS: MastodonFollowRecord,
}


def as_dict(record: Any) -> dict:
    """Accept either a record dataclass or an already-plain mapping."""
    if hasattr(record, 'to_dict'):
        return record.to_dict()
    if isinstance(record, Mapping):
        return dict(record)
    raise ContractError(f'Cannot serialize {type(record).__name__} as a record')


# --- manifest -------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    name: str
    kind: str
    record_count: int
    sha256: str
    scope: str | None = None
    observed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'kind': self.kind,
            'record_count': self.record_count,
            'sha256': self.sha256,
            'scope': self.scope,
            'observed_at': self.observed_at,
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'FileEntry':
        return cls(
            name=record['name'],
            kind=record['kind'],
            record_count=record['record_count'],
            sha256=record['sha256'],
            scope=record.get('scope'),
            observed_at=record.get('observed_at'),
        )


@dataclass(frozen=True)
class Manifest:
    batch_id: str
    created_at: str
    collector_version: str
    collector_commit: str | None = None
    catalog_revision: str | None = None
    files: tuple[FileEntry, ...] = field(default_factory=tuple)
    contract_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict:
        return {
            'contract_version': self.contract_version,
            'batch_id': self.batch_id,
            'created_at': self.created_at,
            'collector_version': self.collector_version,
            'collector_commit': self.collector_commit,
            'catalog_revision': self.catalog_revision,
            'files': [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> 'Manifest':
        validate_against(MANIFEST_SCHEMA_FILE, document, context=MANIFEST_FILENAME)
        manifest = cls(
            batch_id=document['batch_id'],
            created_at=document['created_at'],
            collector_version=document['collector_version'],
            collector_commit=document.get('collector_commit'),
            catalog_revision=document.get('catalog_revision'),
            files=tuple(FileEntry.from_dict(entry) for entry in document['files']),
            contract_version=document['contract_version'],
        )
        manifest.check_consistency()
        return manifest

    def check_consistency(self) -> None:
        """Enforce the cross-field rules JSON Schema cannot express."""
        if self.contract_version != CONTRACT_VERSION:
            raise ContractError(
                f'Unsupported contract version {self.contract_version}; '
                f'this build understands version {CONTRACT_VERSION}'
            )
        seen = set()
        for entry in self.files:
            if entry.name in seen:
                raise ContractError(f'{MANIFEST_FILENAME}: duplicate file entry {entry.name!r}')
            seen.add(entry.name)
            expected = file_name_for(entry.kind, entry.scope)
            if entry.name != expected:
                raise ContractError(
                    f'{MANIFEST_FILENAME}: file {entry.name!r} does not match the '
                    f'name required for kind {entry.kind!r} (expected {expected!r})'
                )

    def entry_for(self, kind: str, scope: str | None = None) -> FileEntry | None:
        for entry in self.files:
            if entry.kind == kind and entry.scope == scope:
                return entry
        return None

    def entries_of_kind(self, kind: str) -> tuple[FileEntry, ...]:
        return tuple(entry for entry in self.files if entry.kind == kind)

    @property
    def total_records(self) -> int:
        return sum(entry.record_count for entry in self.files)


def validate_manifest_document(document: Mapping[str, Any]) -> Manifest:
    return Manifest.from_dict(document)


# --- batch identifiers ----------------------------------------------------

# Time-ordered and microsecond-resolved so a plain lexical sort of directory
# names is a correct FIFO order, with a random suffix to survive collisions.
BATCH_ID_RE = re.compile(r'^\d{8}T\d{12}Z-[0-9a-f]{8}$')


def new_batch_id(now: datetime.datetime | None = None) -> str:
    import uuid

    moment = (now or datetime.datetime.now(UTC)).astimezone(UTC)
    stamp = moment.strftime('%Y%m%dT%H%M%S') + f'{moment.microsecond:06d}' + 'Z'
    return f'{stamp}-{uuid.uuid4().hex[:8]}'


def validate_batch_id(batch_id: str) -> str:
    """Reject anything that is not a batch id before it is joined to a path."""
    if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
        raise ContractError(f'Invalid batch id {batch_id!r}')
    return batch_id


def iter_records(kind: str, records: Iterable[Any]) -> Iterable[dict]:
    for record in records:
        yield as_dict(record)

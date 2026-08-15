"""The batch contract: the destination-neutral record types exchanged on disk.

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

# Contract versions are immutable. An incompatible change ships as a new
# version so a publisher can reject what it does not understand instead of
# guessing.
#
# Writing and reading are separate questions. A collector writes exactly one
# version -- the newest it knows -- while a publisher must go on reading every
# version that could still be sitting in a spool, because an upgrade does not
# get to invalidate batches that were collected before it. Dropping a version
# from the readable set is a decision to abandon whatever is still queued in it.
CONTRACT_VERSION = 3
SUPPORTED_CONTRACT_VERSIONS = (1, 2, 3)

SCHEMA_DIR = Path(__file__).resolve().parent / 'schemas'

# Record kinds. These are contract identifiers, not table names.
KIND_POSTS = 'posts'
KIND_RSS_OBSERVATIONS = 'rss_observations'
KIND_CHECKPOINTS = 'checkpoints'
KIND_BLUESKY_FOLLOWS = 'bluesky_follows'
KIND_MASTODON_FOLLOWS = 'mastodon_follows'
KIND_COLLECTION_RUNS = 'collection_runs'

# Kinds whose files are complete snapshots rather than incremental records.
# A publisher must replace the whole scope, never merge.
SNAPSHOT_KINDS = frozenset({KIND_BLUESKY_FOLLOWS, KIND_MASTODON_FOLLOWS})

# Kinds that are partitioned per instance and therefore need a manifest scope.
SCOPED_KINDS = frozenset({KIND_MASTODON_FOLLOWS})

# Schemas per contract version. The table below is the current shape of each
# kind; a version that predates a change states its own older file. Naming the
# current one once means a new version does not have to restate every kind that
# did not change, which is how two copies of a mapping drift apart.
_SCHEMA_FILE_BY_KIND = {
    KIND_POSTS: 'post.v2.json',
    KIND_RSS_OBSERVATIONS: 'rss-observation.v1.json',
    KIND_CHECKPOINTS: 'checkpoint.v1.json',
    KIND_BLUESKY_FOLLOWS: 'bluesky-follow.v1.json',
    KIND_MASTODON_FOLLOWS: 'mastodon-follow.v1.json',
    KIND_COLLECTION_RUNS: 'collection-run.v1.json',
}

_SCHEMA_OVERRIDES_BY_VERSION = {
    1: {KIND_POSTS: 'post.v1.json'},
}

MANIFEST_FILENAME = 'manifest.json'
_MANIFEST_SCHEMA_BY_VERSION = {
    1: 'manifest.v1.json',
    2: 'manifest.v2.json',
    3: 'manifest.v3.json',
}
MANIFEST_SCHEMA_FILE = _MANIFEST_SCHEMA_BY_VERSION[CONTRACT_VERSION]
COLLECTOR_STATE_SCHEMA_FILE = 'collector-state.v1.json'

_FILENAME_BY_KIND = {
    KIND_POSTS: 'posts.ndjson',
    KIND_RSS_OBSERVATIONS: 'rss-observations.ndjson',
    KIND_CHECKPOINTS: 'checkpoints.ndjson',
    KIND_BLUESKY_FOLLOWS: 'bluesky-follows.ndjson',
    KIND_COLLECTION_RUNS: 'collection-runs.ndjson',
}

# Scope tokens end up in file names, so keep them to a conservative,
# path-safe alphabet rather than trusting configuration.
_SCOPE_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')

TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


class ContractError(ValueError):
    """A record, manifest, or state document violates the batch contract."""


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


def check_contract_version(version: Any) -> int:
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        supported = ', '.join(str(v) for v in SUPPORTED_CONTRACT_VERSIONS)
        raise ContractError(
            f'Unsupported contract version {version!r}; '
            f'this build reads version {supported}'
        )
    return version


def manifest_schema_file_for(contract_version: int = CONTRACT_VERSION) -> str:
    return _MANIFEST_SCHEMA_BY_VERSION[check_contract_version(contract_version)]


def schema_file_for_kind(kind: str, contract_version: int = CONTRACT_VERSION) -> str:
    check_contract_version(contract_version)
    overrides = _SCHEMA_OVERRIDES_BY_VERSION.get(contract_version, {})
    try:
        return overrides.get(kind) or _SCHEMA_FILE_BY_KIND[kind]
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


def validate_record(
    kind: str,
    record: Mapping[str, Any],
    *,
    context: str = '',
    contract_version: int = CONTRACT_VERSION,
) -> None:
    validate_against(
        schema_file_for_kind(kind, contract_version), record, context=context
    )


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


# --- failure vocabulary ---------------------------------------------------

# Results a job or one of its parts can end in. ``skipped`` is not a failure:
# a source that is switched off in configuration still deserves a line, so that
# "nothing from Reddit" reads as a setting rather than a fault.
RESULT_OK = 'ok'
RESULT_PARTIAL = 'partial'
RESULT_FAILED = 'failed'
RESULT_SKIPPED = 'skipped'

# Deliberately small and stable. Exception class names change when a library is
# upgraded, and then this week's counts stop being comparable with last week's.
ERROR_KIND_NONE = ''
ERROR_KIND_UNKNOWN = 'unknown'
ERROR_KINDS = (
    ERROR_KIND_NONE,
    'network',
    'timeout',
    'authentication',
    'rate_limit',
    'http',
    'invalid_response',
    'parse',
    ERROR_KIND_UNKNOWN,
)

ERROR_MESSAGE_MAX_LENGTH = 500


def clean_error_kind(value: Any) -> str:
    """Return a vocabulary term, mapping anything unrecognized to ``unknown``.

    Forgiving rather than strict on purpose. A run summary exists to explain a
    failure, so rejecting the summary because the failure was categorized with
    a typo would lose exactly the record that was worth keeping.
    """
    text = (value or '').strip().lower() if isinstance(value, str) else ''
    if not text:
        return ERROR_KIND_NONE
    return text if text in ERROR_KINDS else ERROR_KIND_UNKNOWN


def clean_error_message(value: Any) -> str:
    """Collapse to one bounded line.

    This is the only field carrying text from a stranger's website, so it is
    truncated here as well as constrained by the schema. The marker matters:
    a message that was cut should not read as one that simply ended.
    """
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    if len(text) <= ERROR_MESSAGE_MAX_LENGTH:
        return text
    return text[:ERROR_MESSAGE_MAX_LENGTH - 3] + '...'


def overall_result(results: Iterable[str]) -> str:
    """Combine part results into the result of the whole.

    Skipped parts are ignored -- a run with every source switched off is not a
    failure. With nothing left to judge, the run succeeded: collecting nothing
    because there was nothing to collect is a valid, quiet success.
    """
    considered = [result for result in results if result != RESULT_SKIPPED]
    if not considered:
        return RESULT_OK
    if all(result == RESULT_OK for result in considered):
        return RESULT_OK
    if any(result == RESULT_OK or result == RESULT_PARTIAL for result in considered):
        return RESULT_PARTIAL
    return RESULT_FAILED


# --- record types ---------------------------------------------------------


@dataclass(frozen=True)
class PostRecord:
    """A post carrying external URLs, free of any storage-assigned identity.

    ``channel_key`` and ``actor_key`` identify the origin this particular
    arrival came from. They matter because ``unique_id`` deliberately does not:
    it is a digest of the URL set, so two accounts linking the same article
    produce one post identity and two origins, and only these fields can tell
    the publisher that the second arrival was not simply a duplicate.

    Keys must be the most rename-proof identifier a source offers. Labels are
    for display and nothing else -- a rating attached to a display name would
    silently move to whoever adopted that name next.
    """

    unique_id: str
    source: str
    source_type: str
    posted_at: str
    urls: tuple[str, ...]
    author: str = ''
    description: str = ''
    direct_link: str = ''
    channel_key: str = ''
    channel_label: str = ''
    actor_key: str = ''
    actor_label: str = ''

    def to_dict(self) -> dict:
        return {
            'unique_id': self.unique_id,
            'source': self.source,
            'source_type': self.source_type,
            'author': _clean_text(self.author),
            'description': _clean_text(self.description),
            'direct_link': _clean_text(self.direct_link),
            'posted_at': to_timestamp(self.posted_at),
            'channel_key': _clean_text(self.channel_key),
            'channel_label': _clean_text(self.channel_label),
            'actor_key': _clean_text(self.actor_key),
            'actor_label': _clean_text(self.actor_label),
            'urls': list(self.urls),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'PostRecord':
        # The origin fields default to empty so a version 1 record still loads.
        # Empty is the truthful reading of one: the origin existed, the
        # collector of the day simply did not write down what it was.
        return cls(
            unique_id=record['unique_id'],
            source=record['source'],
            source_type=record['source_type'],
            posted_at=record['posted_at'],
            urls=tuple(record['urls']),
            author=record.get('author', ''),
            description=record.get('description', ''),
            direct_link=record.get('direct_link', ''),
            channel_key=record.get('channel_key', ''),
            channel_label=record.get('channel_label', ''),
            actor_key=record.get('actor_key', ''),
            actor_label=record.get('actor_label', ''),
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


@dataclass(frozen=True)
class SourceReport:
    """What one source did during one collection run.

    ``items_returned`` and ``posts_kept`` are both here because they answer
    different questions: a source being served nothing looks the same as one
    whose items are all being discarded if only the second is recorded.

    A source reports its own result rather than having it inferred from the
    post count. Zero posts is a perfectly good hour, and the sources disagree
    about how they signal trouble -- RSS returns an observation per feed, while
    others log and hand back an empty list.
    """

    source_type: str
    result: str = RESULT_OK
    elapsed_ms: int = 0
    channels_attempted: int = 0
    channels_succeeded: int = 0
    channels_failed: int = 0
    items_returned: int = 0
    posts_kept: int = 0
    errors: Mapping[str, int] = field(default_factory=dict)
    error_kind: str = ERROR_KIND_NONE
    error_message: str = ''

    def to_dict(self) -> dict:
        errors = {}
        for kind, count in (self.errors or {}).items():
            clean = clean_error_kind(kind)
            errors[clean] = errors.get(clean, 0) + int(count)
        return {
            'source_type': str(self.source_type),
            'result': self.result,
            'elapsed_ms': max(0, int(self.elapsed_ms)),
            'channels_attempted': max(0, int(self.channels_attempted)),
            'channels_succeeded': max(0, int(self.channels_succeeded)),
            'channels_failed': max(0, int(self.channels_failed)),
            'items_returned': max(0, int(self.items_returned)),
            'posts_kept': max(0, int(self.posts_kept)),
            'errors': {kind: count for kind, count in sorted(errors.items()) if count > 0},
            'error_kind': clean_error_kind(self.error_kind),
            'error_message': clean_error_message(self.error_message),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'SourceReport':
        return cls(
            source_type=record['source_type'],
            result=record.get('result', RESULT_OK),
            elapsed_ms=record.get('elapsed_ms', 0),
            channels_attempted=record.get('channels_attempted', 0),
            channels_succeeded=record.get('channels_succeeded', 0),
            channels_failed=record.get('channels_failed', 0),
            items_returned=record.get('items_returned', 0),
            posts_kept=record.get('posts_kept', 0),
            errors=dict(record.get('errors') or {}),
            error_kind=record.get('error_kind', ERROR_KIND_NONE),
            error_message=record.get('error_message', ''),
        )


@dataclass(frozen=True)
class SubtaskReport:
    """Work done alongside collection that can fail on its own.

    Refreshing a follows snapshot is the case that matters: it can fail while
    every post still arrives, and folding it into the source's result would
    report a healthy run as broken.
    """

    name: str
    scope: str = ''
    result: str = RESULT_OK
    elapsed_ms: int = 0
    error_kind: str = ERROR_KIND_NONE
    error_message: str = ''

    def to_dict(self) -> dict:
        return {
            'name': str(self.name),
            'scope': _clean_text(self.scope),
            'result': self.result,
            'elapsed_ms': max(0, int(self.elapsed_ms)),
            'error_kind': clean_error_kind(self.error_kind),
            'error_message': clean_error_message(self.error_message),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'SubtaskReport':
        return cls(
            name=record['name'],
            scope=record.get('scope', ''),
            result=record.get('result', RESULT_OK),
            elapsed_ms=record.get('elapsed_ms', 0),
            error_kind=record.get('error_kind', ERROR_KIND_NONE),
            error_message=record.get('error_message', ''),
        )


@dataclass(frozen=True)
class CollectionRunRecord:
    """What one collection attempt did.

    Exactly one of these belongs in every batch that reaches the spool, so that
    a run which collected nothing, and a run in which every source failed, are
    both visible afterwards rather than being indistinguishable from a run that
    never happened.

    It deliberately carries no identity of its own. The batch it travels in is
    its identity, which is what lets a replayed batch record its collection
    once rather than twice.
    """

    started_at: str
    finished_at: str
    elapsed_ms: int
    result: str = RESULT_OK
    posts_collected: int = 0
    error_kind: str = ERROR_KIND_NONE
    error_message: str = ''
    sources: tuple[SourceReport, ...] = field(default_factory=tuple)
    subtasks: tuple[SubtaskReport, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            'started_at': to_timestamp(self.started_at),
            'finished_at': to_timestamp(self.finished_at),
            'elapsed_ms': max(0, int(self.elapsed_ms)),
            'result': self.result,
            'posts_collected': max(0, int(self.posts_collected)),
            'error_kind': clean_error_kind(self.error_kind),
            'error_message': clean_error_message(self.error_message),
            'sources': [as_dict(report) for report in self.sources],
            'subtasks': [as_dict(report) for report in self.subtasks],
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> 'CollectionRunRecord':
        return cls(
            started_at=record['started_at'],
            finished_at=record['finished_at'],
            elapsed_ms=record['elapsed_ms'],
            result=record.get('result', RESULT_OK),
            posts_collected=record.get('posts_collected', 0),
            error_kind=record.get('error_kind', ERROR_KIND_NONE),
            error_message=record.get('error_message', ''),
            sources=tuple(
                SourceReport.from_dict(entry) for entry in record.get('sources') or ()
            ),
            subtasks=tuple(
                SubtaskReport.from_dict(entry) for entry in record.get('subtasks') or ()
            ),
        )


RECORD_CLASS_BY_KIND = {
    KIND_POSTS: PostRecord,
    KIND_RSS_OBSERVATIONS: RssObservationRecord,
    KIND_CHECKPOINTS: CheckpointRecord,
    KIND_BLUESKY_FOLLOWS: BlueskyFollowRecord,
    KIND_MASTODON_FOLLOWS: MastodonFollowRecord,
    KIND_COLLECTION_RUNS: CollectionRunRecord,
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
        # The version selects the schema, so it has to be read before the
        # document can be validated. Checking it first also turns a batch from
        # some future build into one clear message rather than a pile of
        # schema errors about a const that does not match.
        version = check_contract_version(document.get('contract_version'))
        validate_against(
            manifest_schema_file_for(version), document, context=MANIFEST_FILENAME
        )
        manifest = cls(
            batch_id=document['batch_id'],
            created_at=document['created_at'],
            collector_version=document['collector_version'],
            collector_commit=document.get('collector_commit'),
            catalog_revision=document.get('catalog_revision'),
            files=tuple(FileEntry.from_dict(entry) for entry in document['files']),
            contract_version=version,
        )
        manifest.check_consistency()
        return manifest

    def check_consistency(self) -> None:
        """Enforce the cross-field rules JSON Schema cannot express."""
        check_contract_version(self.contract_version)
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

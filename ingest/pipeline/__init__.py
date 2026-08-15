"""Destination-independent collection pipeline.

``pipeline`` is the boundary between collecting data and storing it:

- :mod:`pipeline.contract` defines the batch contract: the normalized record
  types written to disk, backed by the checked-in JSON Schemas in ``schemas/``.
  Versions are immutable, and a publisher reads every version that could still
  be sitting in a spool.
- :mod:`pipeline.collection` is what a collection cycle hands back before any
  of it is written down.
- :mod:`pipeline.catalog` is the snapshot of what to collect, exported by a
  publisher so the collector never needs database credentials.
- :mod:`pipeline.spool` is the crash-safe batch queue that carries those
  records from a collector to a publisher.
- :mod:`pipeline.state` holds the collector's private resume position.
- :mod:`pipeline.layout` resolves the runtime directory those live in.

Nothing under this package may import a database driver, reference a table
name, or read a database URL. That constraint is what lets the collector run
anywhere while the publisher stays specific to one destination.
"""

from .catalog import (
    CATALOG_VERSION,
    Catalog,
    CatalogError,
    CatalogFeed,
    CatalogSubreddit,
    build_catalog,
)
from .collection import CollectionResult, FollowsSnapshot
from .contract import (
    CONTRACT_VERSION,
    ERROR_KINDS,
    KIND_BLUESKY_FOLLOWS,
    KIND_CHECKPOINTS,
    KIND_COLLECTION_RUNS,
    KIND_MASTODON_FOLLOWS,
    KIND_POSTS,
    KIND_RSS_OBSERVATIONS,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_PARTIAL,
    RESULT_SKIPPED,
    SUPPORTED_CONTRACT_VERSIONS,
    BlueskyFollowRecord,
    CheckpointRecord,
    CollectionRunRecord,
    ContractError,
    FileEntry,
    Manifest,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
    SourceReport,
    SubtaskReport,
    clean_error_kind,
    clean_error_message,
    overall_result,
    to_timestamp,
    utc_now,
)
from .layout import RuntimeLayout
from .spool import BatchValidationError, BatchWriter, ClaimedBatch, Spool, SpoolError
from .state import CollectorState, StateError

__all__ = [
    'CATALOG_VERSION',
    'CONTRACT_VERSION',
    'ERROR_KINDS',
    'BatchValidationError',
    'BatchWriter',
    'BlueskyFollowRecord',
    'Catalog',
    'CatalogError',
    'CatalogFeed',
    'CatalogSubreddit',
    'CheckpointRecord',
    'ClaimedBatch',
    'CollectionResult',
    'CollectionRunRecord',
    'CollectorState',
    'ContractError',
    'FileEntry',
    'FollowsSnapshot',
    'KIND_BLUESKY_FOLLOWS',
    'KIND_CHECKPOINTS',
    'KIND_COLLECTION_RUNS',
    'KIND_MASTODON_FOLLOWS',
    'KIND_POSTS',
    'KIND_RSS_OBSERVATIONS',
    'Manifest',
    'MastodonFollowRecord',
    'PostRecord',
    'RESULT_FAILED',
    'RESULT_OK',
    'RESULT_PARTIAL',
    'RESULT_SKIPPED',
    'RssObservationRecord',
    'RuntimeLayout',
    'SUPPORTED_CONTRACT_VERSIONS',
    'SourceReport',
    'Spool',
    'SpoolError',
    'StateError',
    'SubtaskReport',
    'build_catalog',
    'clean_error_kind',
    'clean_error_message',
    'overall_result',
    'to_timestamp',
    'utc_now',
]

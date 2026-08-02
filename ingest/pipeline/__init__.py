"""Destination-independent collection pipeline.

``pipeline`` is the boundary between collecting data and storing it:

- :mod:`pipeline.contract` defines contract v1, the normalized record types
  written to disk, backed by the checked-in JSON Schemas in ``schemas/``.
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
    KIND_BLUESKY_FOLLOWS,
    KIND_CHECKPOINTS,
    KIND_MASTODON_FOLLOWS,
    KIND_POSTS,
    KIND_RSS_OBSERVATIONS,
    BlueskyFollowRecord,
    CheckpointRecord,
    ContractError,
    FileEntry,
    Manifest,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
    to_timestamp,
    utc_now,
)
from .layout import RuntimeLayout
from .spool import BatchValidationError, BatchWriter, ClaimedBatch, Spool, SpoolError
from .state import CollectorState, StateError

__all__ = [
    'CATALOG_VERSION',
    'CONTRACT_VERSION',
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
    'CollectorState',
    'ContractError',
    'FileEntry',
    'FollowsSnapshot',
    'KIND_BLUESKY_FOLLOWS',
    'KIND_CHECKPOINTS',
    'KIND_MASTODON_FOLLOWS',
    'KIND_POSTS',
    'KIND_RSS_OBSERVATIONS',
    'Manifest',
    'MastodonFollowRecord',
    'PostRecord',
    'RssObservationRecord',
    'RuntimeLayout',
    'Spool',
    'SpoolError',
    'StateError',
    'build_catalog',
    'to_timestamp',
    'utc_now',
]

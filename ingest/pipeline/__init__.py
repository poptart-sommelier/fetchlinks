"""Destination-independent collection pipeline.

``pipeline`` is the boundary between collecting data and storing it:

- :mod:`pipeline.contract` defines contract v1, the normalized record types
  written to disk, backed by the checked-in JSON Schemas in ``schemas/``.
- :mod:`pipeline.spool` is the crash-safe batch queue that carries those
  records from a collector to a publisher.
- :mod:`pipeline.state` holds the collector's private resume position.
- :mod:`pipeline.layout` resolves the runtime directory those live in.

Nothing under this package may import a database driver, reference a table
name, or read a database URL. That constraint is what lets the collector run
anywhere while the publisher stays specific to one destination.
"""

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
    'CONTRACT_VERSION',
    'BatchValidationError',
    'BatchWriter',
    'BlueskyFollowRecord',
    'CheckpointRecord',
    'ClaimedBatch',
    'CollectorState',
    'ContractError',
    'FileEntry',
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
    'to_timestamp',
    'utc_now',
]

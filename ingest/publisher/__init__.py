"""PostgreSQL Publisher.

The destination-specific half of the pipeline. Everything in this package may
know about PostgreSQL, table names, SQL and connection strings; nothing under
``pipeline/`` or on the collection path may. That asymmetry is the whole point
of the split, and ``tests/test_collector_boundary.py`` enforces it.
"""

from .connection import (
    PublisherConfigError,
    connect,
    resolve_database_url,
)
from .apply import PublishOutcome, apply_batch
from .drain import DrainReport, drain_ready

__all__ = [
    'PublisherConfigError',
    'connect',
    'resolve_database_url',
    'PublishOutcome',
    'apply_batch',
    'DrainReport',
    'drain_ready',
]

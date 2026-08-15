"""Write what each scheduled job did into ``content.operation_runs``.

Two quite different callers use this. Publisher-side jobs write their own rows
because they already hold the credential: they announce themselves the moment
they connect, and finish the row at the end. Collection runs arrive from
somewhere else entirely -- inside a batch, up to an hour late -- and are applied
inside that batch's transaction so a replay cannot record the same run twice.

Instrumentation must never be the reason publishing fails. Every function here
that is not part of the batch transaction swallows its own errors and logs
them: a heartbeat that could not be written leaves the page saying the
publisher has not reported, which is the truth, and is a far better outcome
than a drain abandoned over a bookkeeping row.
"""

from __future__ import annotations

import datetime
import json
import logging
import shutil
import time
from contextlib import contextmanager

import psycopg

from pipeline import contract

logger = logging.getLogger(__name__)

JOB_COLLECT = 'collect'
JOB_PUBLISH = 'publish'
JOB_CATALOG_SYNC = 'catalog-sync'
JOB_RETENTION = 'retention'

# Publisher-side failure categories. Deliberately separate from the collection
# vocabulary in `pipeline.contract`: a collector never touches a database, and a
# publisher never parses a stranger's RSS, so folding the two lists together
# would offer every reader half a dozen categories that cannot occur.
KIND_DATABASE = 'database'
KIND_FILESYSTEM = 'filesystem'
KIND_INVALID_BATCH = 'invalid_batch'
KIND_UNKNOWN = 'unknown'
KIND_NONE = ''

#: The column allows 40 characters.
_ERROR_KIND_MAX_LENGTH = 40

_START_RUN = """
INSERT INTO content.operation_runs (job, run_key, started_at, result, details)
VALUES (%s, %s, now(), 'running', %s)
RETURNING run_id
"""

_FINISH_RUN = """
UPDATE content.operation_runs
   SET finished_at = now(), result = %s, elapsed_ms = %s,
       error_kind = %s, error_message = %s, details = %s
 WHERE run_id = %s
"""

# The conflict target matches the partial unique index in migration 0006. A
# replayed batch hits it and applies nothing, exactly as it does in the batch
# ledger.
_RECORD_REPORTED_RUN = """
INSERT INTO content.operation_runs
    (job, run_key, started_at, finished_at, result, elapsed_ms,
     error_kind, error_message, details)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (job, run_key) WHERE run_key <> '' DO NOTHING
"""


def _details(value) -> str:
    """Serialize the job-specific metric set, refusing anything but an object.

    The column has the same check. Both exist because a caller passing a list
    would otherwise fail with a constraint message that says nothing about
    which job did it.
    """
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise TypeError(f'operation run details must be an object, got {type(value).__name__}')
    return json.dumps(value, sort_keys=True, default=str)


class RunRecorder:
    """A publisher-side run in progress.

    Details accumulate as the job learns things, so a run that dies partway
    through still reports what it knew -- the queue depth it found, for
    instance, which is often the whole answer.
    """

    def __init__(self, conn: psycopg.Connection, job: str, run_id: int | None,
                 details: dict | None = None):
        self._conn = conn
        self.job = job
        self.run_id = run_id
        # Seeded with whatever was known at the start, because finishing
        # rewrites the column outright: anything not carried forward here would
        # be silently dropped when the run completes.
        self.details: dict = dict(details or {})
        self.result = contract.RESULT_OK
        self._started = time.monotonic()

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def note(self, **facts) -> None:
        self.details.update(facts)

    def finish(self, result: str | None = None, *, error_kind: str = KIND_NONE,
               error_message: str = '') -> None:
        if self.run_id is None:
            return
        try:
            _rollback_quietly(self._conn)
            with self._conn.cursor() as cur:
                cur.execute(_FINISH_RUN, (
                    result or self.result,
                    self.elapsed_ms,
                    _bounded_kind(error_kind),
                    contract.clean_error_message(error_message),
                    _details(self.details),
                    self.run_id,
                ))
            self._conn.commit()
        except Exception as exc:
            logger.error('Could not finish the %s run record: %s', self.job, exc)
            _rollback_quietly(self._conn)


def _bounded_kind(value: str) -> str:
    return (value or '')[:_ERROR_KIND_MAX_LENGTH]


def _rollback_quietly(conn: psycopg.Connection) -> None:
    """Leave the connection usable, whatever state the caller left it in."""
    try:
        conn.rollback()
    except Exception:
        pass


def start_run(conn: psycopg.Connection, job: str, *, run_key: str = '',
              details: dict | None = None) -> RunRecorder:
    """Announce a run and commit it immediately.

    Committed on its own rather than with the work, so a process that is killed
    mid-drain leaves a visibly unfinished row. Silence and success must never
    look the same.
    """
    run_id = None
    try:
        _rollback_quietly(conn)
        with conn.cursor() as cur:
            cur.execute(_START_RUN, (job, run_key, _details(details)))
            run_id = cur.fetchone()[0]
        conn.commit()
    except Exception as exc:
        logger.error('Could not start the %s run record: %s', job, exc)
        _rollback_quietly(conn)
    return RunRecorder(conn, job, run_id, details)


@contextmanager
def record_run(conn: psycopg.Connection, job: str, *, run_key: str = ''):
    """Run a job with its own operation row, whatever the outcome.

    A job that raises still finishes its row, as failed, before the exception
    continues on its way. The row is the only durable account of what happened,
    so it must not be lost to the very failure it describes.
    """
    recorder = start_run(conn, job, run_key=run_key)
    try:
        yield recorder
    except Exception as exc:
        recorder.finish(
            contract.RESULT_FAILED,
            error_kind=error_kind_for(exc),
            error_message=describe_error(exc),
        )
        raise
    else:
        recorder.finish()


def error_kind_for(exc: BaseException) -> str:
    if isinstance(exc, psycopg.Error):
        return KIND_DATABASE
    if isinstance(exc, OSError):
        return KIND_FILESYSTEM
    return KIND_UNKNOWN


def describe_error(exc: BaseException) -> str:
    text = str(exc).strip()
    name = type(exc).__name__
    return contract.clean_error_message(f'{name}: {text}' if text else name)


def _timestamp(value: str | None):
    """Batch fields are RFC 3339 strings; the column wants an aware datetime.

    Parsed rather than handed over as text: psycopg sends a Python string with
    a text type, and PostgreSQL will not implicitly cast text to timestamptz.
    """
    if value is None:
        return None
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def record_collection_run(cur, batch_id: str, record) -> bool:
    """Store a collection run that arrived inside a batch.

    Runs on the batch's cursor on purpose: the run and the posts it describes
    become visible together, and a replayed batch conflicts here exactly as it
    does on the batch ledger.
    """
    run = contract.CollectionRunRecord.from_dict(record)
    details = {
        'batch_id': batch_id,
        'posts_collected': max(0, int(run.posts_collected)),
        'sources': [report.to_dict() for report in run.sources],
        'subtasks': [report.to_dict() for report in run.subtasks],
    }
    cur.execute(_RECORD_REPORTED_RUN, (
        JOB_COLLECT,
        batch_id,
        _timestamp(run.started_at),
        _timestamp(run.finished_at),
        run.result,
        max(0, int(run.elapsed_ms)),
        contract.clean_error_kind(run.error_kind) if run.error_kind else '',
        contract.clean_error_message(run.error_message),
        _details(details),
    ))
    return cur.rowcount == 1


def host_facts(path=None) -> dict:
    """A few things about the machine the publisher is running on.

    Uptime and boot id answer the question a status page cannot otherwise
    answer: whether a gap in the history was the Pi rebooting or the job
    failing. Free space is here because a full disk stops collection silently,
    and the spool is the first thing to notice.

    Every field is optional. These paths exist on the Pi and not on a developer
    laptop, and instrumentation must not care.
    """
    facts: dict = {}
    try:
        with open('/proc/uptime', encoding='ascii') as handle:
            facts['host_uptime_seconds'] = int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open('/proc/sys/kernel/random/boot_id', encoding='ascii') as handle:
            facts['host_boot_id'] = handle.read().strip()
    except OSError:
        pass
    if path is not None:
        try:
            facts['disk_free_bytes'] = shutil.disk_usage(path).free
        except OSError:
            pass
    return facts


_DATABASE_SIZE = 'SELECT pg_database_size(current_database())'


def database_size_bytes(conn: psycopg.Connection) -> int | None:
    """How much of the 0.5 GB is gone. None when it cannot be read."""
    try:
        with conn.cursor() as cur:
            cur.execute(_DATABASE_SIZE)
            return int(cur.fetchone()[0])
    except Exception as exc:
        logger.warning('Could not read the database size: %s', exc)
        _rollback_quietly(conn)
        return None

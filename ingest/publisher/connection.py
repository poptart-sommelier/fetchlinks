"""Connection handling for the PostgreSQL Publisher.

The database URL is never read from the application config file. It arrives
through the environment, so that the Collector's configuration — which does
live in a file, next to source credentials — has no place to put one even by
accident.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Iterator

import psycopg

logger = logging.getLogger(__name__)

#: Checked in order. ``FETCHLINKS_DATABASE_URL`` is preferred because a Pi that
#: also runs other PostgreSQL-backed tools should not have to share ``DATABASE_URL``.
ENV_VARS = ('FETCHLINKS_DATABASE_URL', 'DATABASE_URL')

#: Neon idles a compute to sleep; the first statement after that pays a wake-up
#: cost. Long enough to absorb it, short enough that a genuinely unreachable
#: database fails the systemd oneshot rather than hanging it.
DEFAULT_CONNECT_TIMEOUT = 30


class PublisherConfigError(RuntimeError):
    """The Publisher was asked to run without a usable database URL."""


def resolve_database_url(explicit: str | None = None, env=None) -> str:
    """Return the database URL, preferring an explicit argument.

    Raises rather than defaulting to localhost. A silent fallback would let a
    misconfigured Pi publish into a database nobody is reading.
    """
    if explicit:
        return explicit
    env = os.environ if env is None else env
    for name in ENV_VARS:
        value = (env.get(name) or '').strip()
        if value:
            return value
    raise PublisherConfigError(
        'No database URL. Set one of: ' + ', '.join(ENV_VARS)
    )


@contextlib.contextmanager
def connect(
    database_url: str | None = None,
    *,
    autocommit: bool = False,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    application_name: str = 'fetchlinks-publisher',
) -> Iterator[psycopg.Connection]:
    """Open a connection, closing it on the way out.

    Defaults to ``autocommit=False`` so that the caller has to be explicit
    about transaction boundaries; publishing a batch depends on it.
    """
    url = resolve_database_url(database_url)
    conn = psycopg.connect(
        url,
        autocommit=autocommit,
        connect_timeout=connect_timeout,
        application_name=application_name,
    )
    try:
        yield conn
    finally:
        conn.close()


def redact(database_url: str) -> str:
    """Return a URL safe to log: userinfo removed, host and database kept."""
    if '@' not in database_url:
        return database_url
    scheme, _, rest = database_url.partition('://')
    if not rest:
        return '<redacted>'
    _userinfo, _, host_part = rest.rpartition('@')
    return f'{scheme}://<redacted>@{host_part}' if scheme else f'<redacted>@{host_part}'

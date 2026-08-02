"""Shared harness for the PostgreSQL integration tests.

These tests need a real server. Mocking psycopg would leave every property
worth testing here — ON CONFLICT behaviour, transaction rollback, cascade
deletes, GRANT enforcement — verified against a fiction.

Point ``FETCHLINKS_TEST_DATABASE_URL`` at a database you do not mind losing::

    docker run -d --name fetchlinks-pg -e POSTGRES_PASSWORD=fetchlinks \\
        -p 55432:5432 postgres:17
    $env:FETCHLINKS_TEST_DATABASE_URL =
        'postgresql://postgres:fetchlinks@localhost:55432/postgres'

Without it the tests skip rather than fail, so the suite still runs on a
machine that has no PostgreSQL.
"""

from __future__ import annotations

import os
import unittest
from urllib.parse import urlsplit, urlunsplit

try:
    import psycopg
    from psycopg import sql
except ImportError:  # pragma: no cover - psycopg is a hard requirement in prod
    psycopg = None
    sql = None

from publisher.migrations import migrate

ENV_VAR = 'FETCHLINKS_TEST_DATABASE_URL'

#: Created and dropped by the harness, so the admin URL can point at any
#: database without the tests writing into it.
TEST_DATABASE = 'fetchlinks_test'

TEST_ROLE_PASSWORD = 'test-only-password'

_ALL_TABLES = (
    'content.post_urls',
    'content.posts',
    'content.rss_feed_health',
    'content.reddit_state',
    'content.bluesky_state',
    'content.mastodon_state',
    'content.follows_snapshots',
    'content.bluesky_follows',
    'content.mastodon_follows',
    'content.published_batches',
    'catalog.rss_feeds',
    'catalog.subreddits',
)


def admin_url() -> str | None:
    value = (os.environ.get(ENV_VAR) or '').strip()
    return value or None


def available() -> bool:
    return psycopg is not None and admin_url() is not None


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f'/{database}',
                       parts.query, parts.fragment))


def _with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or 'localhost'
    port = f':{parts.port}' if parts.port else ''
    netloc = f'{user}:{password}@{host}{port}'
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def create_test_database() -> str:
    """(Re)create the scratch database and return its URL."""
    base = admin_url()
    with psycopg.connect(base, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT 1 FROM pg_database WHERE datname = %s', (TEST_DATABASE,)
        )
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE {TEST_DATABASE}')
    return _with_database(base, TEST_DATABASE)


def role_url(role: str) -> str:
    return _with_credentials(
        _with_database(admin_url(), TEST_DATABASE), role, TEST_ROLE_PASSWORD
    )


def set_role_passwords(conn) -> None:
    """Give the runtime roles a password so the permission tests can log in.

    Migrations deliberately create them without one; production passwords are
    an operator step and never come from code.
    """
    with conn.cursor() as cur:
        for role in ('fetchlinks_web', 'fetchlinks_publisher'):
            # ALTER ROLE ... PASSWORD takes a literal, not a bind parameter.
            cur.execute(sql.SQL('ALTER ROLE {} WITH PASSWORD {}').format(
                sql.Identifier(role), sql.Literal(TEST_ROLE_PASSWORD)
            ))
    conn.commit()


@unittest.skipUnless(available(), f'set {ENV_VAR} to run PostgreSQL tests')
class PostgresTestCase(unittest.TestCase):
    """Migrated, empty schema per test.

    The database is created once and truncated between tests rather than
    recreated, because re-running the migrations for every test would dominate
    the runtime without testing anything the migration tests do not.
    """

    database_url: str

    @classmethod
    def setUpClass(cls):
        cls.database_url = create_test_database()
        with psycopg.connect(cls.database_url) as conn:
            migrate(conn)
            set_role_passwords(conn)

    def setUp(self):
        self.conn = psycopg.connect(self.database_url)
        self.addCleanup(self.conn.close)
        self.truncate()

    def truncate(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                'TRUNCATE ' + ', '.join(_ALL_TABLES) + ' RESTART IDENTITY CASCADE'
            )
        self.conn.commit()

    # -- small query helpers -------------------------------------------------
    #
    # Params default to None rather than (), so a query with a literal % (LIKE
    # patterns, for instance) is not put through placeholder interpolation.

    def scalar(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or None)
            row = cur.fetchone()
        return None if row is None else row[0]

    def rows(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or None)
            return cur.fetchall()

    def count(self, table: str) -> int:
        return self.scalar(f'SELECT count(*) FROM {table}')

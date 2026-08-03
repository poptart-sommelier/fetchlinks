"""Versioned SQL migration runner.

Deliberately small and SQL-first: the runner discovers ``NNNN_name.sql`` files,
applies the ones not yet recorded, and records what it applied. It never
generates DDL, so the schema in the repository is the schema in the database,
readable without running any Python.

An applied migration's checksum is stored and re-checked. Editing a file that
has already run is the failure mode that quietly desynchronises environments,
so it is reported rather than ignored.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

MIGRATION_PATTERN = re.compile(r'^(\d{4})_[a-z0-9_]+\.sql$')

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version    text PRIMARY KEY,
    filename   text        NOT NULL,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration could not be discovered, verified, or applied."""


def default_migrations_dir() -> Path:
    """``<repo>/db/migrations``.

    Migrations live outside ``ingest/`` because the schema is shared with the
    web application; neither component owns it.
    """
    return Path(__file__).resolve().parents[2] / 'db' / 'migrations'


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name

    def read(self) -> str:
        return self.path.read_text(encoding='utf-8')

    def checksum(self) -> str:
        return hashlib.sha256(self.read().encode('utf-8')).hexdigest()


def discover(migrations_dir: Path | None = None) -> list[Migration]:
    """Return migrations in version order."""
    directory = Path(migrations_dir) if migrations_dir else default_migrations_dir()
    if not directory.is_dir():
        raise MigrationError(f'Migrations directory not found: {directory}')

    found: dict[str, Migration] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != '.sql':
            continue
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise MigrationError(
                f'Migration filename must look like 0001_name.sql: {path.name}'
            )
        version = match.group(1)
        if version in found:
            raise MigrationError(
                f'Duplicate migration version {version}: '
                f'{found[version].filename} and {path.name}'
            )
        found[version] = Migration(version=version, path=path)

    if not found:
        raise MigrationError(f'No migrations found in {directory}')
    return [found[key] for key in sorted(found)]


def applied_versions(conn: psycopg.Connection) -> dict[str, tuple[str, str]]:
    """Return ``{version: (filename, checksum)}`` already recorded."""
    with conn.cursor() as cur:
        cur.execute(LEDGER_DDL)
        cur.execute(
            'SELECT version, filename, checksum FROM public.schema_migrations'
        )
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def _verify_unchanged(migration: Migration, recorded: tuple[str, str]) -> None:
    recorded_filename, recorded_checksum = recorded
    if migration.checksum() == recorded_checksum:
        return
    raise MigrationError(
        f'Migration {migration.version} ({recorded_filename}) has changed since '
        f'it was applied. Add a new migration instead of editing an applied one.'
    )


def pending(conn: psycopg.Connection, migrations: list[Migration]) -> list[Migration]:
    """Return migrations not yet applied, verifying the ones that are."""
    recorded = applied_versions(conn)
    outstanding = []
    for migration in migrations:
        if migration.version in recorded:
            _verify_unchanged(migration, recorded[migration.version])
        else:
            outstanding.append(migration)
    return outstanding


def migrate(
    conn: psycopg.Connection,
    migrations_dir: Path | None = None,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Apply outstanding migrations; return the versions applied.

    Each migration and its ledger row commit together, so an interrupted run
    leaves a prefix of migrations applied and correctly recorded rather than a
    database that claims to be at a version it is not.
    """
    migrations = discover(migrations_dir)
    outstanding = pending(conn, migrations)
    conn.commit()

    if dry_run:
        return [m.version for m in outstanding]

    applied = []
    for migration in outstanding:
        logger.info('Applying migration %s (%s)', migration.version, migration.filename)
        try:
            with conn.cursor() as cur:
                cur.execute(migration.read())
                cur.execute(
                    'INSERT INTO public.schema_migrations '
                    '(version, filename, checksum) VALUES (%s, %s, %s)',
                    (migration.version, migration.filename, migration.checksum()),
                )
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            raise MigrationError(
                f'Migration {migration.version} ({migration.filename}) failed: {exc}'
            ) from exc
        applied.append(migration.version)
    return applied

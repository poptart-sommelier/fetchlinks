"""Crash-safe filesystem primitives shared by the spool and collector state.

The batch spool's whole safety argument rests on two guarantees: a file is
either fully present or absent, and a directory appears in its destination
only once it is complete. These helpers provide both, portably enough to
develop on Windows while running in production on Linux.
"""

import os
import shutil
import tempfile
from pathlib import Path


def fsync_file(handle) -> None:
    """Flush Python buffers and ask the OS to commit the file to disk."""
    handle.flush()
    os.fsync(handle.fileno())


def fsync_directory(path) -> None:
    """Commit a directory entry so a rename survives a power loss.

    Only meaningful on POSIX. Windows has no equivalent handle to fsync, and
    opening a directory there fails outright, so this is a no-op on Windows.
    """
    if os.name != 'posix':
        return
    fd = os.open(os.fspath(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path, data: bytes) -> None:
    """Write a file so readers see either the old content or the new content.

    A partially written file would be indistinguishable from a valid one after
    a crash, which for collector state would mean silently losing resume
    position, so the write goes to a sibling temp file and is renamed into
    place only once it is durable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            fsync_file(handle)
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a half-written temp file behind to confuse the next run.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    fsync_directory(path.parent)


def atomic_write_text(path, text: str) -> None:
    atomic_write_bytes(path, text.encode('utf-8'))


class DirectoryExistsError(OSError):
    """The rename target already exists, so the move would not be atomic."""


def atomic_move_directory(source, destination) -> None:
    """Move a directory into place as a single indivisible step.

    Both paths must sit on the same filesystem; the spool keeps every stage
    under one root precisely so this holds. Refuses to overwrite, because a
    pre-existing destination means a duplicate batch id and silently merging
    two batches would corrupt both.
    """
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        raise DirectoryExistsError(f'Refusing to overwrite existing directory {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, destination)
    fsync_directory(source.parent)
    fsync_directory(destination.parent)


def remove_directory(path) -> None:
    shutil.rmtree(path, ignore_errors=True)

"""Bounded local inspection/deletion of planned objects; never walks or recurses trees.

Upload-part cleanup (ADR 0007) is bounded to one known upload directory. Orphan
cleanup (ADR 0008) addresses individual reservation-backed keys only, with the
same fail-closed rules: no symlinks, single-link regular files, bounded size.
"""

import contextlib
import hashlib
import os
import re
import stat
from datetime import datetime
from itertools import islice
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common import digest
from app.storage import FileStore

MAX_PART_BYTES = 1024 * 1024
MAX_CLEANUP_OBJECTS = 100


class TemporaryStorageError(Exception):
    """Unsafe layout or changed bytes; callers expose only a closed error code."""


class TemporaryObject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^(?:[0-9]|[1-4][0-9])-[0-9a-f]{32}$")
    byte_size: int = Field(ge=0, le=MAX_PART_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def upload_directory(store: FileStore, tenant: UUID, upload: UUID) -> Path:
    directory = store.root
    for component in ("tenants", str(tenant), "uploads", str(upload)):
        directory = directory / component
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise TemporaryStorageError()
    return directory


def inspect_part(path: Path, cutoff: datetime) -> TemporaryObject | None:
    if not re.fullmatch(r"(?:[0-9]|[1-4][0-9])-[0-9a-f]{32}", path.name):
        raise TemporaryStorageError()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_PART_BYTES
        ):
            raise TemporaryStorageError()
        if metadata.st_mtime > cutoff.timestamp():
            raise TemporaryStorageError()
        body = source.read(MAX_PART_BYTES + 1)
        if len(body) != metadata.st_size:
            raise TemporaryStorageError()
    return TemporaryObject(name=path.name, byte_size=len(body), sha256=digest(body))


def inventory(
    store: FileStore, tenant: UUID, upload: UUID, cutoff: datetime
) -> list[TemporaryObject]:
    with store.write_lock():
        directory = upload_directory(store, tenant, upload)
        if not directory.exists():
            return []
        objects = []
        with os.scandir(directory) as entries:
            for entry in islice(entries, MAX_CLEANUP_OBJECTS):
                item = inspect_part(Path(entry.path), cutoff)
                if item is not None:
                    objects.append(item)
        return objects


def remove_planned(
    store: FileStore,
    tenant: UUID,
    upload: UUID,
    objects: list[TemporaryObject],
    cutoff: datetime,
) -> dict[str, int]:
    if not 1 <= len(objects) <= MAX_CLEANUP_OBJECTS or len({o.name for o in objects}) != len(
        objects
    ):
        raise TemporaryStorageError()
    with store.write_lock():
        directory = upload_directory(store, tenant, upload)
        present = []
        # Validate the entire batch before the first unlink. A crash after an
        # unlink leaves the committed intent pending; replay tolerates absence.
        for expected in objects:
            actual = inspect_part(directory / expected.name, cutoff)
            if actual is None:
                continue
            if actual != expected:
                raise TemporaryStorageError()
            present.append(actual)
        for item in present:
            (directory / item.name).unlink()
        try:
            directory.rmdir()  # Only the empty, exact upload directory.
        except FileNotFoundError:
            pass
        except OSError:
            # Other batches may remain; never recursively remove the directory.
            if directory.exists() and not any(directory.iterdir()):
                raise
        return {
            "removed_objects": len(present),
            "removed_bytes": sum(item.byte_size for item in present),
            "already_absent_objects": len(objects) - len(present),
        }


class ReservedObject(BaseModel):
    """Plan-time inventory of one reservation's object (absent if never written)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    reservation_id: UUID
    present: bool
    byte_size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mtime_ns: int | None = None


def object_path(store: FileStore, key: str) -> Path:
    """Resolve a validated key, refusing symlinked or non-directory ancestors."""
    store.path(key)  # Validates the server-generated key pattern.
    directory = store.root
    *parents, name = key.split("/")
    for component in parents:
        directory = directory / component
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise TemporaryStorageError()
    return directory / name


def inspect_reserved(
    store: FileStore, key: str, reservation: UUID, max_bytes: int, not_after: datetime
) -> ReservedObject:
    path = object_path(store, key)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return ReservedObject(reservation_id=reservation, present=False, byte_size=0)
    with os.fdopen(fd, "rb") as source:
        metadata = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > max_bytes
            # Writers refuse after their reservation's deadline; a later write is unexplained.
            or metadata.st_mtime > not_after.timestamp()
        ):
            raise TemporaryStorageError()
        checksum, size = hashlib.sha256(), 0
        while chunk := source.read(min(1024 * 1024, max_bytes - size + 1)):
            checksum.update(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise TemporaryStorageError()
        if size != metadata.st_size:
            raise TemporaryStorageError()
    return ReservedObject(
        reservation_id=reservation,
        present=True,
        byte_size=size,
        sha256=checksum.hexdigest(),
        mtime_ns=metadata.st_mtime_ns,
    )


def inventory_reserved(
    store: FileStore, items: list[tuple[str, UUID, int, datetime]]
) -> list[ReservedObject]:
    with store.write_lock():
        return [inspect_reserved(store, *item) for item in items]


def remove_reserved(
    store: FileStore, items: list[tuple[str, int, datetime, ReservedObject]]
) -> dict[str, int]:
    """Validate the whole planned batch under the storage lock, then unlink."""
    if not 1 <= len(items) <= MAX_CLEANUP_OBJECTS or len({key for key, *_ in items}) != len(items):
        raise TemporaryStorageError()
    with store.write_lock():
        present: list[tuple[str, ReservedObject]] = []
        absent = 0
        for key, max_bytes, not_after, expected in items:
            actual = inspect_reserved(store, key, expected.reservation_id, max_bytes, not_after)
            if not actual.present:
                absent += 1  # Never written, or removed by an earlier crashed attempt.
                continue
            if actual != expected:
                raise TemporaryStorageError()
            present.append((key, actual))
        for key, _ in present:
            path = object_path(store, key)
            path.unlink()
            # Only the now-empty exact object directory; never remove recursively.
            with contextlib.suppress(OSError):
                path.parent.rmdir()
        return {
            "removed_objects": len(present),
            "removed_bytes": sum(item.byte_size for _, item in present),
            "already_absent_objects": absent,
            "planned_absent_objects": sum(1 for *_, expected in items if not expected.present),
        }

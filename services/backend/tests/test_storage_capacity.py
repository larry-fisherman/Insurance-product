"""Local object-store semantics. Admission is the PostgreSQL ledger (tests/database)."""

import hashlib
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.storage import FileStore, S3Store, StorageCapacityError


def object_key(tenant: UUID) -> str:
    return f"tenants/{tenant}/uploads/{uuid4()}/part"


def test_objects_are_exclusive_creates_that_survive_restart(tmp_path: Path) -> None:
    tenant = uuid4()
    store = FileStore(tmp_path)
    key = object_key(tenant)
    store.put(key, b"12345678", "application/octet-stream")
    restarted = FileStore(tmp_path)
    with pytest.raises(FileExistsError):
        restarted.put(key, b"changed", "application/octet-stream")
    assert restarted.get(key) == b"12345678"
    assert restarted.probe(key) == (8, hashlib.sha256(b"12345678").hexdigest())
    assert restarted.probe(object_key(tenant)) is None


def test_writer_refuses_once_its_reservation_deadline_passed(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    key = object_key(uuid4())
    with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
        store.put(key, b"late", "application/octet-stream", not_after=datetime.now(UTC))
    assert not store.path(key).parent.exists()
    store.put(
        key, b"in time", "application/octet-stream", not_after=datetime.now(UTC) + timedelta(1)
    )


def test_tenant_scan_counts_only_that_tenant_like_adr_0006(tmp_path: Path) -> None:
    first, second = uuid4(), uuid4()
    store = FileStore(tmp_path)
    store.put(object_key(first), b"12345678", "application/octet-stream")
    store.put(object_key(first), b"", "application/octet-stream")
    store.put(object_key(second), b"1234", "application/octet-stream")
    (tmp_path / "tenants" / str(first) / "uploads" / "link").symlink_to(tmp_path)
    scan = store.scan_tenant(first)
    # tenants/{id}, uploads/, two upload directories, two files and one symlink.
    assert (scan.byte_size, scan.entries, scan.anomalies, len(scan.files)) == (8, 7, 1, 2)
    assert all(key.startswith(f"tenants/{first}/") for key in scan.files)
    assert store.scan_tenant(uuid4()).entries == 0


def test_s3_writes_fail_closed_before_contacting_provider() -> None:
    # No SDK client/credentials are needed: the gate must reject the write
    # before any provider operation can occur.
    store = object.__new__(S3Store)
    with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
        store.put(object_key(uuid4()), b"fictional", "application/octet-stream")


def crash_during_write(root: str, key: str) -> None:
    store = FileStore(Path(root))
    with store.write_lock():
        path = store.path(key)
        path.parent.mkdir(parents=True)
        with path.open("xb") as output:
            output.write(b"123")
            output.flush()
            # Simulate death after a partial object write, before the lock or a
            # business transaction can complete. Only fictional temp files.
            os._exit(23)


def test_crashed_writer_releases_lock_and_partial_bytes_remain_counted(tmp_path: Path) -> None:
    tenant = uuid4()
    key = object_key(tenant)
    process = multiprocessing.get_context("spawn").Process(
        target=crash_during_write, args=(str(tmp_path), key)
    )
    process.start()
    process.join(timeout=15)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Synthetic crashing writer did not terminate")
    assert process.exitcode == 23
    process.close()
    store = FileStore(tmp_path)
    assert store.get(key) == b"123"
    # The lock is free again, and the reconciliation scan still counts the partial bytes.
    store.put(object_key(tenant), b"1234567", "application/octet-stream")
    assert store.scan_tenant(tenant).byte_size == 10

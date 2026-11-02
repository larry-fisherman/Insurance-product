"""Local storage quotas include bytes without a committed business record."""

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.storage import FileStore, S3Store, StorageCapacityError


def object_key(tenant: UUID) -> str:
    return f"tenants/{tenant}/uploads/{uuid4()}/part"


def test_retained_orphan_bytes_survive_store_restart_and_consume_capacity(tmp_path: Path) -> None:
    tenant = uuid4()
    store = FileStore(tmp_path, tenant_byte_limit=10, total_byte_limit=100)
    key = object_key(tenant)
    # Deliberately no database record: an object put can precede a rollback.
    store.put(key, b"12345678", "application/octet-stream")
    restarted = FileStore(tmp_path, tenant_byte_limit=10, total_byte_limit=100)
    rejected = object_key(tenant)
    with pytest.raises(StorageCapacityError):
        restarted.put(rejected, b"123", "application/octet-stream")
    assert restarted.get(key) == b"12345678"
    assert not restarted.path(rejected).parent.exists()
    restarted.put(object_key(tenant), b"12", "application/octet-stream")
    with pytest.raises(FileExistsError):
        restarted.put(key, b"changed", "application/octet-stream")
    assert restarted.get(key) == b"12345678"


def test_tenant_and_global_capacity_are_independent(tmp_path: Path) -> None:
    first, second = uuid4(), uuid4()
    store = FileStore(tmp_path, tenant_byte_limit=8, total_byte_limit=12)
    store.put(object_key(first), b"12345678", "application/octet-stream")
    with pytest.raises(StorageCapacityError):
        store.put(object_key(first), b"1", "application/octet-stream")
    store.put(object_key(second), b"1234", "application/octet-stream")
    with pytest.raises(StorageCapacityError):
        store.put(object_key(second), b"1", "application/octet-stream")


def concurrent_write(root: str, key: str) -> bool:
    try:
        FileStore(Path(root), tenant_byte_limit=10, total_byte_limit=10).put(
            key, b"12345678", "application/octet-stream"
        )
        return True
    except StorageCapacityError:
        return False


def test_independent_processes_cannot_overbook_shared_volume(tmp_path: Path) -> None:
    tenant = uuid4()
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        futures = [
            pool.submit(concurrent_write, str(tmp_path), object_key(tenant)) for _ in range(4)
        ]
        assert sum(future.result(timeout=30) for future in futures) == 1
    assert sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file()) == 8


def test_entry_limit_also_bounds_empty_objects_and_directories(tmp_path: Path) -> None:
    store = FileStore(tmp_path, entry_limit=5)
    tenant = uuid4()
    store.put(object_key(tenant), b"", "application/octet-stream")
    before = set(tmp_path.rglob("*"))
    with pytest.raises(StorageCapacityError):
        store.put(object_key(tenant), b"", "application/octet-stream")
    assert set(tmp_path.rglob("*")) == before


def test_preexisting_over_budget_storage_is_preserved(tmp_path: Path) -> None:
    tenant = uuid4()
    key = object_key(tenant)
    FileStore(tmp_path).put(key, b"historical source", "application/octet-stream")
    restricted = FileStore(tmp_path, tenant_byte_limit=1)
    assert restricted.get(key) == b"historical source"
    with pytest.raises(StorageCapacityError):
        restricted.put(object_key(tenant), b"new", "application/octet-stream")
    assert restricted.get(key) == b"historical source"


def test_s3_writes_fail_closed_before_contacting_provider() -> None:
    # No SDK client/credentials are needed: the missing quota protocol must
    # reject the write before any provider operation can occur.
    store = object.__new__(S3Store)
    with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
        store.put(object_key(uuid4()), b"fictional", "application/octet-stream")


def crash_during_write(root: str, key: str) -> None:
    store = FileStore(Path(root), tenant_byte_limit=10, total_byte_limit=10)
    with store.write_lock():
        store.require_capacity(key, 8)
        path = store.path(key)
        path.parent.mkdir(parents=True)
        with path.open("xb") as output:
            output.write(b"123")
            output.flush()
            # Simulate death after a partial object write, before the lock or a
            # business transaction can complete. Only fictional temp files.
            os._exit(23)


def test_crashed_writer_releases_lock_but_partial_bytes_remain_charged(tmp_path: Path) -> None:
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
    store = FileStore(tmp_path, tenant_byte_limit=10, total_byte_limit=10)
    assert store.get(key) == b"123"
    with pytest.raises(StorageCapacityError):
        store.put(object_key(tenant), b"12345678", "application/octet-stream")
    store.put(object_key(tenant), b"1234567", "application/octet-stream")

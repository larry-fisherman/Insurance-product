import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.common import now
from app.storage import FileStore
from app.temporary_storage import TemporaryStorageError, inventory, remove_planned


def test_bounded_batches_preserve_other_uploads_and_object_categories(tmp_path: Path) -> None:
    tenant, upload, other = uuid4(), uuid4(), uuid4()
    store = FileStore(tmp_path)
    for _ in range(101):
        store.put(
            f"tenants/{tenant}/uploads/{upload}/0-{uuid4().hex}",
            b"part",
            "application/octet-stream",
        )
    protected = [
        f"tenants/{tenant}/documents/{upload}/original",
        f"tenants/{tenant}/artifacts/{upload}/package",
        f"tenants/{tenant}/uploads/{other}/0-{uuid4().hex}",
    ]
    for key in protected:
        store.put(key, b"preserve", "application/octet-stream")
    cutoff = now() + timedelta(seconds=1)
    batch = inventory(store, tenant, upload, cutoff)
    assert len(batch) == 100
    result = remove_planned(store, tenant, upload, batch, cutoff)
    assert result == {"removed_objects": 100, "removed_bytes": 400, "already_absent_objects": 0}
    assert remove_planned(store, tenant, upload, batch, cutoff)["already_absent_objects"] == 100
    remaining = inventory(store, tenant, upload, cutoff)
    assert len(remaining) == 1
    remove_planned(store, tenant, upload, remaining, cutoff)
    assert not (tmp_path / "tenants" / str(tenant) / "uploads" / str(upload)).exists()
    assert all(store.get(key) == b"preserve" for key in protected)


@pytest.mark.parametrize(
    "unsafe", ["symlink", "directory_link", "hardlink", "recent", "oversized", "unknown"]
)
def test_unsafe_temporary_files_fail_closed(tmp_path: Path, unsafe: str) -> None:
    tenant, upload = uuid4(), uuid4()
    store = FileStore(tmp_path / "store")
    key = f"tenants/{tenant}/uploads/{upload}/0-{uuid4().hex}"
    store.put(key, b"part", "application/octet-stream")
    path = store.path(key)
    protected = tmp_path / "protected"
    protected.write_bytes(b"preserve")
    cutoff = now() + timedelta(seconds=1)
    if unsafe == "symlink":
        path.unlink()
        path.symlink_to(protected)
    elif unsafe == "directory_link":
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(tmp_path, target_is_directory=True)
    elif unsafe == "hardlink":
        path.unlink()
        os.link(protected, path)
    elif unsafe == "recent":
        cutoff = now() - timedelta(days=1)
    elif unsafe == "oversized":
        path.write_bytes(b"x" * (1024 * 1024 + 1))
    else:
        path.rename(path.with_name("unknown"))
    with pytest.raises((OSError, TemporaryStorageError)):
        inventory(store, tenant, upload, cutoff)
    assert protected.read_bytes() == b"preserve"


def test_changed_manifest_member_prevents_any_deletion(tmp_path: Path) -> None:
    tenant, upload = uuid4(), uuid4()
    store = FileStore(tmp_path)
    keys = [f"tenants/{tenant}/uploads/{upload}/{n}-{uuid4().hex}" for n in range(2)]
    for key in keys:
        store.put(key, b"part", "application/octet-stream")
    cutoff = now() + timedelta(seconds=1)
    batch = inventory(store, tenant, upload, cutoff)
    store.path(keys[1]).write_bytes(b"evil")
    with pytest.raises(TemporaryStorageError):
        remove_planned(store, tenant, upload, batch, cutoff)
    assert all(store.path(key).exists() for key in keys)


def test_partial_unlink_failure_replays_only_remaining_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, upload = uuid4(), uuid4()
    store = FileStore(tmp_path)
    for number in range(2):
        store.put(
            f"tenants/{tenant}/uploads/{upload}/{number}-{uuid4().hex}",
            b"part",
            "application/octet-stream",
        )
    cutoff = now() + timedelta(seconds=1)
    batch = inventory(store, tenant, upload, cutoff)
    original = Path.unlink
    calls = 0

    def interrupted(path: Path, missing_ok: bool = False) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Synthetic interruption during deletion")
        original(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", interrupted)
    with pytest.raises(OSError, match="Synthetic interruption"):
        remove_planned(store, tenant, upload, batch, cutoff)
    monkeypatch.setattr(Path, "unlink", original)
    assert remove_planned(store, tenant, upload, batch, cutoff) == {
        "removed_objects": 1,
        "removed_bytes": 4,
        "already_absent_objects": 1,
    }

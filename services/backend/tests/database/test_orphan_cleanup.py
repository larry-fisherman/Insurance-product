"""Reservation-backed orphan cleanup deletes only unlinked, expired, unreferenced objects."""

import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import Connection, Engine
from test_workspace import client as client
from test_workspace import demo, post, process_pending
from test_workspace import workspace_settings as workspace_settings

from app import orphan_cleanup, storage_quota
from app.common import digest, now
from app.config import Settings
from app.database.models import AuditEvent
from app.domain.models import DocumentVersion, Package, PackageArtifact
from app.platform_models import ObjectCleanup, StorageReservation, StorageUsage
from app.storage import StorageCapacityError, storage
from app.temporary_storage import remove_reserved

pytestmark = pytest.mark.database
REASON = {"reason": "Remove expired fictional orphan bytes."}
ENDPOINT = "/storage/orphan-cleanup"


@pytest.fixture(scope="module")
def database_engine(
    database_factory: Callable[[], AbstractContextManager[Engine]],
    migration_config: Callable[[Connection], Config],
) -> Iterator[Engine]:
    with database_factory() as engine:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine


def orphan(
    configuration: Settings,
    actor: dict[str, Any],
    body: bytes = b"fictional orphan bytes",
    *,
    write: bool = True,
    purpose: str = "document",
) -> storage_quota.Reservation:
    """A committed reservation whose business transaction never linked it."""
    owner = uuid4()
    key = {
        "document": f"tenants/{actor['tenant_id']}/documents/{owner}/original",
        "artifact": f"tenants/{actor['tenant_id']}/artifacts/{uuid4()}/fictional.pdf",
    }[purpose]
    reservation = storage_quota.reserve(
        storage_quota.reservation_engine(configuration),
        configuration,
        UUID(actor["tenant_id"]),
        UUID(actor["user_id"]),
        purpose=purpose,  # type: ignore[arg-type]
        owner_id=owner,
        slot="original" if purpose == "document" else "1:pdf",
        key=key,
        body=body,
    )
    if write:
        storage_quota.write(storage(configuration), reservation, body, "application/pdf")
    return reservation


def age(engine: Engine, configuration: Settings, reservations: list[UUID], days: float = 3) -> None:
    past = now() - timedelta(days=days)
    with engine.begin() as connection:
        keys = connection.scalars(
            sa.update(StorageReservation)
            .where(StorageReservation.id.in_(reservations))
            .values(expires_at=past)
            .returning(StorageReservation.object_key)
        ).all()
    for key in keys:
        path = configuration.storage_path / key
        if path.exists():
            written = past - timedelta(hours=1)
            os.utime(path, (written.timestamp(), written.timestamp()))


def state(engine: Engine, reservation: UUID) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(StorageReservation).where(StorageReservation.id == reservation)
        ).one()


def charged(engine: Engine, tenant: str) -> int:
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(StorageUsage).where(StorageUsage.tenant_id == UUID(tenant))
        ).one()
    return int(row.reserved_bytes + row.committed_bytes)


def plan(client: TestClient) -> Any:
    return post(client, ENDPOINT + "/plan", REASON)


def execute(client: TestClient, planned: dict[str, Any]) -> Any:
    return post(client, f"{ENDPOINT}/{planned['id']}/execute", {})


def test_planned_orphan_blocks_late_link_then_is_deleted_and_released_once(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    body = b"fictional rolled-back original"
    reservation = orphan(workspace_settings, actor, body)
    path = workspace_settings.storage_path / reservation.object_key
    before = charged(database_engine, actor["tenant_id"])
    age(database_engine, workspace_settings, [reservation.id])
    planned = plan(client)
    assert planned.status_code == 201, planned.text
    assert planned.json()["object_count"] == planned.json()["present_count"] == 1
    assert "object_key" not in planned.text and reservation.object_key not in planned.text
    assert path.exists()  # Planning has no storage side effect.
    assert state(database_engine, reservation.id).state == "cleanup_planned"
    with (
        pytest.raises(StorageCapacityError),
        storage_quota.tenant_session(
            storage_quota.reservation_engine(workspace_settings),
            UUID(actor["tenant_id"]),
            UUID(actor["user_id"]),
        ) as session,
    ):
        storage_quota.link(session, reservation)  # A late business transaction fails.
    completed = execute(client, planned.json())
    assert completed.status_code == 200, completed.text
    assert completed.json()["completion_summary"]["removed_bytes"] == len(body)
    assert not path.exists() and not path.parent.exists()
    row = state(database_engine, reservation.id)
    assert (row.state, row.release_ref) == ("released", UUID(planned.json()["id"]))
    assert charged(database_engine, actor["tenant_id"]) == before - len(body)
    assert execute(client, planned.json()).json() == completed.json()
    assert charged(database_engine, actor["tenant_id"]) == before - len(body)
    with database_engine.connect() as connection:
        actions = list(
            connection.scalars(
                sa.select(AuditEvent.action).where(
                    AuditEvent.object_id == UUID(planned.json()["id"])
                )
            )
        )
    assert actions == ["storage.orphan_cleanup_planned", "storage.orphan_cleanup_completed"]


def test_inflight_and_in_grace_reservations_are_not_candidates(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    orphan(workspace_settings, actor)  # Unexpired: a writer may still be in flight.
    expired = orphan(workspace_settings, actor)
    age(database_engine, workspace_settings, [expired.id], days=0.5)  # Expired, inside grace.
    refused = plan(client)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "ORPHAN_CLEANUP_EMPTY"


def test_changed_bytes_and_symlinks_fail_closed(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    changed = orphan(workspace_settings, actor, b"fictional bytes before change")
    age(database_engine, workspace_settings, [changed.id])
    planned = plan(client)
    assert planned.status_code == 201
    path = workspace_settings.storage_path / changed.object_key
    path.write_bytes(b"fictional bytes after change")
    written = now() - timedelta(days=4)
    os.utime(path, (written.timestamp(), written.timestamp()))
    refused = execute(client, planned.json())
    assert refused.json()["detail"]["code"] == "ORPHAN_CLEANUP_UNSAFE"
    assert path.read_bytes() == b"fictional bytes after change"
    assert state(database_engine, changed.id).state == "cleanup_planned"

    _, other = demo(client)
    linked = orphan(workspace_settings, other)
    age(database_engine, workspace_settings, [linked.id])
    target = workspace_settings.storage_path / linked.object_key
    target.unlink()
    outside = workspace_settings.storage_path / "fictional-outside"
    outside.write_bytes(b"fictional outside bytes")
    target.symlink_to(outside)
    unsafe = plan(client)
    assert unsafe.json()["detail"]["code"] == "ORPHAN_CLEANUP_UNSAFE"
    assert outside.read_bytes() == b"fictional outside bytes" and target.is_symlink()
    assert state(database_engine, linked.id).state == "reserved"


def test_retained_documents_and_artifacts_are_never_candidates(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from test_storage_reservations import put_part, queued_package

    submission, package, actor = queued_package(client, workspace_settings)
    process_pending(workspace_settings, actor)
    with database_engine.connect() as connection:
        documents = list(
            connection.execute(
                sa.select(DocumentVersion).where(DocumentVersion.submission_id == UUID(submission))
            )
        )
    # Supersede one document, exclude another, and fail/quarantine others.
    body = storage(workspace_settings).get(documents[0].object_key)
    assert digest(body) == documents[0].sha256
    initialized = post(
        client,
        f"/submissions/{submission}/uploads",
        {
            "filename": "fictional-replacement.pdf",
            "sha256": digest(body),
            "byte_size": len(body),
            "supersedes_id": str(documents[0].id),
        },
    )
    assert initialized.status_code == 201, initialized.text
    upload_id = initialized.json()["id"]
    assert put_part(client, upload_id, body).status_code == 200
    assert post(client, f"/uploads/{upload_id}/finalize", {}).status_code == 200
    assert (
        post(
            client,
            f"/documents/{documents[1].id}/exclude",
            {"reason": "Synthetic test: exclude a fictional source."},
        ).status_code
        == 200
    )
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(DocumentVersion)
            .where(DocumentVersion.id == documents[-1].id)
            .values(processing_status="failed", scan_status="rejected")
        )
        retained = list(
            connection.scalars(
                sa.select(StorageReservation.id).where(
                    StorageReservation.tenant_id == UUID(actor["tenant_id"])
                )
            )
        )
        artifact_keys = list(
            connection.scalars(
                sa.select(PackageArtifact.object_key).where(
                    PackageArtifact.package_id == UUID(package)
                )
            )
        )
    assert len(artifact_keys) == 3
    age(database_engine, workspace_settings, retained)
    # Defense in depth: a reserved (never linked) document reservation that a
    # retained row nevertheless references is still not a candidate.
    stray = orphan(workspace_settings, actor, b"fictional referenced orphan")
    age(database_engine, workspace_settings, [stray.id])
    with database_engine.begin() as connection:
        connection.execute(
            sa.insert(DocumentVersion).values(
                id=uuid4(),
                tenant_id=UUID(actor["tenant_id"]),
                submission_id=UUID(submission),
                filename="fictional-reference.pdf",
                media_type="application/pdf",
                sha256=stray.sha256,
                byte_size=stray.byte_size,
                object_key=stray.object_key,
                uploaded_by=UUID(actor["user_id"]),
            )
        )
    for status in ("ready", "approved", "failed", "queued", "exported"):
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(Package).where(Package.id == UUID(package)).values(status=status)
            )
        refused = plan(client)
        assert refused.json()["detail"]["code"] == "ORPHAN_CLEANUP_EMPTY", status
    for key in artifact_keys + [doc.object_key for doc in documents] + [stray.object_key]:
        assert (workspace_settings.storage_path / key).exists()
    assert state(database_engine, stray.id).state == "reserved"


def test_other_tenants_cannot_see_plan_or_execute_and_revocation_denies_execute(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    reservation = orphan(workspace_settings, actor)
    age(database_engine, workspace_settings, [reservation.id])
    planned = plan(client).json()
    with database_engine.begin() as connection:
        connection.execute(
            sa.text(
                "DELETE FROM app.membership_role WHERE membership_id=:member AND role_id="
                "(SELECT id FROM app.role WHERE key='tenant_admin')"
            ),
            {"member": UUID(actor["membership_id"])},
        )
    assert execute(client, planned).status_code == 403  # Revoked between plan and execute.
    _, other = demo(client)  # A later agency cannot use the ID as authority.
    assert execute(client, planned).status_code == 404
    assert client.get(f"/api/v1{ENDPOINT}/{planned['id']}").status_code == 404
    second = orphan(workspace_settings, actor, b"another fictional orphan")
    age(database_engine, workspace_settings, [second.id])
    assert plan(client).json()["detail"]["code"] == "ORPHAN_CLEANUP_EMPTY"
    assert state(database_engine, second.id).state == "reserved"
    assert (workspace_settings.storage_path / reservation.object_key).exists()
    from app.database.runtime import Database

    db = Database(workspace_settings)
    try:
        with db.tenant(UUID(other["tenant_id"]), UUID(other["user_id"])) as session:
            for model in (StorageReservation, ObjectCleanup):
                assert (
                    session.scalar(
                        sa.select(sa.func.count())
                        .select_from(model)
                        .where(model.tenant_id == UUID(actor["tenant_id"]))
                    )
                    == 0
                )
            assert session.get(StorageUsage, UUID(actor["tenant_id"])) is None
            assert (
                session.scalar(
                    sa.text(
                        "SELECT has_table_privilege(current_user,'app.object_cleanup','DELETE')"
                    )
                )
                is False
            )
    finally:
        db.close()


def test_plan_idempotency_conflict_and_crash_replay_releases_exactly_once(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, actor = demo(client)
    body = b"fictional orphan for crash replay"
    reservation = orphan(workspace_settings, actor, body)
    age(database_engine, workspace_settings, [reservation.id])
    headers = {
        "X-CSRF-Token": client.cookies.get("cgl_csrf", "") or "",
        "Idempotency-Key": str(uuid4()),
    }
    url = f"/api/v1{ENDPOINT}/plan"
    prepared = client.post(url, json=REASON, headers=headers)
    assert prepared.status_code == 201
    assert client.post(url, json=REASON, headers=headers).json() == prepared.json()
    different = client.post(url, json={"reason": "A different fictional request"}, headers=headers)
    assert different.status_code == 409
    assert different.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    before = charged(database_engine, actor["tenant_id"])

    def crash(*args: Any, **kwargs: Any) -> dict[str, int]:
        remove_reserved(*args, **kwargs)
        raise RuntimeError("Synthetic crash after unlink before database commit")

    monkeypatch.setattr(orphan_cleanup, "remove_reserved", crash)
    with pytest.raises(RuntimeError, match="Synthetic crash"):
        execute(client, prepared.json())
    path: Path = workspace_settings.storage_path / reservation.object_key
    assert not path.exists()
    assert state(database_engine, reservation.id).state == "cleanup_planned"
    assert charged(database_engine, actor["tenant_id"]) == before
    monkeypatch.undo()
    recovered = execute(client, prepared.json())
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["completion_summary"] == {
        "removed_objects": 0,
        "removed_bytes": 0,
        "already_absent_objects": 1,
        "planned_absent_objects": 0,
        "already_released_objects": 0,
    }
    assert charged(database_engine, actor["tenant_id"]) == before - len(body)
    assert execute(client, prepared.json()).json() == recovered.json()
    assert charged(database_engine, actor["tenant_id"]) == before - len(body)


def test_expired_never_written_reservation_releases_as_already_absent(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    never = orphan(workspace_settings, actor, b"fictional bytes never written", write=False)
    age(database_engine, workspace_settings, [never.id])
    before = charged(database_engine, actor["tenant_id"])
    planned = plan(client)
    assert planned.status_code == 201
    assert (planned.json()["object_count"], planned.json()["present_count"]) == (1, 0)
    completed = execute(client, planned.json())
    assert completed.status_code == 200, completed.text
    summary = completed.json()["completion_summary"]
    assert (summary["removed_objects"], summary["planned_absent_objects"]) == (0, 1)
    assert summary["already_absent_objects"] == 1
    assert state(database_engine, never.id).state == "released"
    assert charged(database_engine, actor["tenant_id"]) == before - never.byte_size


def test_late_write_to_a_planned_absent_reservation_fails_closed(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    never = orphan(workspace_settings, actor, b"fictional late bytes", write=False)
    age(database_engine, workspace_settings, [never.id])
    planned = plan(client).json()
    path = workspace_settings.storage_path / never.object_key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fictional late bytes")
    written = now() - timedelta(days=4)
    os.utime(path, (written.timestamp(), written.timestamp()))
    assert execute(client, planned).json()["detail"]["code"] == "ORPHAN_CLEANUP_UNSAFE"
    assert path.exists()


def test_writer_refuses_within_the_safety_margin_of_expiry(
    client: TestClient, workspace_settings: Settings
) -> None:
    from app.upload_cleanup import WRITE_MARGIN

    _, actor = demo(client)
    body = b"fictional bytes past the write deadline"
    reservation = orphan(workspace_settings, actor, body, write=False)
    late = storage_quota.Reservation(
        reservation.id,
        reservation.object_key,
        now() + WRITE_MARGIN - timedelta(seconds=1),
        reservation.byte_size,
        reservation.sha256,
        False,
    )
    with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
        storage_quota.write(storage(workspace_settings), late, body, "application/pdf")
    assert not (workspace_settings.storage_path / reservation.object_key).parent.exists()

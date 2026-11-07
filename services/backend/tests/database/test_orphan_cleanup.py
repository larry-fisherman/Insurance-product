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
from app.common import canonical_json, digest, now
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
    planned_row = state(database_engine, reservation.id)
    assert planned_row.state == "cleanup_planned"
    # Give the planned reservation a live TTL, so only the state gate can refuse the link:
    # neither link()'s expiry check nor the guard trigger's expiry check applies here.
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id == reservation.id)
            .values(expires_at=now() + timedelta(hours=1))
        )
    with (
        pytest.raises(StorageCapacityError),
        storage_quota.tenant_session(
            storage_quota.reservation_engine(workspace_settings),
            UUID(actor["tenant_id"]),
            UUID(actor["user_id"]),
        ) as session,
    ):
        storage_quota.link(session, reservation)  # A late business transaction fails.
    assert state(database_engine, reservation.id).state == "cleanup_planned"
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id == reservation.id)
            .values(expires_at=planned_row.expires_at)
        )
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
            {
                "reason": "Synthetic test: exclude a fictional source.",
                "expected_version": client.get(f"/api/v1/submissions/{submission}").json()[
                    "submission"
                ]["row_version"],
            },
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


def test_upload_parts_are_never_orphan_candidates_even_when_held_or_sessionless(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_storage_reservations import put_part, reservations, start_upload

    from app.platform_models import UploadSession

    submission, actor = demo(client)
    body = b"fictional part whose request rolled back"
    upload_id = start_upload(client, submission, body)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Synthetic failure after the object write")

    monkeypatch.setattr(storage_quota, "link", broken)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        put_part(client, upload_id, body)
    monkeypatch.undo()
    [rolled_back] = reservations(database_engine, upload_id)
    held = post(
        client,
        f"/uploads/{upload_id}/cleanup/hold",
        {"reason": "Preserve fictional bytes for review.", "held": True, "expected_version": 1},
    )
    assert held.status_code == 200, held.text
    # A rolled-back part whose owner is not a known upload session at all.
    sessionless = uuid4()
    sessionless_body = b"fictional part with no upload session"
    stranded = storage_quota.reserve(
        storage_quota.reservation_engine(workspace_settings),
        workspace_settings,
        UUID(actor["tenant_id"]),
        UUID(actor["user_id"]),
        purpose="upload_part",
        owner_id=sessionless,
        slot="0",
        key=f"tenants/{actor['tenant_id']}/uploads/{sessionless}/0-{uuid4().hex}",
        body=sessionless_body,
    )
    storage_quota.write(
        storage(workspace_settings), stranded, sessionless_body, "application/octet-stream"
    )
    age(database_engine, workspace_settings, [rolled_back.id, stranded.id])
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(UploadSession)
            .where(UploadSession.id == UUID(upload_id))
            .values(expires_at=now() - timedelta(days=3))
        )
    before = charged(database_engine, actor["tenant_id"])
    refused = plan(client)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "ORPHAN_CLEANUP_EMPTY"
    for row in (rolled_back, stranded):
        assert state(database_engine, row.id).state == "reserved"
        assert (workspace_settings.storage_path / row.object_key).exists()
    assert charged(database_engine, actor["tenant_id"]) == before
    # ADR 0007, which honors the hold, remains the only deletion path for parts.
    adr_0007 = post(client, f"/uploads/{upload_id}/cleanup/plan", REASON)
    assert adr_0007.json()["detail"]["code"] == "UPLOAD_CLEANUP_HELD"
    # The sweep only reports them, and they never count as orphan candidates.
    from app import storage_sweep
    from app.database.runtime import Database
    from app.storage import FileStore

    store = storage(workspace_settings)
    assert isinstance(store, FileStore)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    db = Database(workspace_settings)
    try:
        with db.tenant(tenant, user) as session:
            report = storage_sweep.counts(session, tenant, store.scan_tenant(tenant))
    finally:
        db.close()
    assert (report["orphan_candidates"], report["unlinked_upload_parts"]) == (0, 2)
    assert (workspace_settings.storage_path / stranded.object_key).exists()


def test_one_unsafe_object_does_not_block_the_rest_of_the_tenants_orphans(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    bad = orphan(workspace_settings, actor, b"fictional object that becomes unsafe")
    good = orphan(workspace_settings, actor, b"fictional safe orphan")
    age(database_engine, workspace_settings, [bad.id], days=4)  # Selected first: oldest expiry.
    age(database_engine, workspace_settings, [good.id], days=3)
    target = workspace_settings.storage_path / bad.object_key
    target.unlink()
    outside = workspace_settings.storage_path / "fictional-outside-target"
    outside.write_bytes(b"fictional outside bytes")
    target.symlink_to(outside)
    planned = plan(client)
    assert planned.status_code == 201, planned.text
    assert planned.json()["object_count"] == 1
    assert state(database_engine, bad.id).state == "reserved"
    assert state(database_engine, good.id).state == "cleanup_planned"
    assert execute(client, planned.json()).status_code == 200
    assert state(database_engine, good.id).state == "released"
    # The unsafe object is untouched and stays charged, and is refused alone.
    assert state(database_engine, bad.id).state == "reserved"
    assert target.is_symlink() and outside.read_bytes() == b"fictional outside bytes"
    assert plan(client).json()["detail"]["code"] == "ORPHAN_CLEANUP_UNSAFE"


def test_lost_plans_stay_visible_in_the_sweep_and_operator_report(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app import storage_admin, storage_sweep
    from app.database.runtime import Database
    from app.storage import FileStore

    _, actor = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    reservation = orphan(workspace_settings, actor)
    age(database_engine, workspace_settings, [reservation.id])
    planned = plan(client).json()  # The client now loses this response and its key.
    store = storage(workspace_settings)
    assert isinstance(store, FileStore)
    db = Database(workspace_settings)
    try:
        with db.tenant(tenant, user) as session:
            report = storage_sweep.counts(session, tenant, store.scan_tenant(tenant))
    finally:
        db.close()
    assert (report["orphan_candidates"], report["cleanup_planned"]) == (0, 1)
    assert storage_admin.run_report(workspace_settings) == 0
    line = next(
        text for text in capsys.readouterr().out.splitlines() if text.startswith(f"tenant={tenant}")
    )
    assert "cleanup_planned=1" in line and f"pending_plans={planned['id']}" in line
    assert reservation.object_key not in line
    # The recovered ID is enough to finish the plan.
    assert execute(client, planned).status_code == 200
    assert storage_admin.run_report(workspace_settings) == 0
    line = next(
        text for text in capsys.readouterr().out.splitlines() if text.startswith(f"tenant={tenant}")
    )
    assert "cleanup_planned=0" in line and "pending_plans" not in line


def test_runtime_cannot_fabricate_an_intent_to_lower_counters(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from test_storage_reservations import put_part, reservations, start_upload

    from app.platform_models import UploadCleanup, UploadSession

    submission, actor = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    member = UUID(actor["membership_id"])
    engine = storage_quota.reservation_engine(workspace_settings)
    with database_engine.connect() as connection:
        document = connection.execute(
            sa.select(DocumentVersion).where(DocumentVersion.submission_id == UUID(submission))
        ).first()
    assert document is not None
    [committed] = reservations(database_engine, document.id)
    live = orphan(workspace_settings, actor, b"fictional live reservation")
    expired = orphan(workspace_settings, actor, b"fictional real orphan bytes")
    age(database_engine, workspace_settings, [expired.id])
    body = b"fictional live upload part"
    upload_id = start_upload(client, submission, body)
    assert put_part(client, upload_id, body).status_code == 200
    [part] = reservations(database_engine, upload_id)
    before = charged(database_engine, actor["tenant_id"])
    manifest = '[{"reservation_id": "%s", "present": false, "byte_size": 0}]'

    def as_runtime(statement: Any, parameters: dict[str, Any] | None = None) -> None:
        with storage_quota.tenant_session(engine, tenant, user) as session:
            session.execute(statement, parameters or {})

    # No direct intent inserts, for either kind of cleanup.
    for model, values in (
        (
            ObjectCleanup,
            {
                "category": "reservation_orphan",
                "policy_version": "storage-orphan-cleanup-1",
                "objects": [{"reservation_id": str(committed.id)}],
            },
        ),
        (
            UploadCleanup,
            {
                "upload_id": UUID(upload_id),
                "policy_version": "local-upload-cleanup-1",
                "objects": [{"name": "0-" + "0" * 32, "byte_size": 1048576, "sha256": "0" * 64}],
            },
        ),
    ):
        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            as_runtime(
                sa.insert(model).values(
                    id=uuid4(),
                    tenant_id=tenant,
                    requested_by=member,
                    reason="Fabricated fictional intent",
                    **values,
                )
            )
    # No direct move to cleanup_planned, which would make a completion call possible.
    with pytest.raises(sa.exc.DBAPIError, match="require a planning or completion function"):
        as_runtime(
            sa.update(StorageReservation)
            .where(StorageReservation.id == expired.id)
            .values(state="cleanup_planned")
        )
    # The planning definer decides from the rows: nothing ineligible can be planned.
    plan_call = sa.text(
        "SELECT app.plan_object_cleanup(:id, :member, 'Fabricated fictional intent',"
        " CAST(:objects AS jsonb))"
    )
    for target in (committed.id, live.id, part.id):
        with pytest.raises(sa.exc.DBAPIError, match="not an eligible orphan"):
            as_runtime(plan_call, {"id": uuid4(), "member": member, "objects": manifest % target})
    with pytest.raises(sa.exc.DBAPIError, match="not an eligible orphan"):
        as_runtime(plan_call, {"id": uuid4(), "member": member, "objects": manifest % uuid4()})
    # A live upload cannot be planned, nor one under a preservation hold.
    upload_call = sa.text(
        "SELECT app.plan_upload_cleanup(:id, :upload, :member, 'Fabricated fictional intent',"
        " CAST(:objects AS jsonb))"
    )
    fabricated = canonical_json(
        [{"name": "0-" + "0" * 32, "byte_size": 1048576, "sha256": "0" * 64}]
    )
    parameters = {"upload": UUID(upload_id), "member": member, "objects": fabricated}
    with pytest.raises(sa.exc.DBAPIError, match="not eligible for cleanup"):
        as_runtime(upload_call, {"id": uuid4(), **parameters})
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(UploadSession)
            .where(UploadSession.id == UUID(upload_id))
            .values(expires_at=now() - timedelta(days=3), cleanup_hold=True)
        )
    with pytest.raises(sa.exc.DBAPIError, match="not eligible for cleanup"):
        as_runtime(upload_call, {"id": uuid4(), **parameters})
    # An eligible upload with a fabricated part and byte count lowers nothing: the bytes
    # come from reconcile's record and reservation rows, never from the manifest.
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(UploadSession)
            .where(UploadSession.id == UUID(upload_id))
            .values(cleanup_hold=False)
        )
    intent = uuid4()
    as_runtime(upload_call, {"id": intent, **parameters})
    as_runtime(
        sa.text("SELECT * FROM app.release_upload_cleanup(:id, CAST('{}' AS jsonb))"),
        {"id": intent},
    )
    assert charged(database_engine, actor["tenant_id"]) == before
    # Requesting under another member's identity is refused as well.
    with pytest.raises(sa.exc.DBAPIError, match="Invalid cleanup plan"):
        as_runtime(plan_call, {"id": uuid4(), "member": uuid4(), "objects": manifest % expired.id})

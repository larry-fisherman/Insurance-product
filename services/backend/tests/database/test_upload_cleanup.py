"""Cleanup removes only disposable temporary files; metadata and originals survive."""

import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import AbstractContextManager
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import Connection, Engine
from test_workspace import client as client
from test_workspace import demo, post
from test_workspace import workspace_settings as workspace_settings

from app import upload_cleanup
from app.common import digest, now
from app.config import Settings
from app.database.models import AuditEvent
from app.domain.models import DocumentVersion
from app.platform_models import UploadCleanup, UploadPart, UploadSession
from app.storage import storage
from app.temporary_storage import remove_planned

pytestmark = pytest.mark.database
REASON = {"reason": "Remove expired fictional transfer bytes."}


@pytest.fixture(scope="module")
def database_engine(
    database_factory: Callable[[], AbstractContextManager[Engine]],
    migration_config: Callable[[Connection], Config],
) -> Iterator[Engine]:
    with database_factory() as engine:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine


def upload(client: TestClient, submission: str, body: bytes = b"fictional part") -> str:
    result = post(
        client,
        f"/submissions/{submission}/uploads",
        {
            "filename": "fictional.pdf",
            "sha256": digest(body),
            "byte_size": len(body),
        },
    )
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    part = client.put(
        f"/api/v1/uploads/{identifier}/parts/0",
        content=body,
        headers={"X-CSRF-Token": client.cookies.get("cgl_csrf", "") or ""},
    )
    assert part.status_code == 200, part.text
    return str(identifier)


def age(engine: Engine, configuration: Settings, actor: dict[str, Any], identifier: str) -> Path:
    past = now() - timedelta(days=3)
    with engine.begin() as connection:
        connection.execute(
            sa.update(UploadSession)
            .where(UploadSession.id == UUID(identifier))
            .values(expires_at=past)
        )
    directory = (
        configuration.storage_path / "tenants" / str(actor["tenant_id"]) / "uploads" / identifier
    )
    for path in directory.iterdir():
        os.utime(path, (past.timestamp(), past.timestamp()))
    return directory


def plan(client: TestClient, identifier: str) -> dict[str, Any]:
    result = post(client, f"/uploads/{identifier}/cleanup/plan", REASON)
    assert result.status_code == 201, result.text
    return dict(result.json())


def execute(client: TestClient, identifier: str, planned: dict[str, Any]) -> Any:
    return post(client, f"/uploads/{identifier}/cleanup/{planned['id']}/execute", {})


def fictional_pdf(engine: Engine, configuration: Settings, submission: str) -> bytes:
    # Read only a generated fixture from this test's private temporary store.
    # The download API correctly withholds originals until they pass scanning.
    with engine.connect() as connection:
        key = connection.scalar(
            sa.select(DocumentVersion.object_key)
            .where(
                DocumentVersion.submission_id == UUID(submission),
                DocumentVersion.media_type == "application/pdf",
            )
            .limit(1)
        )
    assert key
    return storage(configuration).get(key)


def test_expiry_grace_holds_orphans_and_exact_plan_replay(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
) -> None:
    submission, actor = demo(client)
    identifier = upload(client, submission)
    assert (
        post(client, f"/uploads/{identifier}/cleanup/plan", REASON).json()["detail"]["code"]
        == "UPLOAD_CLEANUP_NOT_READY"
    )
    assert post(client, f"/uploads/{identifier}/abort", {}).status_code == 200
    # An aborted transfer is still protected by the expiry + grace period.
    assert post(client, f"/uploads/{identifier}/cleanup/plan", REASON).status_code == 409
    key = f"tenants/{actor['tenant_id']}/uploads/{identifier}/0-{uuid4().hex}"
    storage(workspace_settings).put(key, b"orphan from rollback", "application/octet-stream")
    directory = age(database_engine, workspace_settings, actor, identifier)
    planned = plan(client, identifier)
    assert planned["object_count"] == 2
    assert len(list(directory.iterdir())) == 2  # Planning has no storage side effect.
    held = post(
        client,
        f"/uploads/{identifier}/cleanup/hold",
        {**REASON, "held": True, "expected_version": 1},
    )
    assert held.status_code == 200
    assert client.get(f"/api/v1/uploads/{identifier}/cleanup/hold").json()["version"] == 2
    stale = post(
        client,
        f"/uploads/{identifier}/cleanup/hold",
        {**REASON, "held": False, "expected_version": 1},
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "UPLOAD_HOLD_CHANGED"
    assert execute(client, identifier, planned).json()["detail"]["code"] == "UPLOAD_CLEANUP_HELD"
    assert len(list(directory.iterdir())) == 2
    post(
        client,
        f"/uploads/{identifier}/cleanup/hold",
        {**REASON, "held": False, "expected_version": 2},
    )
    completed = execute(client, identifier, planned)
    assert completed.status_code == 200, completed.text
    assert completed.json()["completion_summary"]["removed_objects"] == 2
    assert not directory.exists()
    assert execute(client, identifier, planned).json() == completed.json()
    assert post(client, f"/uploads/{identifier}/finalize", {}).status_code == 410
    with database_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(UploadPart)
                .where(UploadPart.upload_id == UUID(identifier))
            )
            == 1
        )
        actions = list(
            connection.scalars(
                sa.select(AuditEvent.action).where(AuditEvent.object_id == UUID(planned["id"]))
            )
        )
    assert actions == ["upload.cleanup_planned", "upload.cleanup_completed"]


def test_crash_after_unlink_keeps_durable_intent_for_recovery(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission, actor = demo(client)
    identifier = upload(client, submission)
    directory = age(database_engine, workspace_settings, actor, identifier)
    planned = plan(client, identifier)
    original = remove_planned

    def crash(*args: Any, **kwargs: Any) -> dict[str, int]:
        original(*args, **kwargs)
        raise RuntimeError("Synthetic crash after unlink before database commit")

    monkeypatch.setattr(upload_cleanup, "remove_planned", crash)
    with pytest.raises(RuntimeError, match="Synthetic crash"):
        execute(client, identifier, planned)
    assert not directory.exists()
    with database_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(UploadCleanup.completed_at).where(UploadCleanup.id == UUID(planned["id"]))
            )
            is None
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.object_id == UUID(planned["id"]))
            )
            == 1
        )
    monkeypatch.setattr(upload_cleanup, "remove_planned", original)
    recovered = execute(client, identifier, planned)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["completion_summary"] == {
        "removed_objects": 0,
        "removed_bytes": 0,
        "already_absent_objects": 1,
    }


def test_finalized_original_integrity_and_retry_survive_cleanup(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
) -> None:
    submission, actor = demo(client)
    body = fictional_pdf(database_engine, workspace_settings, submission)
    identifier = upload(client, submission, body)
    finalized = post(client, f"/uploads/{identifier}/finalize", {})
    assert finalized.status_code == 200, finalized.text
    new_document = UUID(finalized.json()["document_id"])
    directory = age(database_engine, workspace_settings, actor, identifier)
    planned = plan(client, identifier)
    with database_engine.connect() as connection:
        key = connection.scalar(
            sa.select(DocumentVersion.object_key).where(DocumentVersion.id == new_document)
        )
    assert key
    original_path = workspace_settings.storage_path / key
    original_path.write_bytes(b"synthetic integrity failure")
    assert (
        execute(client, identifier, planned).json()["detail"]["code"] == "UPLOAD_CLEANUP_INTEGRITY"
    )
    assert directory.exists()
    original_path.write_bytes(body)
    assert execute(client, identifier, planned).status_code == 200
    assert original_path.read_bytes() == body
    assert post(client, f"/uploads/{identifier}/finalize", {}).json()["document_id"] == str(
        new_document
    )


def test_current_permission_tenant_scope_and_manifest_immutability(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
) -> None:
    submission, actor = demo(client)
    identifier = upload(client, submission)
    directory = age(database_engine, workspace_settings, actor, identifier)
    planned = plan(client, identifier)
    with database_engine.begin() as connection:
        connection.execute(
            sa.text(
                "DELETE FROM app.membership_role WHERE membership_id=:member AND role_id=(SELECT id FROM app.role WHERE key='tenant_admin')"
            ),
            {"member": UUID(actor["membership_id"])},
        )
    assert execute(client, identifier, planned).status_code == 403
    assert (
        post(
            client,
            f"/uploads/{identifier}/cleanup/hold",
            {**REASON, "held": True, "expected_version": 1},
        ).status_code
        == 403
    )
    # A later active agency cannot use an ID as authority.
    demo(client)
    assert execute(client, identifier, planned).status_code == 404
    assert (
        post(
            client,
            f"/uploads/{identifier}/cleanup/hold",
            {**REASON, "held": True, "expected_version": 1},
        ).status_code
        == 404
    )
    assert directory.exists()
    from app.database.runtime import Database

    with (
        database_engine.begin() as connection,
        pytest.raises(sa.exc.DBAPIError),
        connection.begin_nested(),
    ):
        connection.execute(
            sa.update(UploadCleanup)
            .where(UploadCleanup.id == UUID(planned["id"]))
            .values(reason="rewrite history")
        )
    # Check runtime no-scope/cross-tenant visibility and lack of DELETE privilege.
    db = Database(workspace_settings)
    try:
        with db.runtime_engine.connect() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(UploadCleanup)) == 0
        with db.tenant(UUID(actor["tenant_id"]), UUID(actor["user_id"])) as session:
            assert (
                session.scalar(
                    sa.text(
                        "SELECT has_table_privilege(current_user,'app.upload_cleanup','DELETE')"
                    )
                )
                is False
            )
    finally:
        db.close()


def test_cleanup_plan_waits_for_inflight_finalization(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import api

    submission, actor = demo(client)
    body = fictional_pdf(database_engine, workspace_settings, submission)
    identifier = upload(client, submission, body)
    entered, release = Event(), Event()
    original = api.store_document

    def paused(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(timeout=15)
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "store_document", paused)
    # Advance only cleanup's clock to make it eligible while the finalizer
    # still owns the upload lock; no production expiry rules are weakened.
    future = now() + timedelta(days=3)
    monkeypatch.setattr(upload_cleanup, "now", lambda: future)
    with ThreadPoolExecutor(max_workers=2) as pool:
        finalizing = pool.submit(post, client, f"/uploads/{identifier}/finalize", {})
        assert entered.wait(timeout=15)
        preparing = pool.submit(post, client, f"/uploads/{identifier}/cleanup/plan", REASON)
        try:
            with pytest.raises(TimeoutError):
                preparing.result(timeout=0.2)
        finally:
            release.set()
        assert finalizing.result(timeout=15).status_code == 200
        prepared = preparing.result(timeout=15)
        assert prepared.status_code == 201, prepared.text
    assert execute(client, identifier, prepared.json()).status_code == 200
    assert post(client, f"/uploads/{identifier}/finalize", {}).status_code == 200


def test_plan_idempotency_and_retained_reference_guard(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
) -> None:
    submission, actor = demo(client)
    identifier = upload(client, submission)
    directory = age(database_engine, workspace_settings, actor, identifier)
    headers = {
        "X-CSRF-Token": client.cookies.get("cgl_csrf", "") or "",
        "Idempotency-Key": str(uuid4()),
    }
    endpoint = f"/api/v1/uploads/{identifier}/cleanup/plan"
    prepared = client.post(endpoint, json=REASON, headers=headers)
    assert prepared.status_code == 201
    assert client.post(endpoint, json=REASON, headers=headers).json() == prepared.json()
    assert (
        client.post(
            endpoint, json={"reason": "A different cleanup request"}, headers=headers
        ).status_code
        == 409
    )
    part = next(directory.iterdir())
    # Seed an unexpected retained source reference. Namespace assumptions alone
    # must not authorize deleting bytes referenced by immutable history.
    with database_engine.begin() as connection:
        connection.execute(
            sa.insert(DocumentVersion).values(
                id=uuid4(),
                tenant_id=UUID(actor["tenant_id"]),
                submission_id=UUID(submission),
                filename="fictional-reference.pdf",
                media_type="application/pdf",
                sha256=digest(part.read_bytes()),
                byte_size=part.stat().st_size,
                object_key=str(part.relative_to(workspace_settings.storage_path)),
                uploaded_by=UUID(actor["user_id"]),
            )
        )
    refused = execute(client, identifier, prepared.json())
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "UPLOAD_CLEANUP_REFERENCED"
    assert part.exists()

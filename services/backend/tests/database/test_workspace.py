"""Real runtime-role tests; requests do not run as the migration owner."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pyotp
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine, make_url

from app.config import Environment, Settings
from app.database.runtime import Database
from app.domain.models import ProcessingJob
from app.main import create_app
from app.worker import process_job

pytestmark = pytest.mark.database


@pytest.fixture
def workspace_settings(database_engine: Engine, tmp_path: Path) -> Settings:
    local = Settings()
    assert local.runtime_database_url and local.auth_database_url
    return local.model_copy(
        update={
            "app_env": Environment.TEST,
            "redis_url": None,
            "runtime_database_url": SecretStr(
                make_url(local.runtime_database_url.get_secret_value())
                .set(database=database_engine.url.database)
                .render_as_string(hide_password=False)
            ),
            "auth_database_url": SecretStr(
                make_url(local.auth_database_url.get_secret_value())
                .set(database=database_engine.url.database)
                .render_as_string(hide_password=False)
            ),
            "storage_backend": "filesystem",
            "storage_path": tmp_path / "objects",
            "extraction_provider": "local",
        }
    )


@pytest.fixture
def client(workspace_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(workspace_settings)) as client:
        client.headers["Origin"] = "http://testserver"
        yield client


def post(client: TestClient, path: str, body: Any) -> Any:
    headers = {
        "X-CSRF-Token": client.cookies.get("cgl_csrf", "") or "",
        "Idempotency-Key": str(__import__("uuid").uuid4()),
    }
    return client.post("/api/v1" + path, json=body, headers=headers)


def demo(client: TestClient) -> tuple[str, dict[str, Any]]:
    response = post(client, "/auth/demo", {})
    assert response.status_code == 201, response.text
    return response.json()["submission_id"], client.get("/api/v1/me").json()


def process_pending(configuration: Settings, actor: dict[str, Any]) -> None:
    db = Database(configuration)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    with db.tenant(tenant, user) as session:
        ids = list(
            session.scalars(sa.select(ProcessingJob.id).where(ProcessingJob.status == "queued"))
        )
    db.close()
    for job_id in ids:
        process_job(configuration, tenant, user, job_id)


def reviewed_workspace(client: TestClient, configuration: Settings) -> tuple[str, dict[str, Any]]:
    submission_id, actor = demo(client)
    process_pending(configuration, actor)
    data = client.get("/api/v1/submissions/" + submission_id).json()
    assert all(job["status"] == "completed" for job in data["jobs"]), data["jobs"]
    assert any(group["conflicting"] for group in data["readiness"]["groups"])
    decisions = [
        {
            "group_key": group["key"],
            "fingerprint": group["fingerprint"],
            "action": "approve",
            "observation_id": group["observations"][0]["id"],
            "reason": "Synthetic test: broker verified this value against the source and confirmed the sales estimate.",
        }
        for group in data["readiness"]["groups"]
    ]
    response = post(
        client,
        f"/submissions/{submission_id}/review-decisions",
        {"expected_version": data["submission"]["row_version"], "decisions": decisions},
    )
    assert response.status_code == 200, response.text
    return submission_id, actor


def test_end_to_end_review_approval_export_and_staleness(
    client: TestClient, workspace_settings: Settings
) -> None:
    submission_id, actor = reviewed_workspace(client, workspace_settings)
    data = client.get("/api/v1/submissions/" + submission_id).json()
    assert data["readiness"]["ready"], data["readiness"]["blockers"]
    generated = post(
        client,
        f"/submissions/{submission_id}/packages",
        {"expected_version": data["submission"]["row_version"]},
    )
    assert generated.status_code == 202, generated.text
    package_id = generated.json()["id"]
    process_pending(workspace_settings, actor)
    detail = client.get("/api/v1/packages/" + package_id).json()
    assert detail["package"]["status"] == "ready", detail
    assert client.get(f"/api/v1/packages/{package_id}/artifacts/pdf").status_code == 409
    preview = client.get(f"/api/v1/packages/{package_id}/artifacts/pdf?preview=true")
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF")
    approved = post(
        client,
        f"/packages/{package_id}/approvals",
        {
            "manifest_hash": detail["package"]["manifest_hash"],
            "attestation": "I reviewed these exact synthetic files and approve this package for test export.",
        },
    )
    assert approved.status_code == 200, approved.text
    exported = client.get(f"/api/v1/packages/{package_id}/artifacts/pdf")
    assert exported.status_code == 200 and exported.content == preview.content
    archive = client.get(f"/api/v1/packages/{package_id}/artifacts/zip")
    assert archive.status_code == 200 and archive.content.startswith(b"PK")
    # Excluding a document is material and invalidates the approval for current export.
    doc_id = data["documents"][0]["id"]
    assert (
        post(
            client,
            f"/documents/{doc_id}/exclude",
            {"reason": "Replaced for a synthetic integrity test"},
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/packages/{package_id}/artifacts/pdf").status_code == 409
    assert (
        post(
            client,
            f"/packages/{package_id}/approvals",
            {
                "manifest_hash": detail["package"]["manifest_hash"],
                "attestation": "An old manifest cannot authorize a changed submission.",
            },
        ).status_code
        == 409
    )


def test_cross_tenant_records_sources_downloads_and_jobs_are_inaccessible(
    client: TestClient, workspace_settings: Settings
) -> None:
    first_id, actor = demo(client)
    process_pending(workspace_settings, actor)
    first = client.get("/api/v1/submissions/" + first_id).json()
    demo(client)  # New session belongs exclusively to another isolated fictional agency.
    for path in [
        f"/submissions/{first_id}",
        f"/insureds/{first['insured']['id']}",
        f"/documents/{first['documents'][0]['id']}/content",
        f"/sources/{first['sources'][0]['id']}",
        f"/submissions/{first_id}/audit",
    ]:
        assert client.get("/api/v1" + path).status_code == 404, path
    assert post(client, f"/jobs/{first['jobs'][0]['id']}/retry", {}).status_code == 404
    # Tenant headers cannot override the server-selected agency.
    response = client.get(
        "/api/v1/submissions/" + first_id, headers={"X-Tenant-ID": actor["tenant_id"]}
    )
    assert response.status_code == 404


def test_rls_fails_closed_and_transaction_scope_does_not_leak(
    client: TestClient, workspace_settings: Settings
) -> None:
    _, actor = demo(client)
    db = Database(workspace_settings)
    try:
        with db.runtime_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM app.submission")) == 0
        with db.tenant(UUID(actor["tenant_id"]), UUID(actor["user_id"])) as session:
            assert session.scalar(sa.text("SELECT count(*) FROM app.submission")) == 1
        with db.runtime_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM app.submission")) == 0
        with db.tenant(UUID(actor["tenant_id"]), UUID(int=1)) as session:
            assert session.scalar(sa.text("SELECT count(*) FROM app.submission")) == 0
    finally:
        db.close()


def test_authenticator_enrollment_csrf_logout_and_replay(client: TestClient) -> None:
    response = post(
        client,
        "/auth/register",
        {
            "email": "test-" + __import__("uuid").uuid4().hex + "@example.invalid",
            "password": "A fictional password 2026!",
            "name": "Broker Example",
            "agency": "Fictional MFA Agency",
        },
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/dashboard").status_code == 403
    secret = response.json()["secret"]
    code = pyotp.TOTP(secret).now()
    enrolled = post(client, "/auth/mfa/confirm", {"code": code})
    assert enrolled.status_code == 200 and len(enrolled.json()["recovery_codes"]) == 10
    actor = client.get("/api/v1/me").json()
    assert actor["mfa_verified"] and actor["auth_method"] == "password"
    assert client.post("/api/v1/auth/logout", json={}).status_code == 403
    assert post(client, "/auth/logout", {}).status_code == 200
    assert client.get("/api/v1/me").status_code == 401


def test_material_conflict_and_stale_review_cannot_generate(
    client: TestClient, workspace_settings: Settings
) -> None:
    submission_id, actor = demo(client)
    process_pending(workspace_settings, actor)
    data = client.get("/api/v1/submissions/" + submission_id).json()
    response = post(
        client,
        f"/submissions/{submission_id}/packages",
        {"expected_version": data["submission"]["row_version"]},
    )
    assert response.status_code == 409
    group = data["readiness"]["groups"][0]
    response = post(
        client,
        f"/submissions/{submission_id}/review-decisions",
        {
            "expected_version": data["submission"]["row_version"],
            "decisions": [
                {
                    "group_key": group["key"],
                    "fingerprint": "old-evidence",
                    "action": "approve",
                    "observation_id": group["observations"][0]["id"],
                    "reason": "This must be rejected because evidence changed",
                }
            ],
        },
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "EVIDENCE_CHANGED"


def test_role_changes_apply_immediately_and_last_admin_is_protected(
    client: TestClient, workspace_settings: Settings
) -> None:
    submission_id, actor = demo(client)
    members = client.get("/api/v1/agency").json()["members"]
    me = next(member for member in members if member["id"] == actor["membership_id"])
    headers = {"X-CSRF-Token": client.cookies.get("cgl_csrf", "") or ""}
    response = client.patch(
        "/api/v1/memberships/" + me["id"],
        json={"expected_version": me["row_version"], "status": "active", "roles": ["viewer"]},
        headers=headers,
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "LAST_ADMINISTRATOR"
    response = client.patch(
        "/api/v1/memberships/" + me["id"],
        json={"expected_version": me["row_version"], "status": "active", "roles": ["tenant_admin"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/agency").status_code == 200
    assert client.get("/api/v1/submissions/" + submission_id).status_code == 403
    assert post(client, "/insureds", {"name": "Unauthorized fictional insured"}).status_code == 403
    assert client.get("/api/v1/me").json()["roles"] == ["tenant_admin"]


def test_recovery_codes_are_one_use_and_old_totp_cannot_replay(client: TestClient) -> None:
    from uuid import uuid4

    email = f"recovery-{uuid4().hex}@example.invalid"
    password = "Fictional recovery password 2026!"
    registered = post(
        client,
        "/auth/register",
        {
            "email": email,
            "password": password,
            "name": "Recovery Broker",
            "agency": "Recovery Test Agency",
        },
    )
    code = pyotp.TOTP(registered.json()["secret"]).now()
    confirmed = post(client, "/auth/mfa/confirm", {"code": code})
    assert confirmed.status_code == 200
    assert post(client, "/auth/logout", {}).status_code == 200
    assert (
        post(
            client, "/auth/login", {"email": email, "password": password, "code": code}
        ).status_code
        == 401
    )
    recovery_code = confirmed.json()["recovery_codes"][0]
    recovered = post(
        client,
        "/auth/recover",
        {
            "email": email,
            "recovery_code": recovery_code,
            "new_password": "New fictional password 2027!",
        },
    )
    assert recovered.status_code == 200 and recovered.json()["mfa_enrollment_required"]
    assert client.get("/api/v1/dashboard").status_code == 403
    assert (
        post(
            client,
            "/auth/recover",
            {
                "email": email,
                "recovery_code": recovery_code,
                "new_password": "Another fictional password!",
            },
        ).status_code
        == 401
    )


def test_normal_upload_cannot_claim_synthetic_scan_bypass(
    client: TestClient, workspace_settings: Settings
) -> None:
    from app.demo import pdf
    from app.domain.models import DocumentVersion

    submission_id, actor = demo(client)
    response = client.post(
        f"/api/v1/submissions/{submission_id}/documents",
        files={
            "file": (
                "fictional-upload.pdf",
                pdf(["Fictional uploaded document for quarantine verification"]),
                "application/pdf",
            )
        },
        headers={
            "X-CSRF-Token": client.cookies.get("cgl_csrf", "") or "",
            "Idempotency-Key": "normal-upload-test",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["scan_status"] == "quarantined"
    assert client.get("/api/v1/documents/" + response.json()["id"] + "/content").status_code == 409
    db = Database(workspace_settings)
    with db.tenant(UUID(actor["tenant_id"]), UUID(actor["user_id"])) as session:
        document = session.get(DocumentVersion, UUID(response.json()["id"]))
        assert document is not None and document.fixture_signature is None
    db.close()


def test_rendered_manifest_is_database_sealed(
    client: TestClient, workspace_settings: Settings
) -> None:
    submission_id, actor = reviewed_workspace(client, workspace_settings)
    data = client.get("/api/v1/submissions/" + submission_id).json()
    generated = post(
        client,
        f"/submissions/{submission_id}/packages",
        {"expected_version": data["submission"]["row_version"]},
    )
    assert generated.status_code == 202
    package_id = generated.json()["id"]
    process_pending(workspace_settings, actor)
    db = Database(workspace_settings)
    try:
        with (
            pytest.raises(sa.exc.DBAPIError) as caught,
            db.tenant(UUID(actor["tenant_id"]), UUID(actor["user_id"])) as session,
        ):
            session.execute(
                sa.text("UPDATE app.package SET manifest='{}'::jsonb WHERE id=:id"),
                {"id": UUID(package_id)},
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55000"
    finally:
        db.close()


def test_material_account_edit_invalidates_submission_input(
    client: TestClient, workspace_settings: Settings
) -> None:
    submission_id, _ = reviewed_workspace(client, workspace_settings)
    data = client.get("/api/v1/submissions/" + submission_id).json()
    insured = data["insured"]
    payload = {
        key: insured[key]
        for key in (
            "name",
            "dba",
            "entity_type",
            "mailing_address",
            "operations",
            "external_reference",
            "status",
        )
    }
    payload.update(name="Fictional Renamed Entity LLC", expected_version=insured["row_version"])
    changed = client.patch(
        "/api/v1/insureds/" + insured["id"],
        json=payload,
        headers={"X-CSRF-Token": client.cookies.get("cgl_csrf", "") or ""},
    )
    assert changed.status_code == 200, changed.text
    updated = client.get("/api/v1/submissions/" + submission_id).json()
    assert updated["submission"]["input_version"] == data["submission"]["input_version"] + 1
    assert any(
        item["key"] == "missing:account_name_mismatch" for item in updated["readiness"]["blockers"]
    )

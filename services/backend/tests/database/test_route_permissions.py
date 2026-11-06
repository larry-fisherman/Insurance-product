"""Route-by-role matrix: every API route is authenticated, and a viewer cannot write."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine, make_url

from app.config import Environment, Settings
from app.database.runtime import Database
from app.main import create_app

pytestmark = pytest.mark.database

# Reachable without a session. Every other /api/v1 route must answer 401.
PUBLIC = {
    ("GET", "/api/v1/health/live"),
    ("GET", "/api/v1/health/ready"),
    ("GET", "/api/v1/auth/oidc/start"),
    ("GET", "/api/v1/auth/oidc/callback"),
    # Pre-sign-in probe: the sign-in page reads the identity mode before any session exists.
    ("GET", "/api/v1/auth/config"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/recover"),
    ("POST", "/api/v1/auth/demo"),
}

# Authenticated session/identity self-service: no agency permission applies, so the
# viewer matrix does not cover them (they still require a session above).
SELF_SERVICE = {
    ("POST", "/api/v1/auth/mfa/confirm"),
    ("POST", "/api/v1/auth/agency"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/sessions/revoke-others"),
    ("POST", "/api/v1/auth/reauthenticate"),
    ("POST", "/api/v1/auth/activate"),
    ("POST", "/api/v1/invitations/accept"),
}

REASON = "Synthetic route permission matrix check."

# Minimal valid request bodies, so a 403 is the permission decision rather than a 422.
VIEWER_BODIES: dict[tuple[str, str], Any] = {
    ("PATCH", "/api/v1/agency/onboarding"): {
        "name": "Fictional Agency",
        "timezone": "UTC",
        "contact_email": "owner@example.invalid",
        "expected_version": 1,
        "acknowledge": True,
    },
    ("PATCH", "/api/v1/billing"): {"billing_email": "bill@example.invalid", "expected_version": 1},
    ("POST", "/api/v1/billing/requests"): {"kind": "plan_change", "note": REASON},
    ("POST", "/api/v1/submissions/{submission_id}/uploads"): {
        "filename": "fictional.pdf",
        "sha256": "0" * 64,
        "byte_size": 1,
    },
    ("PUT", "/api/v1/uploads/{upload_id}/parts/{number}"): None,
    ("POST", "/api/v1/uploads/{upload_id}/finalize"): None,
    ("POST", "/api/v1/uploads/{upload_id}/abort"): None,
    ("POST", "/api/v1/uploads/{upload_id}/cleanup/hold"): {
        "reason": REASON,
        "held": True,
        "expected_version": 1,
    },
    ("POST", "/api/v1/uploads/{upload_id}/cleanup/plan"): {"reason": REASON},
    ("POST", "/api/v1/uploads/{upload_id}/cleanup/{plan_id}/execute"): None,
    ("POST", "/api/v1/storage/orphan-cleanup/plan"): {"reason": REASON},
    ("POST", "/api/v1/storage/orphan-cleanup/{plan_id}/execute"): None,
    ("POST", "/api/v1/insureds"): {"name": "Fictional Insured"},
    ("PATCH", "/api/v1/insureds/{insured_id}"): {
        "name": "Fictional Insured",
        "expected_version": 1,
    },
    ("POST", "/api/v1/submissions"): "submission",
    ("PATCH", "/api/v1/submissions/{submission_id}"): "submission_edit",
    ("POST", "/api/v1/submissions/{submission_id}/status"): {
        "reason": REASON,
        "expected_version": 1,
        "status": "cancelled",
    },
    ("POST", "/api/v1/submissions/{submission_id}/documents"): "multipart",
    ("POST", "/api/v1/documents/{document_id}/exclude"): {"reason": REASON},
    ("POST", "/api/v1/documents/{document_id}/classification"): {
        "reason": REASON,
        "document_type": "unknown",
    },
    ("POST", "/api/v1/jobs/{job_id}/retry"): None,
    ("POST", "/api/v1/submissions/{submission_id}/review-decisions"): {
        "expected_version": 1,
        "decisions": [{"group_key": "fictional", "action": "approve", "reason": REASON}],
    },
    ("POST", "/api/v1/submissions/{submission_id}/exceptions"): {
        "reason": REASON,
        "key": "missing:fictional",
        "fingerprint": "0" * 64,
        "expected_version": 1,
    },
    ("POST", "/api/v1/submissions/{submission_id}/packages"): {"expected_version": 1},
    ("POST", "/api/v1/packages/{package_id}/approvals"): {
        "manifest_hash": "0" * 64,
        "attestation": "A viewer cannot approve this synthetic package.",
    },
    ("POST", "/api/v1/submissions/{submission_id}/tasks"): {"title": "Fictional task"},
    ("PATCH", "/api/v1/tasks/{task_id}"): {"expected_version": 1, "status": "completed"},
    ("POST", "/api/v1/invitations"): {"email": "viewer-probe@example.invalid", "roles": ["viewer"]},
    ("POST", "/api/v1/invitations/{invitation_id}/revoke"): None,
    ("PATCH", "/api/v1/memberships/{membership_id}"): {
        "status": "active",
        "roles": ["tenant_admin"],
        "expected_version": 1,
    },
    ("POST", "/api/v1/submissions/{submission_id}/measurements"): {
        "baseline_minutes": 10,
        "actual_minutes": 5,
        "note": REASON,
    },
}


def api_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Flatten included routers; method/path pairs for every application route."""

    def walk(routes: list[Any]) -> Iterator[Any]:
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from walk(original.routes)
            else:
                yield route

    pairs = {
        (method, route.path)
        for route in walk(list(app.routes))
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    return sorted(pairs)


def fill(path: str, ids: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda match: ids.get(match.group(1), str(uuid4())), path)


@pytest.fixture
def matrix_settings(database_engine: Engine, tmp_path: Path) -> Settings:
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


def test_route_inventory_is_classified() -> None:
    routes = set(api_routes(create_app(Settings())))
    assert routes >= PUBLIC, sorted(PUBLIC - routes)
    assert routes >= SELF_SERVICE, sorted(SELF_SERVICE - routes)
    assert routes >= set(VIEWER_BODIES), sorted(set(VIEWER_BODIES) - routes)
    writes = {pair for pair in routes if pair[0] != "GET"}
    # A new write route must be added to the viewer matrix or explicitly exempted.
    assert writes - PUBLIC - SELF_SERVICE == set(VIEWER_BODIES), sorted(
        writes - PUBLIC - SELF_SERVICE ^ set(VIEWER_BODIES)
    )


def test_every_non_public_route_requires_a_session(matrix_settings: Settings) -> None:
    app = create_app(matrix_settings)
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        unexpected = {}
        for method, path in api_routes(app):
            if (method, path) in PUBLIC or not path.startswith("/api/v1/"):
                continue
            response = client.request(method, fill(path, {"format": "pdf", "number": "0"}), json={})
            if response.status_code != 401:
                unexpected[f"{method} {path}"] = response.status_code
        assert unexpected == {}


def test_viewer_cannot_write_or_export(matrix_settings: Settings) -> None:
    with TestClient(create_app(matrix_settings)) as client:
        client.headers["Origin"] = "http://testserver"
        csrf = {"X-CSRF-Token": client.cookies.get("cgl_csrf", "") or ""}
        demo = client.post("/api/v1/auth/demo", json={}, headers=csrf)
        assert demo.status_code == 201, demo.text
        actor = client.get("/api/v1/me").json()
        client.headers["X-Agency-Context"] = actor["agency_context"]
        client.headers["X-CSRF-Token"] = client.cookies.get("cgl_csrf", "") or ""
        submission_id = demo.json()["submission_id"]
        detail = client.get("/api/v1/submissions/" + submission_id).json()

        def create(path: str, body: Any) -> Any:
            response = client.post(
                "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid4())}
            )
            assert response.status_code in {200, 201}, response.text
            return response.json()

        # Real records, so a refusal cannot be a not-found that happens to precede the check.
        upload = create(
            f"/submissions/{submission_id}/uploads",
            {"filename": "fictional.pdf", "sha256": "1" * 64, "byte_size": 1},
        )
        task = create(f"/submissions/{submission_id}/tasks", {"title": "Fictional task"})
        invitation = create(
            "/invitations", {"email": f"invitee-{uuid4().hex}@example.invalid", "roles": ["viewer"]}
        )
        ids = {
            "submission_id": submission_id,
            "insured_id": detail["insured"]["id"],
            "document_id": detail["documents"][0]["id"],
            "job_id": detail["jobs"][0]["id"],
            "upload_id": upload["id"],
            "task_id": task["id"],
            "invitation_id": invitation["invitation"]["id"],
            "membership_id": actor["membership_id"],
            "number": "0",
            "format": "pdf",
        }
        submission_body = {
            "insured_id": ids["insured_id"],
            "title": "Fictional submission",
            "intended_start": "2027-01-01",
            "intended_end": "2028-01-01",
        }

        # Leave only the viewer role. The API refuses this for the last administrator, so
        # it is set directly; roles are re-read on every request.
        db = Database(matrix_settings)
        try:
            with db.tenant(UUID(actor["tenant_id"]), UUID(actor["user_id"])) as session:
                session.execute(
                    sa.text(
                        "DELETE FROM app.membership_role WHERE tenant_id=:tenant AND membership_id=:member"
                    ),
                    {"tenant": actor["tenant_id"], "member": actor["membership_id"]},
                )
                session.execute(
                    sa.text(
                        "INSERT INTO app.membership_role(tenant_id, membership_id, role_id)"
                        " SELECT :tenant, :member, id FROM app.role WHERE key='viewer'"
                    ),
                    {"tenant": actor["tenant_id"], "member": actor["membership_id"]},
                )
        finally:
            db.close()
        assert client.get("/api/v1/me").json()["roles"] == ["viewer"]

        refused = {}
        for (method, path), body in VIEWER_BODIES.items():
            url = fill(path, ids)
            headers = {"Idempotency-Key": str(uuid4())}
            if body == "multipart":
                response = client.post(
                    url,
                    files={"file": ("fictional.pdf", b"%PDF-1.4\n", "application/pdf")},
                    headers=headers,
                )
            elif method == "PUT":
                response = client.put(url, content=b"x", headers=headers)
            else:
                payload = (
                    submission_body
                    if body == "submission"
                    else {**submission_body, "expected_version": 1}
                    if body == "submission_edit"
                    else body
                )
                if path.endswith("/review-decisions"):
                    # Review checks the optimistic version before its permission; send the
                    # current one so the response is the permission decision.
                    current = client.get("/api/v1/submissions/" + submission_id).json()
                    payload = {**payload, "expected_version": current["submission"]["row_version"]}
                response = client.request(method, url, json=payload, headers=headers)
            refused[f"{method} {path}"] = response.status_code
        refused["GET /api/v1/packages/{package_id}/artifacts/{format}"] = client.get(
            fill("/api/v1/packages/{package_id}/artifacts/{format}", ids)
        ).status_code
        assert {key: code for key, code in refused.items() if code != 403} == {}

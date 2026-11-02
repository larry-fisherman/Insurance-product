"""Regression coverage for the November platform audit using scoped runtime requests."""

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import jwt
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import Connection, Engine
from test_workspace import client as client
from test_workspace import post, reviewed_workspace
from test_workspace import workspace_settings as workspace_settings

from app import identity, oidc
from app.common import now
from app.config import Settings
from app.database.models import AuthSession
from app.domain.cgl import RULE_VERSION, SCHEMA_VERSION
from app.domain.models import CanonicalRevision, Discrepancy
from app.main import create_app

pytestmark = pytest.mark.database


@pytest.fixture(scope="module")
def database_engine(
    database_factory: Callable[[], AbstractContextManager[Engine]],
    migration_config: Callable[[Connection], Config],
) -> Iterator[Engine]:
    # These requests commit work; keep them independent of constraint fixtures
    # that intentionally assert the contents of otherwise-empty tables.
    with database_factory() as engine:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine


def test_new_revision_and_finding_versions_match_actual_rules(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    submission, _ = reviewed_workspace(client, workspace_settings)
    with database_engine.connect() as connection:
        rows = connection.execute(
            sa.select(CanonicalRevision.schema_version, CanonicalRevision.snapshot).where(
                CanonicalRevision.submission_id == UUID(submission)
            )
        ).all()
        rules = set(
            connection.scalars(
                sa.select(Discrepancy.rule_version).where(
                    Discrepancy.submission_id == UUID(submission)
                )
            )
        )
    assert rows and rules == {RULE_VERSION}
    for version, snapshot in rows:
        assert version == snapshot["schema_version"] == SCHEMA_VERSION
        assert snapshot["rule_version"] == RULE_VERSION


def test_signed_provider_authentication_age_is_preserved_for_sensitive_actions(
    workspace_settings: Settings, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = workspace_settings.model_copy(
        update={
            "auth_mode": "oidc",
            "oidc_issuer": "https://identity.example.invalid",
            "oidc_client_id": "test-client",
            "oidc_client_secret": SecretStr("fictional-secret"),
            "oidc_authorization_endpoint": "https://identity.example.invalid/authorize",
            "oidc_token_endpoint": "https://identity.example.invalid/token",
            "oidc_jwks_uri": "https://identity.example.invalid/keys",
        }
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    key.update(kid="test-key", use="sig", alg="RS256")
    base = now()
    authenticated = int((base - timedelta(seconds=590)).timestamp())
    with TestClient(create_app(configuration)) as browser:
        browser.headers["Origin"] = "http://testserver"
        started = browser.get("/api/v1/auth/oidc/start", follow_redirects=False)
        values = parse_qs(urlsplit(started.headers["location"]).query)
        claims = {
            "iss": configuration.oidc_issuer,
            "sub": uuid4().hex,
            "aud": "test-client",
            "nonce": values["nonce"][0],
            "iat": int(base.timestamp()),
            "exp": int((base + timedelta(minutes=5)).timestamp()),
            "auth_time": authenticated,
            "email": f"test-{uuid4().hex}@example.invalid",
            "email_verified": True,
            "amr": ["mfa"],
        }
        token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})

        def response_data(client: Any, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
            return {"id_token": token} if method == "POST" else {"keys": [key]}

        monkeypatch.setattr(oidc, "bounded_json", response_data)
        callback = browser.get(
            f"/api/v1/auth/oidc/callback?state={values['state'][0]}&code=fictional",
            follow_redirects=False,
        )
        assert "error=" not in callback.headers["location"]
        actor = browser.get("/api/v1/me").json()
        with database_engine.connect() as connection:
            persisted = connection.scalar(
                sa.select(AuthSession.authenticated_at).where(
                    AuthSession.id == UUID(actor["session_id"])
                )
            )
        assert persisted == datetime.fromtimestamp(authenticated, UTC)
        assert post(browser, "/auth/sessions/revoke-others", {}).status_code == 200
        monkeypatch.setattr(identity, "now", lambda: base + timedelta(seconds=20))
        result = post(browser, "/auth/sessions/revoke-others", {})
        assert result.status_code == 403
        assert result.json()["detail"]["code"] == "REAUTHENTICATION_REQUIRED"

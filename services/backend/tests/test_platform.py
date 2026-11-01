"""Security boundaries independent of external identity and lead services."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import oidc
from app.common import now
from app.config import Environment, Settings
from app.demo import pdf
from app.inquiries import InquirySettings, create_app, engine_for, inquiries, metadata
from app.parser_service import run_parser


def oidc_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=Environment.TEST,
        auth_mode="oidc",
        oidc_issuer="https://identity.example.invalid",
        oidc_client_id="fictional-client",
        oidc_client_secret=SecretStr("fictional-secret"),
        oidc_authorization_endpoint="https://identity.example.invalid/authorize",
        oidc_token_endpoint="https://identity.example.invalid/token",
        oidc_jwks_uri="https://identity.example.invalid/keys",
    )


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"iss": "https://attacker.invalid"},
        {"aud": "wrong-client"},
        {"nonce": "wrong"},
        {"email_verified": False},
        {"amr": ["pwd"]},
        {"auth_time": 0},
        {"exp": 0},
        {"azp": "wrong"},
        {"aud": ["fictional-client", "another-client"]},
    ],
)
def test_oidc_cryptographic_claim_validation(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    key.update(kid="test-key", use="sig", alg="RS256")
    claims = {
        "iss": "https://identity.example.invalid",
        "sub": "test-subject",
        "aud": "fictional-client",
        "nonce": "expected",
        "iat": int(now().timestamp()),
        "exp": int((now() + timedelta(minutes=5)).timestamp()),
        "auth_time": int(now().timestamp()),
        "email": "broker@example.invalid",
        "email_verified": True,
        "amr": ["pwd", "mfa"],
        **change,
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})

    def exchange_data(client: Any, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if method == "POST":
            assert kwargs["data"]["code_verifier"] == "pkce-verifier"
            assert kwargs["data"]["redirect_uri"].endswith("/api/v1/auth/oidc/callback")
            return {"id_token": token}
        return {"keys": [key]}

    monkeypatch.setattr(oidc, "bounded_json", exchange_data)
    if change:
        with pytest.raises((ValueError, jwt.PyJWTError)):
            oidc.exchange(oidc_settings(), "authorization-code", "pkce-verifier", "expected")
    else:
        assert (
            oidc.exchange(oidc_settings(), "authorization-code", "pkce-verifier", "expected")["sub"]
            == "test-subject"
        )


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.invalid",
        "//attacker.invalid",
        "/\\attacker.invalid",
        "/dashboard\n",
        "/api/v1/documents/secret",
    ],
)
def test_oidc_return_target_is_allowlisted(path: str) -> None:
    assert oidc.safe_return(path) == "/dashboard"


def test_inquiries_are_isolated_bounded_and_idempotent(tmp_path: Path) -> None:
    configuration = InquirySettings(
        database_url="sqlite:///" + str(tmp_path / "inquiries.sqlite3"), environment="test"
    )
    engine = engine_for(configuration)
    metadata.create_all(engine)
    payload = {
        "purpose": "demo",
        "name": "Fictional Broker",
        "email": "broker@example.invalid",
        "agency": "Fictional Agency",
        "consent": True,
    }
    key = str(uuid4())
    with TestClient(create_app(configuration)) as client:
        assert client.get("/api/v1/me").status_code == 404
        first = client.post("/inquiries", json=payload, headers={"Idempotency-Key": key})
        assert first.status_code == 201
        assert (
            client.post("/inquiries", json=payload, headers={"Idempotency-Key": key}).status_code
            == 200
        )
        assert (
            client.post(
                "/inquiries",
                json={**payload, "agency": "Changed Agency"},
                headers={"Idempotency-Key": key},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/inquiries",
                json={**payload, "tenant_id": str(uuid4())},
                headers={"Idempotency-Key": str(uuid4())},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/inquiries", content=b"x" * 8193, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )
        assert (
            client.post(
                "/inquiries",
                json={**payload, "website": "spam.invalid"},
                headers={"Idempotency-Key": str(uuid4())},
            ).status_code
            == 201
        )
    with engine.connect() as connection:
        rows = connection.execute(inquiries.select()).all()
        assert len(rows) == 1
    engine.dispose()


def test_parser_subprocess_preserves_fictional_source_text() -> None:
    result = run_parser(pdf(["Insured: Fictional Parser Example"]), "application/pdf")
    assert "error" not in result, result
    assert result["page_count"] == 1
    assert any("Fictional Parser Example" in block["text"] for block in result["blocks"])

"""Server-only OIDC code flow. Provider tokens never become browser API credentials."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx2
import jwt
import sqlalchemy as sa
from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse

from app.common import digest, fail, now
from app.config import Settings
from app.database.models import AppUser, Membership, Tenant
from app.domain.models import OauthState
from app.identity import database, set_session, settings, throttle

router = APIRouter(prefix="/api/v1/auth/oidc", tags=["identity"])


def safe_return(value: str) -> str:
    parsed = urlsplit(value)
    if (
        len(value) > 500
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or parsed.scheme
        or parsed.netloc
        or any(ord(c) < 32 for c in value)
    ):
        return "/dashboard"
    if parsed.path not in {
        "/activate",
        "/invite",
        "/onboarding",
        "/profile/security",
        "/dashboard",
    }:
        return "/dashboard"
    return parsed.path


def browser_cookie(configuration: Settings) -> str:
    return "__Host-app_auth" if configuration.public_origin.startswith("https://") else "cgl_oidc"


def callback_url(configuration: Settings) -> str:
    return configuration.public_origin.rstrip("/") + "/api/v1/auth/oidc/callback"


def bounded_json(client: httpx2.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 65536:
                raise ValueError("Identity response exceeds bound")
        import json

        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Invalid identity response")
        return cast(dict[str, Any], value)


def exchange(configuration: Settings, code: str, verifier: str, nonce: str) -> dict[str, Any]:
    assert (
        configuration.oidc_token_endpoint
        and configuration.oidc_jwks_uri
        and configuration.oidc_client_secret
    )
    with httpx2.Client(timeout=10, follow_redirects=False) as client:
        tokens = bounded_json(
            client,
            "POST",
            configuration.oidc_token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url(configuration),
                "client_id": configuration.oidc_client_id,
                "client_secret": configuration.oidc_client_secret.get_secret_value(),
                "code_verifier": verifier,
            },
        )
        token = tokens.get("id_token")
        if not isinstance(token, str) or len(token) > 32000:
            raise ValueError("Missing ID token")
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Unsupported identity signing key")
        keyset = bounded_json(client, "GET", configuration.oidc_jwks_uri)
        keys = keyset.get("keys", [])
        if not isinstance(keys, list) or len(keys) > 30:
            raise ValueError("Invalid signing keys")
        matching = [
            key
            for key in keys
            if isinstance(key, dict)
            and key.get("kid") == header["kid"]
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
            and key.get("alg", "RS256") == "RS256"
        ]
        if len(matching) != 1:
            raise ValueError("Unknown signing key")
        key = jwt.PyJWK.from_dict(matching[0], algorithm="RS256")
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=configuration.oidc_client_id,
            issuer=configuration.oidc_issuer,
            leeway=30,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce", "auth_time"]},
        )
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
        raise ValueError("Invalid nonce")
    if claims.get("azp", configuration.oidc_client_id) != configuration.oidc_client_id:
        raise ValueError("Invalid authorized party")
    if (
        isinstance(claims["aud"], list)
        and len(claims["aud"]) > 1
        and claims.get("azp") != configuration.oidc_client_id
    ):
        raise ValueError("Authorized party required")
    if (
        claims.get("email_verified") is not True
        or not isinstance(claims.get("email"), str)
        or "@" not in claims["email"]
        or len(claims["email"]) > 254
    ):
        raise ValueError("Verified email required")
    if (
        not isinstance(claims.get("amr"), list)
        or configuration.oidc_required_amr not in claims["amr"]
    ):
        raise ValueError("MFA required")
    if (
        type(claims.get("auth_time")) not in (int, float)
        or not -30 <= now().timestamp() - claims["auth_time"] <= 600
    ):
        raise ValueError("Recent authentication required")
    if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
        raise ValueError("Invalid subject")
    return claims


@router.get("/start")
def start(request: Request, return_to: str = "/dashboard") -> RedirectResponse:
    configuration = settings(request)
    if configuration.auth_mode != "oidc":
        fail(404, "OIDC_UNAVAILABLE", "Managed sign-in is not configured.")
    throttle(request, "oidc-start", 15)
    state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    with database(request).identity() as session:
        session.add(
            OauthState(
                state_hash=digest(state),
                browser_hash=digest(browser),
                nonce=nonce,
                verifier=verifier,
                return_path=safe_return(return_to),
                expires_at=now() + timedelta(minutes=10),
            )
        )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = RedirectResponse(
        str(configuration.oidc_authorization_endpoint)
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": configuration.oidc_client_id,
                "scope": "openid profile email",
                "redirect_uri": callback_url(configuration),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "max_age": "0",
                "prompt": "login",
            }
        ),
        status_code=303,
    )
    response.set_cookie(
        browser_cookie(configuration),
        browser,
        httponly=True,
        secure=configuration.public_origin.startswith("https://"),
        samesite="lax",
        path="/",
        max_age=600,
    )
    return response


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    configuration = settings(request)
    if configuration.auth_mode != "oidc":
        fail(404, "OIDC_UNAVAILABLE", "Managed sign-in is not configured.")
    throttle(request, "oidc-callback", 20)
    error = RedirectResponse(configuration.public_origin + "/login?error=identity", status_code=303)
    error.delete_cookie(browser_cookie(configuration), path="/")
    if not 20 <= len(state) <= 100 or not 1 <= len(code) <= 4096:
        return error
    with database(request).identity() as session:
        row = session.scalar(
            sa.select(OauthState).where(OauthState.state_hash == digest(state)).with_for_update()
        )
        if (
            row is None
            or row.consumed_at
            or row.expires_at <= now()
            or not hmac.compare_digest(
                row.browser_hash, digest(request.cookies.get(browser_cookie(configuration), ""))
            )
        ):
            return error
        row.consumed_at = now()
        verifier, nonce, return_path = row.verifier, row.nonce, row.return_path
    try:
        claims = exchange(configuration, code, verifier, nonce)
    except (httpx2.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError):
        return error
    with database(request).identity() as session:
        session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": str(configuration.oidc_issuer) + ":" + claims["sub"]},
        )
        user = session.scalar(
            sa.select(AppUser).where(
                AppUser.identity_issuer == configuration.oidc_issuer,
                AppUser.identity_subject == claims["sub"],
            )
        )
        if user is None:
            user = AppUser(
                id=uuid4(),
                identity_issuer=str(configuration.oidc_issuer),
                identity_subject=claims["sub"],
                display_name=str(claims.get("name") or claims["email"])[:160],
                email=claims["email"].lower(),
                email_verified=True,
            )
            session.add(user)
            session.flush()
        if user.status != "active":
            return error
        # Email changes do not link identities or silently change invitation recipients.
        user.email, user.email_verified = claims["email"].lower(), True
        memberships = list(
            session.scalars(
                sa.select(Membership)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    Membership.user_id == user.id,
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            )
        )
        tenant_id = memberships[0].tenant_id if len(memberships) == 1 else None
        if return_path == "/dashboard":
            return_path = (
                "/agency-select"
                if len(memberships) > 1
                else "/access-pending"
                if not memberships
                else "/dashboard"
            )
        response = RedirectResponse(configuration.public_origin + return_path, status_code=303)
        set_session(
            session,
            response,
            configuration,
            user.id,
            tenant_id,
            verified=True,
            method="oidc",
            authenticated_at=min(datetime.fromtimestamp(claims["auth_time"], UTC), now()),
        )
        response.delete_cookie(browser_cookie(configuration), path="/")
        return response

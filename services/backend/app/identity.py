"""Local review authentication with password/MFA, opaque sessions and tenant RBAC.

Local credentials are restricted to the synthetic review build. Managed OIDC is a separate adapter.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

import pyotp
import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.common import digest, fail, now
from app.config import Environment, Settings
from app.database.models import (
    AppUser,
    AuthSession,
    Membership,
    MembershipRole,
    Permission,
    Role,
    RolePermission,
    Tenant,
)
from app.database.runtime import Database
from app.domain.models import Credential, RecoveryCode

router = APIRouter(prefix="/api/v1", tags=["identity"])
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
dummy_hash = hasher.hash("constant-time-unknown-account-check")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=64)


class Registration(Login):
    name: str = Field(min_length=2, max_length=160)
    agency: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if "@" not in value or any(c.isspace() for c in value):
            raise ValueError("Enter a valid email address")
        return value.lower()


class CodeInput(Input):
    code: str = Field(min_length=6, max_length=64)


class RecoveryInput(Input):
    email: str = Field(min_length=3, max_length=254)
    recovery_code: str = Field(min_length=16, max_length=64)
    new_password: str = Field(min_length=12, max_length=256)


class TenantChoice(Input):
    tenant_id: UUID


@dataclass(frozen=True)
class Actor:
    user_id: UUID
    session_id: UUID
    display_name: str
    email: str | None
    tenant_id: UUID | None
    membership_id: UUID | None
    roles: tuple[str, ...]
    permissions: frozenset[str]
    mfa_verified: bool
    auth_method: str
    context_version: int = 1
    authenticated_at: datetime | None = None


def database(request: Request) -> Database:
    db: Database | None = request.app.state.database
    if db is None:
        fail(503, "WORKSPACE_NOT_CONFIGURED", "Start the configured workspace with make full-up.")
    assert db is not None
    return db


def settings(request: Request) -> Settings:
    result: Settings = request.app.state.settings
    return result


def cipher(configuration: Settings) -> Fernet:
    assert configuration.app_secret is not None
    key = base64.urlsafe_b64encode(
        hashlib.sha256(configuration.app_secret.get_secret_value().encode()).digest()
    )
    return Fernet(key)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = {settings(request).public_origin}
    if settings(request).app_env in (Environment.LOCAL, Environment.TEST):
        allowed |= {"http://localhost:3000", "http://127.0.0.1:3000", "http://testserver"}
    if origin not in allowed:
        fail(403, "ORIGIN_REJECTED", "Request origin is not permitted.")


def throttle(request: Request, key: str, maximum: int = 10) -> None:
    limiter = request.app.state.limiter
    address = request.client.host if request.client else "unknown"
    bucket = digest(address + ":" + key)
    if not limiter.allow(bucket, maximum, 60):
        fail(429, "RATE_LIMITED", "Too many attempts. Please wait a minute and try again.")


def set_session(
    session: Session,
    response: Response,
    configuration: Settings,
    user_id: UUID,
    tenant_id: UUID | None,
    *,
    verified: bool,
    method: str = "password",
) -> AuthSession:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row = AuthSession(
        user_id=user_id,
        secret_hash=digest(token),
        csrf_hash=digest(csrf),
        expires_at=now() + timedelta(hours=8),
        active_tenant_id=tenant_id,
        mfa_verified=verified,
        auth_method=method,
    )
    session.add(row)
    session.flush()
    secure = configuration.public_origin.startswith("https://")
    response.set_cookie(
        configuration.session_cookie,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=28800,
        path="/",
    )
    response.delete_cookie("cgl_session", path="/api/v1")
    response.set_cookie(
        configuration.csrf_cookie, csrf, secure=secure, samesite="lax", max_age=28800, path="/"
    )
    return row


def current_actor(request: Request) -> Actor:
    token = request.cookies.get(settings(request).session_cookie, "")
    if not token or len(token) > 256:
        fail(401, "SIGN_IN_REQUIRED", "Sign in to continue.")
    with database(request).identity() as session:
        row = session.scalar(
            sa.select(AuthSession).where(
                AuthSession.secret_hash == digest(token),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now(),
                AuthSession.last_seen_at
                > now() - timedelta(minutes=settings(request).session_idle_minutes),
            )
        )
        if row is None:
            fail(401, "SESSION_EXPIRED", "Your session has expired. Please sign in.")
        assert row is not None
        row.last_seen_at = now()
        user = session.get(AppUser, row.user_id)
        if user is None or user.status != "active":
            fail(401, "SIGN_IN_REQUIRED", "Sign in to continue.")
        assert user is not None
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_origin(request)
            csrf = request.headers.get("x-csrf-token", "")
            cookie = request.cookies.get(settings(request).csrf_cookie, "")
            if not (
                csrf
                and hmac.compare_digest(csrf, cookie)
                and row.csrf_hash
                and hmac.compare_digest(digest(csrf), row.csrf_hash)
            ):
                fail(403, "CSRF_REJECTED", "Refresh the page and try again.")
        member = (
            session.scalar(
                sa.select(Membership)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    Membership.user_id == user.id,
                    Membership.tenant_id == row.active_tenant_id,
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            )
            if row.active_tenant_id
            else None
        )
        roles: tuple[str, ...] = ()
        permissions: frozenset[str] = frozenset()
        if member:
            roles = tuple(
                session.scalars(
                    sa.select(Role.key)
                    .join(MembershipRole, MembershipRole.role_id == Role.id)
                    .where(
                        MembershipRole.membership_id == member.id,
                        MembershipRole.tenant_id == member.tenant_id,
                    )
                )
            )
            permissions = frozenset(
                session.scalars(
                    sa.select(Permission.key)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .join(Role, Role.id == RolePermission.role_id)
                    .where(Role.key.in_(roles))
                )
            )
        return Actor(
            user.id,
            row.id,
            user.display_name,
            user.email,
            member.tenant_id if member else None,
            member.id if member else None,
            roles,
            permissions,
            row.mfa_verified,
            row.auth_method,
            row.context_version,
            row.authenticated_at,
        )


@dataclass
class Scope:
    session: Session
    actor: Actor
    configuration: Settings

    @property
    def tenant_id(self) -> UUID:
        assert self.actor.tenant_id is not None
        return self.actor.tenant_id

    @property
    def membership_id(self) -> UUID:
        assert self.actor.membership_id is not None
        return self.actor.membership_id

    def require(self, permission: str) -> None:
        if permission not in self.actor.permissions:
            fail(403, "PERMISSION_REQUIRED", "Your role does not permit this action.")
        if self.actor.auth_method != "synthetic-demo-bypass" and permission in {
            "insureds.write",
            "submissions.write",
            "documents.write",
            "reviews.write",
            "packages.generate",
        }:
            from app.platform_models import AgencyProfile

            completed = self.session.scalar(
                sa.select(AgencyProfile.onboarding_completed_at).where(
                    AgencyProfile.tenant_id == self.tenant_id
                )
            )
            if completed is None:
                fail(
                    403,
                    "ONBOARDING_REQUIRED",
                    "An agency administrator must complete Agency setup before starting insurance operations.",
                )


def get_scope(request: Request, actor: Annotated[Actor, Depends(current_actor)]) -> Iterator[Scope]:
    if not actor.mfa_verified:
        fail(403, "MFA_REQUIRED", "Complete multi-factor authentication first.")
    if actor.tenant_id is None or actor.membership_id is None:
        fail(403, "AGENCY_REQUIRED", "Select an active agency to continue.")
    assert actor.tenant_id is not None
    expected = request.headers.get("x-agency-context") or (
        request.query_params.get("agency_context") if request.method in {"GET", "HEAD"} else None
    )
    if expected != f"{actor.tenant_id}:{actor.session_id}:{actor.context_version}":
        fail(
            409,
            "AGENCY_CONTEXT_CHANGED",
            "Your agency context changed. Reload this page before continuing.",
        )
    with database(request).tenant(actor.tenant_id, actor.user_id) as session:
        yield Scope(session, actor, settings(request))


Scoped = Annotated[Scope, Depends(get_scope)]
Authenticated = Annotated[Actor, Depends(current_actor)]


def verify_totp(credential: Credential, code: str, configuration: Settings) -> bool:
    if not credential.totp_secret or not code.isdigit() or len(code) != 6:
        return False
    secret = cipher(configuration).decrypt(credential.totp_secret.encode()).decode()
    totp = pyotp.TOTP(secret)
    step = int(time.time()) // 30
    for candidate in (step - 1, step, step + 1):
        if candidate > credential.last_totp_step and hmac.compare_digest(
            totp.at(candidate * 30), code
        ):
            credential.last_totp_step = candidate
            return True
    return False


@router.post("/auth/register")
def register(payload: Registration, request: Request, response: Response) -> dict[str, Any]:
    require_local(request)
    check_origin(request)
    throttle(request, "register", 4)
    secret = pyotp.random_base32()
    with database(request).identity() as session:
        if session.scalar(sa.select(Credential.user_id).where(Credential.email == payload.email)):
            fail(
                409, "REGISTRATION_UNAVAILABLE", "This email cannot be registered. Try signing in."
            )
        user = AppUser(
            identity_issuer="local-password",
            identity_subject=str(uuid4()),
            display_name=payload.name,
            email=payload.email,
        )
        session.add(user)
        session.flush()
        session.add(
            Credential(
                user_id=user.id,
                email=payload.email,
                password_hash=hasher.hash(payload.password),
                totp_secret=cipher(settings(request)).encrypt(secret.encode()).decode(),
            )
        )
        tenant_id = session.scalar(
            sa.text("SELECT app.create_agency_owner(:user, :name)"),
            {"user": user.id, "name": payload.agency},
        )
        set_session(session, response, settings(request), user.id, tenant_id, verified=False)
    return {
        "mfa_enrollment_required": True,
        "secret": secret,
        "uri": pyotp.TOTP(secret).provisioning_uri(payload.email, issuer_name="CGL Workspace"),
    }


@router.post("/auth/login")
def login(payload: Login, request: Request, response: Response) -> dict[str, Any]:
    require_local(request)
    check_origin(request)
    throttle(request, "login", 8)
    with database(request).identity() as session:
        credential = session.scalar(
            sa.select(Credential).where(Credential.email == payload.email.lower()).with_for_update()
        )
        try:
            hasher.verify(credential.password_hash if credential else dummy_hash, payload.password)
        except VerificationError:
            fail(401, "SIGN_IN_FAILED", "Sign-in unsuccessful. Check your credentials.")
        if credential is None:
            fail(401, "SIGN_IN_FAILED", "Sign-in unsuccessful. Check your credentials.")
        assert credential is not None
        if not credential.mfa_enrolled:
            if not credential.totp_secret:
                fail(401, "SIGN_IN_FAILED", "Use recovery to enroll an authenticator.")
            secret = cipher(settings(request)).decrypt(credential.totp_secret.encode()).decode()
            member = session.scalar(
                sa.select(Membership).where(
                    Membership.user_id == credential.user_id, Membership.status == "active"
                )
            )
            set_session(
                session,
                response,
                settings(request),
                credential.user_id,
                member.tenant_id if member else None,
                verified=False,
            )
            return {
                "mfa_enrollment_required": True,
                "secret": secret,
                "uri": pyotp.TOTP(secret).provisioning_uri(
                    payload.email, issuer_name="CGL Workspace"
                ),
            }
        if not verify_totp(credential, payload.code, settings(request)):
            fail(401, "MFA_REQUIRED", "Enter a new authenticator code.")
        user = session.get(AppUser, credential.user_id)
        if user is None or user.status != "active":
            fail(401, "SIGN_IN_FAILED", "Sign-in unsuccessful. Check your credentials.")
        member = session.scalar(
            sa.select(Membership)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Membership.user_id == credential.user_id,
                Membership.status == "active",
                Tenant.status == "active",
            )
        )
        set_session(
            session,
            response,
            settings(request),
            credential.user_id,
            member.tenant_id if member else None,
            verified=True,
        )
    return {"ok": True}


@router.post("/auth/mfa/confirm")
def confirm_mfa(
    payload: CodeInput, request: Request, response: Response, actor: Authenticated
) -> dict[str, Any]:
    require_local(request)
    throttle(request, "mfa", 8)
    with database(request).identity() as session:
        credential = session.scalar(
            sa.select(Credential).where(Credential.user_id == actor.user_id).with_for_update()
        )
        if (
            credential is None
            or credential.mfa_enrolled
            or not verify_totp(credential, payload.code, settings(request))
        ):
            fail(400, "MFA_INVALID", "Enter a new authenticator code.")
        assert credential is not None
        credential.mfa_enrolled = True
        row = session.get(AuthSession, actor.session_id, with_for_update=True)
        assert row is not None
        row.mfa_verified = True
        row.authenticated_at = now()
        rotate_session(row, response, settings(request), row.active_tenant_id)
        codes = [secrets.token_hex(12) for _ in range(10)]
        session.add_all(
            [RecoveryCode(user_id=actor.user_id, code_hash=digest(code)) for code in codes]
        )
    return {"recovery_codes": codes}


@router.post("/auth/recover")
def recover(payload: RecoveryInput, request: Request, response: Response) -> dict[str, Any]:
    require_local(request)
    check_origin(request)
    throttle(request, "recovery", 5)
    secret = pyotp.random_base32()
    with database(request).identity() as session:
        credential = session.scalar(
            sa.select(Credential).where(Credential.email == payload.email.lower()).with_for_update()
        )
        code = (
            session.scalar(
                sa.select(RecoveryCode)
                .where(
                    RecoveryCode.user_id == credential.user_id,
                    RecoveryCode.code_hash == digest(payload.recovery_code),
                    RecoveryCode.used_at.is_(None),
                )
                .with_for_update()
            )
            if credential
            else None
        )
        if not credential or not code:
            fail(401, "RECOVERY_FAILED", "Recovery unsuccessful. Check your recovery code.")
        assert credential is not None
        credential.password_hash = hasher.hash(payload.new_password)
        credential.totp_secret = cipher(settings(request)).encrypt(secret.encode()).decode()
        credential.mfa_enrolled = False
        credential.last_totp_step = -1
        session.execute(sa.delete(RecoveryCode).where(RecoveryCode.user_id == credential.user_id))
        session.execute(
            sa.update(AuthSession)
            .where(AuthSession.user_id == credential.user_id)
            .values(revoked_at=now())
        )
        member = session.scalar(
            sa.select(Membership).where(
                Membership.user_id == credential.user_id, Membership.status == "active"
            )
        )
        set_session(
            session,
            response,
            settings(request),
            credential.user_id,
            member.tenant_id if member else None,
            verified=False,
        )
    return {
        "mfa_enrollment_required": True,
        "secret": secret,
        "uri": pyotp.TOTP(secret).provisioning_uri(payload.email, issuer_name="CGL Workspace"),
    }


@router.get("/me")
def me(request: Request, actor: Authenticated) -> dict[str, Any]:
    with database(request).identity() as session:
        agencies = [
            {"id": str(tenant.id), "name": tenant.name, "membership_id": str(member.id)}
            for tenant, member in session.execute(
                sa.select(Tenant, Membership)
                .join(Membership, Membership.tenant_id == Tenant.id)
                .where(
                    Membership.user_id == actor.user_id,
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            )
        ]
    return {
        "user_id": str(actor.user_id),
        "name": actor.display_name,
        "email": actor.email,
        "tenant_id": str(actor.tenant_id) if actor.tenant_id else None,
        "membership_id": str(actor.membership_id) if actor.membership_id else None,
        "roles": actor.roles,
        "permissions": sorted(actor.permissions),
        "agencies": agencies,
        "mfa_verified": actor.mfa_verified,
        "auth_method": actor.auth_method,
        "synthetic_only": True,
        "csrf_token": request.cookies.get(settings(request).csrf_cookie, ""),
        "agency_context": f"{actor.tenant_id}:{actor.session_id}:{actor.context_version}"
        if actor.tenant_id
        else None,
        "session_id": str(actor.session_id),
    }


@router.post("/auth/agency")
def switch_agency(
    payload: TenantChoice, request: Request, response: Response, actor: Authenticated
) -> dict[str, bool]:
    with database(request).identity() as session:
        member = session.scalar(
            sa.select(Membership)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Tenant.status == "active",
                Membership.user_id == actor.user_id,
                Membership.tenant_id == payload.tenant_id,
                Membership.status == "active",
            )
        )
        if member is None:
            fail(404, "NOT_FOUND", "Agency not found.")
        row = session.get(AuthSession, actor.session_id, with_for_update=True)
        assert row is not None
        rotate_session(row, response, settings(request), payload.tenant_id)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, actor: Authenticated) -> dict[str, bool]:
    with database(request).identity() as session:
        session.execute(
            sa.update(AuthSession)
            .where(AuthSession.id == actor.session_id)
            .values(revoked_at=now())
        )
    response.delete_cookie(settings(request).session_cookie, path="/")
    response.delete_cookie("cgl_session", path="/api/v1")
    response.delete_cookie(settings(request).csrf_cookie, path="/")
    return {"ok": True}


def require_local(request: Request) -> None:
    if settings(request).auth_mode != "local":
        fail(404, "LOCAL_AUTH_DISABLED", "Use managed sign-in.")


def rotate_session(
    row: AuthSession, response: Response, configuration: Settings, tenant_id: UUID | None
) -> None:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row.secret_hash, row.csrf_hash = digest(token), digest(csrf)
    row.active_tenant_id = tenant_id
    row.context_version += 1
    secure = configuration.public_origin.startswith("https://")
    response.set_cookie(
        configuration.session_cookie,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=max(1, int((row.expires_at - now()).total_seconds())),
        path="/",
    )
    response.set_cookie(
        configuration.csrf_cookie,
        csrf,
        secure=secure,
        samesite="lax",
        max_age=max(1, int((row.expires_at - now()).total_seconds())),
        path="/",
    )


def require_recent(actor: Actor) -> None:
    if actor.auth_method == "synthetic-demo-bypass":
        return
    if actor.authenticated_at is None or actor.authenticated_at < now() - timedelta(minutes=10):
        fail(
            403,
            "REAUTHENTICATION_REQUIRED",
            "Confirm your identity from Profile → Security, then review and retry this action.",
        )


@router.get("/auth/config")
def auth_config(request: Request) -> dict[str, Any]:
    return {
        "mode": settings(request).auth_mode,
        "synthetic_only": True,
        "marketing_origin": settings(request).marketing_origin,
    }


@router.get("/auth/sessions")
def sessions(request: Request, actor: Authenticated) -> dict[str, Any]:
    with database(request).identity() as session:
        rows = session.scalars(
            sa.select(AuthSession)
            .where(
                AuthSession.user_id == actor.user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now(),
            )
            .order_by(AuthSession.created_at.desc())
            .limit(100)
        )
        return {
            "items": [
                {
                    "id": str(row.id),
                    "current": row.id == actor.session_id,
                    "created_at": row.created_at.isoformat(),
                    "last_seen_at": row.last_seen_at.isoformat(),
                    "expires_at": row.expires_at.isoformat(),
                    "method": row.auth_method,
                }
                for row in rows
            ]
        }


@router.post("/auth/sessions/revoke-others")
def revoke_others(request: Request, actor: Authenticated) -> dict[str, bool]:
    require_recent(actor)
    with database(request).identity() as session:
        session.execute(
            sa.update(AuthSession)
            .where(
                AuthSession.user_id == actor.user_id,
                AuthSession.id != actor.session_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now())
        )
    return {"ok": True}


@router.post("/auth/reauthenticate")
def reauthenticate(payload: Login, request: Request, actor: Authenticated) -> dict[str, bool]:
    require_local(request)
    throttle(request, "reauthenticate", 8)
    with database(request).identity() as session:
        credential = session.get(Credential, actor.user_id, with_for_update=True)
        try:
            hasher.verify(credential.password_hash if credential else dummy_hash, payload.password)
        except VerificationError:
            fail(401, "SIGN_IN_FAILED", "Identity confirmation failed.")
        if (
            credential is None
            or credential.email != payload.email.lower()
            or not verify_totp(credential, payload.code, settings(request))
        ):
            fail(401, "SIGN_IN_FAILED", "Identity confirmation failed.")
        row = session.get(AuthSession, actor.session_id)
        assert row is not None
        row.authenticated_at = now()
    return {"ok": True}

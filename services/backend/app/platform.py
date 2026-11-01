"""Agency setup, subscription administration, and identity activation."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.common import audit, canonical_json, digest, fail, now, record
from app.database.models import AuthSession, Membership, MembershipRole, Role, Tenant
from app.domain.models import Submission, UsageEvent
from app.identity import (
    Authenticated,
    Input,
    Scope,
    Scoped,
    database,
    require_recent,
    rotate_session,
    settings,
)
from app.platform_models import AgencyProfile, BillingRequest, Subscription
from app.workflow import get, idempotent

router = APIRouter(prefix="/api/v1", tags=["agency platform"])


class OnboardingEdit(Input):
    name: str = Field(min_length=2, max_length=160)
    timezone: str = Field(min_length=1, max_length=80)
    contact_email: str = Field(min_length=3, max_length=254)
    expected_version: int = Field(ge=1)
    acknowledge: Literal[True]

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid timezone") from None
        return value

    @field_validator("contact_email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if "@" not in value or any(c.isspace() for c in value):
            raise ValueError("Valid email required")
        return value.lower()


class OnboardingResponse(BaseModel):
    name: str
    timezone: str
    contact_email: str
    row_version: int
    completed_at: datetime | None
    has_preparer: bool
    has_approver: bool
    acknowledgment_version: str
    synthetic_only: bool = True


class BillingResponse(BaseModel):
    id: UUID
    plan: str
    status: str
    source: Literal["manual"] = "manual"
    billing_email: str
    row_version: int
    seat_limit: int
    active_seats: int
    submission_limit: int
    submissions_used: int
    period_end: datetime | None
    payment_connected: bool = False
    costs: Literal["unpriced"] = "unpriced"
    requests: list[dict[str, Any]]


class BillingEdit(Input):
    billing_email: str = Field(min_length=3, max_length=254)
    expected_version: int = Field(ge=1)

    @field_validator("billing_email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return OnboardingEdit.valid_email(value)


class BillingChange(Input):
    kind: Literal["plan_change", "cancellation", "data_return"]
    note: str = Field(min_length=10, max_length=1500)


class ActivationInput(Input):
    token: str = Field(min_length=20, max_length=100)
    acknowledge: Literal[True]
    terms_version: Literal["preview-1"]


def subscription(scope: Scope, *, lock: bool = False) -> Subscription:
    statement = sa.select(Subscription).where(Subscription.tenant_id == scope.tenant_id)
    if lock:
        statement = statement.with_for_update()
    row = scope.session.scalar(statement)
    if row is None:
        fail(503, "SUBSCRIPTION_UNAVAILABLE", "Agency subscription setup is unavailable.")
    return row


def require_capacity(
    scope: Scope, kind: Literal["seats", "submissions", "processing"] = "processing"
) -> None:
    get(scope.session, Tenant, scope.tenant_id, lock=True)
    row = subscription(scope, lock=True)
    if row.status not in {"manual_pilot", "active"} or (
        row.period_end is not None and row.period_end <= now()
    ):
        fail(
            403,
            "SUBSCRIPTION_RESTRICTED",
            "This agency has read access. Ask a billing administrator to review the subscription.",
        )
    if kind == "seats":
        count = (
            scope.session.scalar(
                sa.select(sa.func.count())
                .select_from(Membership)
                .where(Membership.status == "active")
            )
            or 0
        )
        if count >= row.seat_limit:
            fail(409, "SEAT_LIMIT", "The agency has reached its active employee limit.")
    elif kind == "submissions":
        count = scope.session.scalar(sa.select(sa.func.count()).select_from(Submission)) or 0
        if count >= row.submission_limit:
            fail(409, "SUBMISSION_LIMIT", "The agency has reached its submission limit.")


@router.get("/agency/onboarding", response_model=OnboardingResponse)
def onboarding(scope: Scoped) -> dict[str, Any]:
    scope.require("agency.read")
    tenant = get(scope.session, Tenant, scope.tenant_id)
    profile = scope.session.scalar(
        sa.select(AgencyProfile).where(AgencyProfile.tenant_id == scope.tenant_id)
    )
    if profile is None:
        fail(503, "ONBOARDING_UNAVAILABLE", "Agency setup is unavailable.")
    roles = set(
        scope.session.scalars(
            sa.select(Role.key)
            .join(MembershipRole, MembershipRole.role_id == Role.id)
            .join(Membership, Membership.id == MembershipRole.membership_id)
            .where(Membership.status == "active")
        )
    )
    return {
        "name": tenant.name,
        "timezone": tenant.timezone,
        "contact_email": profile.contact_email,
        "row_version": profile.row_version,
        "completed_at": profile.onboarding_completed_at,
        "has_preparer": "preparer" in roles,
        "has_approver": "approver" in roles,
        "acknowledgment_version": "preview-1",
        "synthetic_only": True,
    }


@router.patch("/agency/onboarding", response_model=OnboardingResponse)
def update_onboarding(payload: OnboardingEdit, scope: Scoped) -> dict[str, Any]:
    scope.require("agency.manage")
    tenant = get(scope.session, Tenant, scope.tenant_id, lock=True)
    profile = scope.session.scalar(
        sa.select(AgencyProfile).where(AgencyProfile.tenant_id == scope.tenant_id).with_for_update()
    )
    if profile is None or profile.row_version != payload.expected_version:
        fail(409, "STALE_SETTINGS", "Agency setup changed. Reload before saving.")
    tenant.name, tenant.timezone = payload.name, payload.timezone
    tenant.row_version += 1
    profile.contact_email = payload.contact_email
    profile.acknowledgments = {
        "version": "preview-1",
        "actor": str(scope.actor.user_id),
        "at": now().isoformat(),
        "scope": "fictional-only",
    }
    profile.onboarding_completed_at = now()
    profile.row_version += 1
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "agency.onboarding_updated",
        "tenant",
        scope.tenant_id,
        {"policy_version": "preview-1"},
    )
    scope.session.flush()
    return onboarding(scope)


@router.get("/billing", response_model=BillingResponse)
def billing(scope: Scoped) -> dict[str, Any]:
    scope.require("billing.read")
    row = subscription(scope)
    return {
        **record(row),
        "active_seats": scope.session.scalar(
            sa.select(sa.func.count()).select_from(Membership).where(Membership.status == "active")
        )
        or 0,
        "submissions_used": scope.session.scalar(sa.select(sa.func.count()).select_from(Submission))
        or 0,
        "requests": [
            record(item)
            for item in scope.session.scalars(
                sa.select(BillingRequest).order_by(BillingRequest.created_at.desc()).limit(50)
            )
        ],
    }


@router.patch("/billing", response_model=BillingResponse)
def edit_billing(payload: BillingEdit, scope: Scoped) -> dict[str, Any]:
    scope.require("billing.manage")
    require_recent(scope.actor)
    row = subscription(scope, lock=True)
    if row.row_version != payload.expected_version:
        fail(409, "STALE_SETTINGS", "Billing details changed. Reload before saving.")
    row.billing_email = payload.billing_email
    row.row_version += 1
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "billing.contact_updated",
        "agency_subscription",
        row.id,
    )
    scope.session.flush()
    return billing(scope)


@router.post("/billing/requests", status_code=201)
def request_billing_change(
    payload: BillingChange, scope: Scoped, idempotency_key: str | None = Header(default=None)
) -> dict[str, Any]:
    scope.require("billing.manage")
    require_recent(scope.actor)
    object_id, exists = idempotent(scope, "billing.request", idempotency_key, payload.model_dump())
    if exists:
        return record(get(scope.session, BillingRequest, object_id))
    row = BillingRequest(
        id=object_id,
        tenant_id=scope.tenant_id,
        requested_by=scope.membership_id,
        **payload.model_dump(),
    )
    scope.session.add(row)
    scope.session.flush()
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "billing.change_requested",
        "billing_request",
        row.id,
        {"kind": payload.kind},
    )
    return record(row)


@router.get("/agency/usage")
def usage(scope: Scoped) -> dict[str, Any]:
    scope.require("usage.read")
    rows = scope.session.execute(
        sa.select(UsageEvent.kind, sa.func.sum(UsageEvent.quantity)).group_by(UsageEvent.kind)
    )
    return {
        "items": [{"kind": kind, "quantity": quantity} for kind, quantity in rows],
        "period": "all_time",
        "cost_status": "unpriced",
        "measured_at": now().isoformat(),
    }


@router.post("/auth/activate")
def activate(
    payload: ActivationInput, request: Request, response: Response, actor: Authenticated
) -> dict[str, str]:
    if not actor.mfa_verified:
        fail(403, "MFA_REQUIRED", "Complete multi-factor authentication first.")
    require_recent(actor)
    with database(request).identity() as session:
        try:
            with session.begin_nested():
                tenant_id = session.scalar(
                    sa.text("SELECT app.activate_agency(:token,:user,:request)"),
                    {
                        "token": digest(payload.token),
                        "user": actor.user_id,
                        "request": digest(
                            canonical_json(
                                {
                                    "user": actor.user_id,
                                    "terms": payload.terms_version,
                                    "acknowledge": payload.acknowledge,
                                }
                            )
                        ),
                    },
                )
        except sa.exc.DBAPIError:
            fail(
                400,
                "ACTIVATION_INVALID",
                "This activation is unavailable for this verified identity.",
            )
        row = session.get(AuthSession, actor.session_id)
        assert row is not None
        rotate_session(row, response, settings(request), tenant_id)
    return {"tenant_id": str(tenant_id)}

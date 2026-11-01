"""Shared agency administration and resumable intake; no insurance semantics."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, IdentityMixin
from app.domain.models import TenantRecord, owned


class AgencyProfile(Base, TenantRecord):
    __tablename__ = "agency_profile"
    __table_args__ = (
        *owned("agency_profile"),
        sa.UniqueConstraint("tenant_id", name="uq_agency_profile_single_tenant"),
    )
    contact_email: Mapped[str] = mapped_column(sa.Text, server_default="")
    acknowledgments: Mapped[dict[str, str]] = mapped_column(
        JSONB, server_default=sa.text("'{}'::jsonb")
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))


class Subscription(Base, TenantRecord):
    __tablename__ = "agency_subscription"
    __table_args__ = (
        *owned("agency_subscription"),
        sa.UniqueConstraint("tenant_id", name="uq_agency_subscription_single_tenant"),
        sa.CheckConstraint("seat_limit > 0 AND submission_limit > 0", name="positive_limits"),
        sa.CheckConstraint(
            "status IN ('manual_pilot','active','restricted','cancelled')", name="valid_status"
        ),
    )
    plan: Mapped[str] = mapped_column(sa.Text, server_default="synthetic-review")
    status: Mapped[str] = mapped_column(sa.Text, server_default="manual_pilot")
    seat_limit: Mapped[int] = mapped_column(server_default=sa.text("25"))
    submission_limit: Mapped[int] = mapped_column(server_default=sa.text("100"))
    billing_email: Mapped[str] = mapped_column(sa.Text, server_default="")
    period_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))


class BillingRequest(Base, TenantRecord):
    __tablename__ = "billing_request"
    __table_args__ = (*owned("billing_request", requested_by="membership"),)
    kind: Mapped[str] = mapped_column(sa.Text)
    note: Mapped[str] = mapped_column(sa.Text)
    requested_by: Mapped[UUID] = mapped_column(sa.Uuid)


class AgencyActivation(Base, IdentityMixin):
    __tablename__ = "agency_activation"
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    email: Mapped[str] = mapped_column(sa.Text)
    agency_name: Mapped[str] = mapped_column(sa.Text)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    consumed_by: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.app_user.id"))
    tenant_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.tenant.id"))
    request_hash: Mapped[str | None] = mapped_column(sa.String(64))
    operator_reference: Mapped[str] = mapped_column(sa.Text)


class UploadSession(Base, TenantRecord):
    __tablename__ = "upload_session"
    __table_args__ = (
        *owned("upload_session", submission_id="submission", document_id="document_version"),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 52428800", name="bounded_size"),
    )
    submission_id: Mapped[UUID] = mapped_column(sa.Uuid)
    filename: Mapped[str] = mapped_column(sa.Text)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    byte_size: Mapped[int] = mapped_column(sa.Integer)
    supersedes_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    uploaded_by: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    document_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    status: Mapped[str] = mapped_column(sa.Text, server_default="open")


class UploadPart(Base, TenantRecord):
    __tablename__ = "upload_part"
    __table_args__ = (
        *owned("upload_part", upload_id="upload_session"),
        sa.UniqueConstraint("tenant_id", "upload_id", "number", name="uq_upload_part_number"),
    )
    upload_id: Mapped[UUID] = mapped_column(sa.Uuid)
    number: Mapped[int] = mapped_column(sa.Integer)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    byte_size: Mapped[int] = mapped_column(sa.Integer)
    object_key: Mapped[str] = mapped_column(sa.Text)
    storage_version: Mapped[str | None] = mapped_column(sa.Text)

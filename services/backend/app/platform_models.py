"""Shared agency administration and resumable intake; no insurance semantics."""

from datetime import datetime
from typing import Any
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
        sa.CheckConstraint("cleanup_hold_version > 0", name="positive_cleanup_hold_version"),
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
    cleanup_hold: Mapped[bool] = mapped_column(server_default=sa.false())
    cleanup_hold_version: Mapped[int] = mapped_column(server_default=sa.text("1"))


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


class UploadCleanup(Base, TenantRecord):
    __tablename__ = "upload_cleanup"
    __table_args__ = (
        *owned("upload_cleanup", upload_id="upload_session", requested_by="membership"),
        sa.CheckConstraint(
            "jsonb_typeof(objects) = 'array' AND jsonb_array_length(objects) BETWEEN 1 AND 100",
            name="bounded_manifest",
        ),
    )
    upload_id: Mapped[UUID] = mapped_column(sa.Uuid)
    requested_by: Mapped[UUID] = mapped_column(sa.Uuid)
    reason: Mapped[str] = mapped_column(sa.Text)
    policy_version: Mapped[str] = mapped_column(sa.Text)
    objects: Mapped[list[dict[str, str | int]]] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completion_summary: Mapped[dict[str, int]] = mapped_column(
        JSONB, server_default=sa.text("'{}'::jsonb")
    )


class StorageLimit(Base):
    """Migration-managed singleton; runtime has no grant (read by definer functions)."""

    __tablename__ = "storage_limit"
    __table_args__ = (
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint(
            "tenant_byte_limit > 0 AND total_byte_limit > 0 AND entry_limit > 0",
            name="positive_limits",
        ),
    )
    id: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True)
    tenant_byte_limit: Mapped[int] = mapped_column(sa.BigInteger)
    total_byte_limit: Mapped[int] = mapped_column(sa.BigInteger)
    entry_limit: Mapped[int] = mapped_column(sa.BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class StorageGlobalUsage(Base):
    """Cross-tenant total; runtime has no grant (updated only by definer functions)."""

    __tablename__ = "storage_global_usage"
    __table_args__ = (
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("charged_bytes >= 0 AND entries >= 0", name="non_negative"),
    )
    id: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True)
    charged_bytes: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    entries: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    version: Mapped[int] = mapped_column(sa.BigInteger, server_default="1")


class StorageUsage(Base):
    """Per-tenant ledger counters; runtime may only read its own row."""

    __tablename__ = "storage_usage"
    __table_args__ = (
        sa.CheckConstraint(
            "reserved_bytes >= 0 AND committed_bytes >= 0 AND entries >= 0", name="non_negative"
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"), primary_key=True)
    reserved_bytes: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    committed_bytes: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    entries: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    initialized: Mapped[bool] = mapped_column(server_default=sa.false())
    version: Mapped[int] = mapped_column(sa.BigInteger, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class StorageReservation(Base, IdentityMixin):
    """Durable write intent committed before every object write.

    Deliberately without tenant/membership foreign keys (see migration 0010): the
    reservation transaction must not wait on the tenant row a business transaction
    locks. Forced RLS and the definer function bind the tenant.
    """

    __tablename__ = "storage_reservation"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_reservation_tenant_id"),
        sa.UniqueConstraint("object_key", name="uq_storage_reservation_object_key"),
        sa.Index("ix_storage_reservation_slot", "tenant_id", "purpose", "owner_id", "slot"),
        sa.Index("ix_storage_reservation_state", "tenant_id", "state", "expires_at"),
        sa.CheckConstraint(
            "purpose IN ('upload_part','document','artifact')", name="valid_purpose"
        ),
        sa.CheckConstraint(
            "state IN ('reserved','committed','cleanup_planned','released')", name="valid_state"
        ),
        sa.CheckConstraint("byte_size >= 0", name="non_negative_size"),
        sa.CheckConstraint("length(slot) BETWEEN 1 AND 100", name="bounded_slot"),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.Uuid)
    purpose: Mapped[str] = mapped_column(sa.Text)
    owner_id: Mapped[UUID] = mapped_column(sa.Uuid)
    slot: Mapped[str] = mapped_column(sa.Text)
    object_key: Mapped[str] = mapped_column(sa.Text)
    byte_size: Mapped[int] = mapped_column(sa.BigInteger)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    state: Mapped[str] = mapped_column(sa.Text, server_default="reserved")
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[UUID | None] = mapped_column(sa.Uuid)
    linked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    release_ref: Mapped[UUID | None] = mapped_column(sa.Uuid)


class ObjectCleanup(Base, TenantRecord):
    """Immutable intent for deleting reservation-backed orphan objects."""

    __tablename__ = "object_cleanup"
    __table_args__ = (
        *owned("object_cleanup", requested_by="membership"),
        sa.CheckConstraint("category IN ('reservation_orphan')", name="valid_category"),
        sa.CheckConstraint(
            "jsonb_typeof(objects) = 'array' AND jsonb_array_length(objects) BETWEEN 1 AND 100",
            name="bounded_manifest",
        ),
    )
    category: Mapped[str] = mapped_column(sa.Text)
    requested_by: Mapped[UUID] = mapped_column(sa.Uuid)
    reason: Mapped[str] = mapped_column(sa.Text)
    policy_version: Mapped[str] = mapped_column(sa.Text)
    objects: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completion_summary: Mapped[dict[str, int]] = mapped_column(
        JSONB, server_default=sa.text("'{}'::jsonb")
    )


class StorageSweep(Base, TenantRecord):
    """Per-tenant sweep lease and latest report counts; never keys, paths or contents."""

    __tablename__ = "storage_sweep"
    __table_args__ = (
        *owned("storage_sweep"),
        sa.UniqueConstraint("tenant_id", name="uq_storage_sweep_single_tenant"),
        sa.CheckConstraint("attempt >= 0", name="non_negative_attempt"),
        sa.CheckConstraint("origin IN ('scheduler','operator')", name="valid_origin"),
    )
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(sa.Integer, server_default="0")
    origin: Mapped[str] = mapped_column(sa.Text, server_default="scheduler")
    reported_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    report_attempt: Mapped[int | None] = mapped_column(sa.Integer)
    orphan_candidates: Mapped[int | None] = mapped_column(sa.BigInteger)
    expired_absent: Mapped[int | None] = mapped_column(sa.BigInteger)
    legacy_unreserved: Mapped[int | None] = mapped_column(sa.BigInteger)
    legacy_unreserved_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    anomalies: Mapped[int | None] = mapped_column(sa.BigInteger)
    drift_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    drift_entries: Mapped[int | None] = mapped_column(sa.BigInteger)
    reconciled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

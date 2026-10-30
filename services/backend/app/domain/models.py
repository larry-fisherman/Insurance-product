"""Tenant-owned workflow records. Immutable evidence stays separate from reviewed facts."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, IdentityMixin


def owned(name: str, **links: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id"),
        *(
            sa.ForeignKeyConstraint(
                ["tenant_id", column],
                [f"app.{target}.tenant_id", f"app.{target}.id"],
                name=f"fk_{name}_{column}",
            )
            for column, target in links.items()
        ),
    )


class TenantRecord(IdentityMixin):
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"))


class Credential(Base):
    __tablename__ = "credential"
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"), primary_key=True)
    email: Mapped[str] = mapped_column(sa.Text, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    totp_secret: Mapped[str | None] = mapped_column(sa.Text)
    mfa_enrolled: Mapped[bool] = mapped_column(server_default=sa.false())
    last_totp_step: Mapped[int] = mapped_column(sa.BigInteger, server_default=sa.text("-1"))


class RecoveryCode(Base):
    __tablename__ = "recovery_code"
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"), primary_key=True)
    code_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class OauthState(Base):
    __tablename__ = "oauth_state"
    state_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    verifier: Mapped[str] = mapped_column(sa.Text)
    nonce: Mapped[str] = mapped_column(sa.Text)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class InsuredAccount(Base, TenantRecord):
    __tablename__ = "insured_account"
    __table_args__ = (
        *owned("insured_account"),
        sa.CheckConstraint("btrim(name) <> ''", name="name"),
    )
    name: Mapped[str] = mapped_column(sa.Text)
    dba: Mapped[str | None] = mapped_column(sa.Text)
    entity_type: Mapped[str | None] = mapped_column(sa.Text)
    mailing_address: Mapped[str | None] = mapped_column(sa.Text)
    operations: Mapped[str | None] = mapped_column(sa.Text)
    external_reference: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, server_default="active")
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))


class Submission(Base, TenantRecord):
    __tablename__ = "submission"
    __table_args__ = (
        *owned("submission", insured_id="insured_account", prior_submission_id="submission"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assigned_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
        ),
        sa.CheckConstraint("intended_end > intended_start", name="valid_dates"),
        sa.CheckConstraint("kind IN ('new_business','renewal')", name="kind"),
        sa.Index("ix_submission_tenant_due", "tenant_id", "due_date", "status"),
    )
    insured_id: Mapped[UUID]
    prior_submission_id: Mapped[UUID | None]
    title: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.Text, server_default="new_business")
    line: Mapped[str] = mapped_column(sa.Text, server_default="cgl")
    intended_start: Mapped[date]
    intended_end: Mapped[date]
    due_date: Mapped[date | None]
    loss_required_start: Mapped[date | None]
    loss_required_end: Mapped[date | None]
    assigned_membership_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(sa.Text, server_default="collecting")
    input_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    revision_number: Mapped[int] = mapped_column(server_default=sa.text("0"))
    requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa.text("'{}'::jsonb")
    )
    renewal_candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=sa.text("'[]'::jsonb")
    )


class DocumentVersion(Base, TenantRecord):
    __tablename__ = "document_version"
    __table_args__ = (
        *owned("document_version", submission_id="submission", supersedes_id="document_version"),
        sa.Index("ix_document_tenant_submission", "tenant_id", "submission_id"),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 52428800", name="bounded_size"),
    )
    submission_id: Mapped[UUID]
    supersedes_id: Mapped[UUID | None]
    filename: Mapped[str] = mapped_column(sa.Text)
    media_type: Mapped[str] = mapped_column(sa.Text)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    byte_size: Mapped[int]
    object_key: Mapped[str] = mapped_column(sa.Text)
    storage_version: Mapped[str | None] = mapped_column(sa.Text)
    fixture_signature: Mapped[str | None] = mapped_column(sa.Text)
    scan_status: Mapped[str] = mapped_column(sa.Text, server_default="quarantined")
    processing_status: Mapped[str] = mapped_column(sa.Text, server_default="queued")
    document_type: Mapped[str] = mapped_column(sa.Text, server_default="unknown")
    page_count: Mapped[int] = mapped_column(server_default=sa.text("0"))
    excluded_reason: Mapped[str | None] = mapped_column(sa.Text)
    excluded_by: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.app_user.id"))
    excluded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    processing_inventory: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa.text("'{}'::jsonb")
    )
    uploaded_by: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))


class SourceBlock(Base, TenantRecord):
    __tablename__ = "source_block"
    __table_args__ = (*owned("source_block", document_id="document_version"),)
    document_id: Mapped[UUID]
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(sa.Text)
    parser_version: Mapped[str] = mapped_column(sa.Text)


class Observation(Base, TenantRecord):
    __tablename__ = "observation"
    __table_args__ = (
        *owned(
            "observation",
            submission_id="submission",
            document_id="document_version",
            source_id="source_block",
        ),
        sa.Index("ix_observation_tenant_group", "tenant_id", "submission_id", "group_key"),
    )
    submission_id: Mapped[UUID]
    document_id: Mapped[UUID]
    source_id: Mapped[UUID]
    supporting_source_ids: Mapped[list[str]] = mapped_column(
        JSONB, server_default=sa.text("'[]'::jsonb")
    )
    field: Mapped[str] = mapped_column(sa.Text)
    group_key: Mapped[str] = mapped_column(sa.Text)
    value: Mapped[Any] = mapped_column(JSONB)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_value: Mapped[str] = mapped_column(sa.Text)
    method: Mapped[str] = mapped_column(sa.Text)
    pipeline_version: Mapped[str] = mapped_column(sa.Text)
    warnings: Mapped[list[str]] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    availability: Mapped[str] = mapped_column(sa.Text, server_default="available")
    support_status: Mapped[str] = mapped_column(sa.Text, server_default="supported")


class ProcessingJob(Base, TenantRecord):
    __tablename__ = "processing_job"
    __table_args__ = (
        *owned(
            "processing_job",
            submission_id="submission",
            document_id="document_version",
            package_id="package",
        ),
        sa.Index("ix_processing_job_tenant_status", "tenant_id", "status"),
    )
    submission_id: Mapped[UUID]
    document_id: Mapped[UUID | None]
    package_id: Mapped[UUID | None]
    actor_user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    stage: Mapped[str] = mapped_column(sa.Text, server_default="validate")
    kind: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default=sa.text("0"))
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Discrepancy(Base, TenantRecord):
    __tablename__ = "discrepancy"
    __table_args__ = (*owned("discrepancy", submission_id="submission"),)
    submission_id: Mapped[UUID]
    key: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.Text)
    severity: Mapped[str] = mapped_column(sa.Text)
    message: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, server_default="open")
    input_fingerprint: Mapped[str] = mapped_column(sa.Text)
    observation_ids: Mapped[list[str]] = mapped_column(JSONB)
    resolution_reason: Mapped[str | None] = mapped_column(sa.Text)
    resolved_by: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.app_user.id"))
    rule_version: Mapped[str] = mapped_column(sa.Text, server_default="cgl-1")


class ReviewDecision(Base, TenantRecord):
    __tablename__ = "review_decision"
    __table_args__ = (
        *owned("review_decision", submission_id="submission", observation_id="observation"),
    )
    submission_id: Mapped[UUID]
    sequence: Mapped[int]
    group_key: Mapped[str] = mapped_column(sa.Text)
    input_fingerprint: Mapped[str] = mapped_column(sa.Text)
    action: Mapped[str] = mapped_column(sa.Text)
    field: Mapped[str] = mapped_column(sa.Text)
    value: Mapped[Any] = mapped_column(JSONB)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observation_id: Mapped[UUID | None]
    reason: Mapped[str] = mapped_column(sa.Text)
    reviewer_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    support: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))


class CanonicalRevision(Base, TenantRecord):
    __tablename__ = "canonical_revision"
    __table_args__ = (
        *owned("canonical_revision", submission_id="submission"),
        sa.UniqueConstraint("tenant_id", "submission_id", "number", name="uq_revision_number"),
    )
    submission_id: Mapped[UUID]
    number: Mapped[int]
    input_version: Mapped[int]
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    schema_version: Mapped[str] = mapped_column(sa.Text, server_default="cgl-1")
    created_by: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))


class Package(Base, TenantRecord):
    __tablename__ = "package"
    __table_args__ = (
        *owned("package", submission_id="submission", revision_id="canonical_revision"),
    )
    submission_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    input_version: Mapped[int]
    status: Mapped[str] = mapped_column(sa.Text, server_default="queued")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    manifest_hash: Mapped[str | None] = mapped_column(sa.String(64))
    template_version: Mapped[str] = mapped_column(sa.Text, server_default="original-cgl-2")


class PackageArtifact(Base, TenantRecord):
    __tablename__ = "package_artifact"
    __table_args__ = (*owned("package_artifact", package_id="package"),)
    package_id: Mapped[UUID]
    format: Mapped[str] = mapped_column(sa.Text)
    filename: Mapped[str] = mapped_column(sa.Text)
    media_type: Mapped[str] = mapped_column(sa.Text)
    object_key: Mapped[str] = mapped_column(sa.Text)
    storage_version: Mapped[str | None] = mapped_column(sa.Text)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    byte_size: Mapped[int]


class Approval(Base, TenantRecord):
    __tablename__ = "approval"
    __table_args__ = (*owned("approval", package_id="package"), sa.UniqueConstraint("package_id"))
    package_id: Mapped[UUID]
    approver_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    manifest_hash: Mapped[str] = mapped_column(sa.String(64))
    revision_hash: Mapped[str] = mapped_column(sa.String(64))
    attestation: Mapped[str] = mapped_column(sa.Text)


class ExportEvent(Base, TenantRecord):
    __tablename__ = "export_event"
    __table_args__ = (*owned("export_event", package_id="package", approval_id="approval"),)
    package_id: Mapped[UUID]
    approval_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    format: Mapped[str] = mapped_column(sa.Text)


class WorkItem(Base, TenantRecord):
    __tablename__ = "work_item"
    __table_args__ = (*owned("work_item", submission_id="submission"),)
    submission_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, server_default="open")
    due_date: Mapped[date | None]
    owner_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.app_user.id"))
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    resolution_reason: Mapped[str | None] = mapped_column(sa.Text)


class UsageEvent(Base, TenantRecord):
    __tablename__ = "usage_event"
    __table_args__ = (*owned("usage_event", submission_id="submission"),)
    submission_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(sa.Text)
    quantity: Mapped[int]
    estimated_cost: Mapped[str | None] = mapped_column(sa.Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IdempotencyRecord(Base, TenantRecord):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        *owned("idempotency_record"),
        sa.UniqueConstraint("tenant_id", "operation", "key", name="uq_idempotency_request"),
    )
    operation: Mapped[str] = mapped_column(sa.Text)
    key: Mapped[str] = mapped_column(sa.String(100))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    response_id: Mapped[UUID]

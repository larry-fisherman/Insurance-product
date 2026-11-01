"""Identity/tenant schema and operational ledgers; no insurance records or access API yet."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(schema="app", naming_convention=NAMING_CONVENTION)


class IdentityMixin:
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Tenant(Base, IdentityMixin):
    __tablename__ = "tenant"
    __table_args__ = (
        sa.CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        sa.CheckConstraint("btrim(timezone) <> ''", name="timezone_not_blank"),
        sa.CheckConstraint("status IN ('active', 'suspended', 'archived')", name="valid_status"),
        sa.CheckConstraint("row_version > 0", name="positive_version"),
    )
    name: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, server_default="active")
    timezone: Mapped[str] = mapped_column(sa.Text, server_default="UTC")
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))


class AppUser(Base, IdentityMixin):
    __tablename__ = "app_user"
    __table_args__ = (
        sa.UniqueConstraint("identity_issuer", "identity_subject", name="uq_app_user_identity"),
        sa.CheckConstraint("btrim(identity_issuer) <> ''", name="issuer_not_blank"),
        sa.CheckConstraint("btrim(identity_subject) <> ''", name="subject_not_blank"),
        sa.CheckConstraint("btrim(display_name) <> ''", name="display_name_not_blank"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="valid_status"),
    )
    identity_issuer: Mapped[str] = mapped_column(sa.Text)
    identity_subject: Mapped[str] = mapped_column(sa.Text)
    display_name: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text, server_default="active")
    email: Mapped[str | None] = mapped_column(sa.Text)
    email_verified: Mapped[bool] = mapped_column(server_default=sa.false())


class Membership(Base, IdentityMixin):
    __tablename__ = "membership"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "id", name="uq_membership_tenant_id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="valid_status"),
        sa.CheckConstraint("row_version > 0", name="positive_version"),
        sa.Index("ix_membership_user_status", "user_id", "status"),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"))
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    status: Mapped[str] = mapped_column(sa.Text, server_default="active")
    row_version: Mapped[int] = mapped_column(server_default=sa.text("1"))


class Role(Base, IdentityMixin):
    __tablename__ = "role"
    __table_args__ = (
        sa.UniqueConstraint("key", name="uq_role_key"),
        sa.CheckConstraint("btrim(key) <> ''", name="key_not_blank"),
    )
    key: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str] = mapped_column(sa.Text)


class Permission(Base, IdentityMixin):
    __tablename__ = "permission"
    __table_args__ = (
        sa.UniqueConstraint("key", name="uq_permission_key"),
        sa.CheckConstraint("btrim(key) <> ''", name="key_not_blank"),
    )
    key: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str] = mapped_column(sa.Text)


class RolePermission(Base):
    __tablename__ = "role_permission"
    role_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.role.id"), primary_key=True)
    permission_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("app.permission.id"), primary_key=True
    )


class MembershipRole(Base):
    __tablename__ = "membership_role"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_membership_role_tenant_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_membership_role_tenant_grantor",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"), primary_key=True)
    membership_id: Mapped[UUID] = mapped_column(primary_key=True)
    role_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.role.id"), primary_key=True)
    granted_by_membership_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class Invitation(Base, IdentityMixin):
    __tablename__ = "invitation"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "id", name="uq_invitation_tenant_id"),
        sa.UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "inviter_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_invitation_tenant_inviter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "accepted_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_invitation_tenant_acceptor",
        ),
        sa.CheckConstraint("btrim(email) <> '' AND email = lower(btrim(email))", name="email_key"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_format"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')", name="valid_status"
        ),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL"
            " AND accepted_membership_id IS NOT NULL)"
            " OR (status <> 'accepted' AND accepted_at IS NULL AND accepted_membership_id IS NULL)",
            name="accepted_state",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= created_at AND accepted_at < expires_at)",
            name="accepted_during_validity",
        ),
        sa.Index(
            "uq_invitation_pending_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        ),
        sa.Index("ix_invitation_tenant_expiry", "tenant_id", "expires_at"),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"))
    email: Mapped[str] = mapped_column(sa.Text)
    token_hash: Mapped[str] = mapped_column(sa.String(64))
    inviter_membership_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(sa.Text, server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    accepted_membership_id: Mapped[UUID | None]
    role_keys: Mapped[list[str]] = mapped_column(
        JSONB, server_default=sa.text("'[\"preparer\"]'::jsonb")
    )


class AuthSession(Base, IdentityMixin):
    __tablename__ = "auth_session"
    __table_args__ = (
        sa.UniqueConstraint("secret_hash", name="uq_auth_session_secret_hash"),
        sa.CheckConstraint("secret_hash ~ '^[0-9a-f]{64}$'", name="secret_hash_format"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at", name="valid_revocation"
        ),
        sa.Index("ix_auth_session_user_expiry", "user_id", "expires_at"),
    )
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.app_user.id"))
    secret_hash: Mapped[str] = mapped_column(sa.String(64))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    csrf_hash: Mapped[str | None] = mapped_column(sa.String(64))
    auth_method: Mapped[str] = mapped_column(sa.Text, server_default="pending")
    mfa_verified: Mapped[bool] = mapped_column(server_default=sa.false())
    active_tenant_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.tenant.id"))
    context_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    authenticated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class AuditEvent(Base, IdentityMixin):
    __tablename__ = "audit_event"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "id", name="uq_audit_event_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_audit_event_tenant_actor",
        ),
        sa.CheckConstraint(
            "(actor_type = 'member' AND actor_membership_id IS NOT NULL AND actor_user_id IS NULL)"
            " OR (actor_type = 'system' AND actor_membership_id IS NULL AND actor_user_id IS NULL)"
            " OR (actor_type = 'platform_operator' AND actor_membership_id IS NULL"
            " AND actor_user_id IS NOT NULL)",
            name="actor_identity",
        ),
        sa.CheckConstraint(
            "btrim(action) <> '' AND btrim(object_type) <> ''", name="nonblank_action"
        ),
        sa.CheckConstraint("jsonb_typeof(change_summary) = 'object'", name="summary_object"),
        sa.Index("ix_audit_event_tenant_time", "tenant_id", "created_at"),
        sa.Index("ix_audit_event_tenant_object", "tenant_id", "object_type", "object_id"),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"))
    actor_type: Mapped[str] = mapped_column(sa.Text)
    actor_membership_id: Mapped[UUID | None]
    actor_user_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("app.app_user.id"))
    action: Mapped[str] = mapped_column(sa.Text)
    object_type: Mapped[str] = mapped_column(sa.Text)
    object_id: Mapped[UUID | None]
    request_id: Mapped[UUID]
    change_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa.text("'{}'::jsonb")
    )


class OutboxEvent(Base, IdentityMixin):
    __tablename__ = "outbox_event"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "id", name="uq_outbox_event_tenant_id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_outbox_event_tenant_dedupe"),
        sa.CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
        sa.CheckConstraint("event_version > 0", name="positive_event_version"),
        sa.CheckConstraint(
            "btrim(event_type) <> '' AND btrim(aggregate_type) <> ''", name="nonblank_type"
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at >= created_at", name="valid_publish"
        ),
        sa.Index(
            "ix_outbox_event_pending",
            "tenant_id",
            "available_at",
            "created_at",
            postgresql_where=sa.text("published_at IS NULL"),
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(sa.ForeignKey("app.tenant.id"))
    event_type: Mapped[str] = mapped_column(sa.Text)
    event_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    aggregate_type: Mapped[str] = mapped_column(sa.Text)
    aggregate_id: Mapped[UUID]
    dedupe_key: Mapped[str | None] = mapped_column(sa.Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(server_default=sa.text("0"))

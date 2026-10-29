"""identity tenancy audit and outbox

Revision ID: 0001_foundation
Revises: none
"""

from collections.abc import Sequence
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("app"))
    op.execute("REVOKE ALL ON SCHEMA app FROM PUBLIC")
    op.create_table(
        "app_user",
        sa.Column("identity_issuer", sa.Text(), nullable=False),
        sa.Column("identity_subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(display_name) <> ''", name=op.f("ck_app_user_display_name_not_blank")
        ),
        sa.CheckConstraint(
            "btrim(identity_issuer) <> ''", name=op.f("ck_app_user_issuer_not_blank")
        ),
        sa.CheckConstraint(
            "btrim(identity_subject) <> ''", name=op.f("ck_app_user_subject_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_app_user_valid_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("identity_issuer", "identity_subject", name="uq_app_user_identity"),
        schema="app",
    )
    op.create_table(
        "permission",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(key) <> ''", name=op.f("ck_permission_key_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission")),
        sa.UniqueConstraint("key", name="uq_permission_key"),
        schema="app",
    )
    op.create_table(
        "role",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(key) <> ''", name=op.f("ck_role_key_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
        sa.UniqueConstraint("key", name="uq_role_key"),
        schema="app",
    )
    op.create_table(
        "tenant",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_tenant_name_not_blank")),
        sa.CheckConstraint("btrim(timezone) <> ''", name=op.f("ck_tenant_timezone_not_blank")),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')", name=op.f("ck_tenant_valid_status")
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_tenant_positive_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant")),
        schema="app",
    )
    op.create_table(
        "auth_session",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "secret_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_auth_session_secret_hash_format")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_auth_session_expiry_after_creation")
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name=op.f("ck_auth_session_valid_revocation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app.app_user.id"], name=op.f("fk_auth_session_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_session")),
        sa.UniqueConstraint("secret_hash", name="uq_auth_session_secret_hash"),
        schema="app",
    )
    op.create_index(
        "ix_auth_session_user_expiry",
        "auth_session",
        ["user_id", "expires_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "membership",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_membership_valid_status")
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_membership_positive_version")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["app.tenant.id"], name=op.f("fk_membership_tenant_id_tenant")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app.app_user.id"], name=op.f("fk_membership_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_membership_tenant_id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        schema="app",
    )
    op.create_index(
        "ix_membership_user_status", "membership", ["user_id", "status"], unique=False, schema="app"
    )
    op.create_table(
        "outbox_event",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> '' AND btrim(aggregate_type) <> ''",
            name=op.f("ck_outbox_event_nonblank_type"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_outbox_event_payload_object")
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_event_nonnegative_attempts")),
        sa.CheckConstraint(
            "event_version > 0", name=op.f("ck_outbox_event_positive_event_version")
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at >= created_at",
            name=op.f("ck_outbox_event_valid_publish"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["app.tenant.id"], name=op.f("fk_outbox_event_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_event")),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_outbox_event_tenant_dedupe"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_outbox_event_tenant_id"),
        schema="app",
    )
    op.create_index(
        "ix_outbox_event_pending",
        "outbox_event",
        ["tenant_id", "available_at", "created_at"],
        unique=False,
        schema="app",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["app.permission.id"],
            name=op.f("fk_role_permission_permission_id_permission"),
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["app.role.id"], name=op.f("fk_role_permission_role_id_role")
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name=op.f("pk_role_permission")),
        schema="app",
    )
    op.create_table(
        "audit_event",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "change_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(actor_type = 'member' AND actor_membership_id IS NOT NULL AND actor_user_id IS NULL)"
            " OR (actor_type = 'system' AND actor_membership_id IS NULL AND actor_user_id IS NULL)"
            " OR (actor_type = 'platform_operator' AND actor_membership_id IS NULL"
            " AND actor_user_id IS NOT NULL)",
            name=op.f("ck_audit_event_actor_identity"),
        ),
        sa.CheckConstraint(
            "btrim(action) <> '' AND btrim(object_type) <> ''",
            name=op.f("ck_audit_event_nonblank_action"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(change_summary) = 'object'", name=op.f("ck_audit_event_summary_object")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app.app_user.id"],
            name=op.f("fk_audit_event_actor_user_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_audit_event_tenant_actor",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["app.tenant.id"], name=op.f("fk_audit_event_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_audit_event_tenant_id"),
        schema="app",
    )
    op.create_index(
        "ix_audit_event_tenant_object",
        "audit_event",
        ["tenant_id", "object_type", "object_id"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "ix_audit_event_tenant_time",
        "audit_event",
        ["tenant_id", "created_at"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "invitation",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("inviter_membership_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_membership_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL"
            " AND accepted_membership_id IS NOT NULL)"
            " OR (status <> 'accepted' AND accepted_at IS NULL AND accepted_membership_id IS NULL)",
            name=op.f("ck_invitation_accepted_state"),
        ),
        sa.CheckConstraint(
            "btrim(email) <> '' AND email = lower(btrim(email))",
            name=op.f("ck_invitation_email_key"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name=op.f("ck_invitation_valid_status"),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_invitation_token_hash_format")
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= created_at AND accepted_at < expires_at)",
            name=op.f("ck_invitation_accepted_during_validity"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_invitation_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "accepted_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_invitation_tenant_acceptor",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "inviter_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_invitation_tenant_inviter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["app.tenant.id"], name=op.f("fk_invitation_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitation")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_invitation_tenant_id"),
        sa.UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
        schema="app",
    )
    op.create_index(
        "ix_invitation_tenant_expiry",
        "invitation",
        ["tenant_id", "expires_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        "uq_invitation_pending_email",
        "invitation",
        ["tenant_id", "email"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "membership_role",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_membership_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["app.role.id"], name=op.f("fk_membership_role_role_id_role")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "granted_by_membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_membership_role_tenant_grantor",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_membership_role_tenant_membership",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["app.tenant.id"], name=op.f("fk_membership_role_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "membership_id", "role_id", name=op.f("pk_membership_role")
        ),
        schema="app",
    )
    seed_catalog()
    op.execute("""
        CREATE FUNCTION app.reject_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, app AS $$
        BEGIN
            RAISE EXCEPTION 'Audit events are append-only' USING ERRCODE = '55000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_event_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON app.audit_event
        FOR EACH STATEMENT EXECUTE FUNCTION app.reject_audit_mutation()
    """)


def downgrade() -> None:
    # Only use on disposable databases or under an explicitly approved recovery plan.
    op.execute("DROP TRIGGER audit_event_append_only ON app.audit_event")
    op.execute("DROP FUNCTION app.reject_audit_mutation()")
    op.drop_table("membership_role", schema="app")
    op.drop_index(
        "uq_invitation_pending_email",
        table_name="invitation",
        schema="app",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index("ix_invitation_tenant_expiry", table_name="invitation", schema="app")
    op.drop_table("invitation", schema="app")
    op.drop_index("ix_audit_event_tenant_time", table_name="audit_event", schema="app")
    op.drop_index("ix_audit_event_tenant_object", table_name="audit_event", schema="app")
    op.drop_table("audit_event", schema="app")
    op.drop_table("role_permission", schema="app")
    op.drop_index(
        "ix_outbox_event_pending",
        table_name="outbox_event",
        schema="app",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("outbox_event", schema="app")
    op.drop_index("ix_membership_user_status", table_name="membership", schema="app")
    op.drop_table("membership", schema="app")
    op.drop_index("ix_auth_session_user_expiry", table_name="auth_session", schema="app")
    op.drop_table("auth_session", schema="app")
    op.drop_table("tenant", schema="app")
    op.drop_table("role", schema="app")
    op.drop_table("permission", schema="app")
    op.drop_table("app_user", schema="app")
    op.execute(sa.schema.DropSchema("app"))


def seed_catalog() -> None:
    # Frozen revision data: do not import mutable application models or future catalogs.
    namespace = UUID("f3a6fdf0-1b2a-4f53-a5c0-111a0fb2297b")
    viewer = {
        "agency.read",
        "members.read",
        "insureds.read",
        "submissions.read",
        "documents.read",
        "reviews.read",
        "packages.read",
        "audit.read",
    }
    bundles = {
        "viewer": viewer,
        "preparer": viewer
        | {
            "insureds.write",
            "submissions.write",
            "documents.write",
            "reviews.write",
            "packages.generate",
            "packages.export",
        },
        "approver": viewer
        | {
            "reviews.write",
            "exceptions.accept",
            "packages.approve",
            "packages.export",
        },
        "tenant_admin": {
            "agency.read",
            "agency.manage",
            "members.read",
            "members.manage",
            "usage.read",
            "audit.read",
            "retention.manage",
            "deletion.request",
        },
    }
    permissions = sorted(set().union(*bundles.values()))
    role_table = sa.table(
        "role",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.Text()),
        sa.column("description", sa.Text()),
        schema="app",
    )
    permission_table = sa.table(
        "permission",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.Text()),
        sa.column("description", sa.Text()),
        schema="app",
    )
    link_table = sa.table(
        "role_permission",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        schema="app",
    )
    op.bulk_insert(
        role_table,
        [
            {
                "id": uuid5(namespace, "role:" + key),
                "key": key,
                "description": key.replace("_", " "),
            }
            for key in sorted(bundles)
        ],
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "id": uuid5(namespace, "permission:" + key),
                "key": key,
                "description": key.replace(".", " "),
            }
            for key in permissions
        ],
    )
    op.bulk_insert(
        link_table,
        [
            {
                "role_id": uuid5(namespace, "role:" + role),
                "permission_id": uuid5(namespace, "permission:" + permission),
            }
            for role in sorted(bundles)
            for permission in sorted(bundles[role])
        ],
    )

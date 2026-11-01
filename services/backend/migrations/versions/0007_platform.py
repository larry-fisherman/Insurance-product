"""Agency lifecycle, session binding, subscriptions and resumable intake.

Revision ID: 0007_platform
Revises: 0006_workspace
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import SchemaItem

revision: str = "0007_platform"
down_revision: str | None = "0006_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def tenant_columns(name: str) -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def tenant_table(name: str, *columns: SchemaItem) -> None:
    op.create_table(
        name,
        *tenant_columns(name),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id"),
        *columns,
        schema="app",
    )
    op.execute(f"ALTER TABLE app.{name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY maintenance ON app.{name} TO cgl_migrator USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY tenant_scope ON app.{name} TO cgl_runtime USING (tenant_id=app.current_tenant_id() AND app.active_member(tenant_id)) WITH CHECK (tenant_id=app.current_tenant_id() AND app.active_member(tenant_id))"
    )
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON app.{name} TO cgl_runtime")


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="app",
    )
    session_columns: list[sa.Column[Any]] = [
        sa.Column("context_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "authenticated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]
    for column in session_columns:
        op.add_column("auth_session", column, schema="app")
    oauth_columns: list[sa.Column[Any]] = [
        sa.Column("browser_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("return_path", sa.Text(), nullable=False, server_default="/dashboard"),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    ]
    for column in oauth_columns:
        op.add_column("oauth_state", column, schema="app")
    tenant_table(
        "agency_profile",
        sa.Column("contact_email", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "acknowledgments", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", name="uq_agency_profile_single_tenant"),
    )
    tenant_table(
        "agency_subscription",
        sa.Column("plan", sa.Text(), nullable=False, server_default="synthetic-review"),
        sa.Column("status", sa.Text(), nullable=False, server_default="manual_pilot"),
        sa.Column("seat_limit", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("submission_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("billing_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", name="uq_agency_subscription_single_tenant"),
        sa.CheckConstraint("seat_limit > 0 AND submission_limit > 0", name="positive_limits"),
        sa.CheckConstraint(
            "status IN ('manual_pilot','active','restricted','cancelled')", name="valid_status"
        ),
    )
    tenant_table(
        "billing_request",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_billing_request_requested_by",
        ),
    )
    op.execute("REVOKE UPDATE ON app.billing_request FROM cgl_runtime")
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON app.billing_request FOR EACH STATEMENT EXECUTE FUNCTION app.reject_immutable_change()"
    )
    op.create_table(
        "agency_activation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("agency_name", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_by", sa.Uuid(), sa.ForeignKey("app.app_user.id")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id")),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("operator_reference", sa.Text(), nullable=False),
        schema="app",
    )
    op.execute("ALTER TABLE app.agency_activation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.agency_activation FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY maintenance ON app.agency_activation TO cgl_migrator USING (true) WITH CHECK (true)"
    )
    tenant_table(
        "upload_session",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid()),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("app.app_user.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document_id", sa.Uuid()),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            ["app.submission.tenant_id", "app.submission.id"],
            name="fk_upload_session_submission_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["app.document_version.tenant_id", "app.document_version.id"],
            name="fk_upload_session_document_id",
        ),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 52428800", name="bounded_size"),
    )
    tenant_table(
        "upload_part",
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("storage_version", sa.Text()),
        sa.UniqueConstraint("tenant_id", "upload_id", "number", name="uq_upload_part_number"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "upload_id"],
            ["app.upload_session.tenant_id", "app.upload_session.id"],
            name="fk_upload_part_upload_id",
        ),
    )
    op.execute("ALTER FUNCTION app.accept_invitation(text,uuid) RENAME TO accept_invitation_legacy")
    op.execute("REVOKE EXECUTE ON FUNCTION app.accept_invitation_legacy(text,uuid) FROM cgl_auth")
    op.execute("""
    CREATE FUNCTION app.accept_invitation(p_hash text,p_user uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
    DECLARE inv app.invitation%ROWTYPE; lim app.agency_subscription%ROWTYPE;
    BEGIN
      SELECT * INTO inv FROM app.invitation WHERE token_hash=p_hash;
      IF NOT FOUND THEN RAISE EXCEPTION 'Invitation unavailable'; END IF;
      PERFORM 1 FROM app.tenant WHERE id=inv.tenant_id AND status='active' FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Agency unavailable'; END IF;
      IF NOT EXISTS(SELECT 1 FROM app.app_user WHERE id=p_user AND status='active'
        AND lower(email)=inv.email AND (email_verified OR identity_issuer='local-password'))
        THEN RAISE EXCEPTION 'Verified matching identity required'; END IF;
      IF EXISTS(SELECT 1 FROM app.membership WHERE tenant_id=inv.tenant_id AND user_id=p_user)
        THEN RAISE EXCEPTION 'Existing membership must be managed by an administrator'; END IF;
      IF NOT EXISTS(SELECT 1 FROM app.membership m JOIN app.membership_role mr ON mr.membership_id=m.id AND mr.tenant_id=m.tenant_id
        JOIN app.role_permission rp ON rp.role_id=mr.role_id JOIN app.permission p ON p.id=rp.permission_id
        WHERE m.id=inv.inviter_membership_id AND m.tenant_id=inv.tenant_id AND m.status='active' AND p.key='members.manage')
        THEN RAISE EXCEPTION 'Inviter no longer authorized'; END IF;
      IF jsonb_array_length(inv.role_keys)=0 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(inv.role_keys) k
        WHERE NOT EXISTS(SELECT 1 FROM app.role r WHERE r.key=k.value)) THEN RAISE EXCEPTION 'Invalid roles'; END IF;
      SELECT * INTO lim FROM app.agency_subscription WHERE tenant_id=inv.tenant_id FOR UPDATE;
      IF lim.status NOT IN ('manual_pilot','active') OR (lim.period_end IS NOT NULL AND lim.period_end<=clock_timestamp())
        OR (SELECT count(*) FROM app.membership WHERE tenant_id=inv.tenant_id AND status='active')>=lim.seat_limit
        THEN RAISE EXCEPTION 'Subscription limit'; END IF;
      RETURN app.accept_invitation_legacy(p_hash,p_user);
    END; $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app.accept_invitation(text,uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.accept_invitation(text,uuid) TO cgl_auth")
    namespace = UUID("f3a6fdf0-1b2a-4f53-a5c0-111a0fb2297b")
    connection = op.get_bind()
    role_id = uuid5(namespace, "role:billing_admin")
    connection.execute(
        sa.text(
            "INSERT INTO app.role(id,key,description) VALUES(:id,'billing_admin','Agency subscription administration')"
        ),
        {"id": role_id},
    )
    for key in ("billing.read", "billing.manage"):
        permission_id = uuid5(namespace, "permission:" + key)
        connection.execute(
            sa.text("INSERT INTO app.permission(id,key,description) VALUES(:id,:key,:key)"),
            {"id": permission_id, "key": key},
        )
        connection.execute(
            sa.text(
                "INSERT INTO app.role_permission(role_id,permission_id) VALUES(:role,:permission)"
            ),
            {"role": role_id, "permission": permission_id},
        )
    op.execute(
        "INSERT INTO app.role_permission(role_id,permission_id) SELECT r.id,p.id FROM app.role r,app.permission p WHERE r.key='billing_admin' AND p.key IN ('agency.read','usage.read')"
    )
    op.execute(
        "INSERT INTO app.agency_subscription(id,tenant_id) SELECT gen_random_uuid(),id FROM app.tenant"
    )
    op.execute(
        "INSERT INTO app.agency_profile(id,tenant_id) SELECT gen_random_uuid(),id FROM app.tenant"
    )
    op.execute("""
    CREATE FUNCTION app.initialize_agency_platform() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$ BEGIN
      INSERT INTO app.agency_profile(id,tenant_id) VALUES(gen_random_uuid(),NEW.id);
      INSERT INTO app.agency_subscription(id,tenant_id) VALUES(gen_random_uuid(),NEW.id);
      RETURN NEW;
    END; $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app.initialize_agency_platform() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER initialize_platform AFTER INSERT ON app.tenant FOR EACH ROW EXECUTE FUNCTION app.initialize_agency_platform()"
    )
    op.execute("""
    CREATE FUNCTION app.create_agency_owner(p_user uuid,p_name text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
    DECLARE t uuid; m uuid;
    BEGIN
      t:=app.bootstrap_agency(p_user,p_name);
      SELECT id INTO m FROM app.membership WHERE tenant_id=t AND user_id=p_user;
      DELETE FROM app.membership_role WHERE tenant_id=t AND membership_id=m;
      INSERT INTO app.membership_role(tenant_id,membership_id,role_id) SELECT t,m,id FROM app.role WHERE key IN ('tenant_admin','billing_admin');
      RETURN t;
    END; $$
    """)
    op.execute("""
    CREATE FUNCTION app.activate_agency(p_hash text,p_user uuid,p_request text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
    DECLARE a app.agency_activation%ROWTYPE; t uuid;
    BEGIN
      SELECT i.* INTO a FROM app.agency_activation i JOIN app.app_user u ON lower(u.email)=i.email
        WHERE i.token_hash=p_hash AND u.id=p_user AND u.status='active' AND u.email_verified FOR UPDATE OF i;
      IF NOT FOUND THEN RAISE EXCEPTION 'Activation unavailable'; END IF;
      IF a.consumed_by IS NOT NULL THEN
        IF a.consumed_by=p_user AND a.request_hash=p_request THEN RETURN a.tenant_id; END IF;
        RAISE EXCEPTION 'Activation already used';
      END IF;
      IF a.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Activation expired'; END IF;
      t:=app.create_agency_owner(p_user,a.agency_name);
      UPDATE app.agency_activation SET consumed_by=p_user,tenant_id=t,request_hash=p_request WHERE id=a.id;
      UPDATE app.agency_profile SET acknowledgments=jsonb_build_object('terms','preview-1','authority','attested','actor',p_user::text,'at',clock_timestamp()::text) WHERE tenant_id=t;
      RETURN t;
    END; $$
    """)
    for signature in ("create_agency_owner(uuid,text)", "activate_agency(text,uuid,text)"):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO cgl_auth")


def downgrade() -> None:
    op.execute("DROP FUNCTION app.accept_invitation(text,uuid)")
    op.execute("ALTER FUNCTION app.accept_invitation_legacy(text,uuid) RENAME TO accept_invitation")
    op.execute("GRANT EXECUTE ON FUNCTION app.accept_invitation(text,uuid) TO cgl_auth")
    op.execute("DROP TRIGGER initialize_platform ON app.tenant")
    op.execute("DROP FUNCTION app.initialize_agency_platform()")
    op.execute("DROP FUNCTION app.activate_agency(text,uuid,text)")
    op.execute("DROP FUNCTION app.create_agency_owner(uuid,text)")
    op.execute(
        "DELETE FROM app.membership_role WHERE role_id IN (SELECT id FROM app.role WHERE key='billing_admin')"
    )
    op.execute(
        "DELETE FROM app.role_permission WHERE role_id IN (SELECT id FROM app.role WHERE key='billing_admin')"
    )
    op.execute("DELETE FROM app.role WHERE key='billing_admin'")
    op.execute("DELETE FROM app.permission WHERE key IN ('billing.read','billing.manage')")
    for table in (
        "upload_part",
        "upload_session",
        "agency_activation",
        "billing_request",
        "agency_subscription",
        "agency_profile",
    ):
        op.drop_table(table, schema="app")
    for column in ("consumed_at", "return_path", "browser_hash"):
        op.drop_column("oauth_state", column, schema="app")
    for column in ("last_seen_at", "authenticated_at", "context_version"):
        op.drop_column("auth_session", column, schema="app")
    op.drop_column("app_user", "email_verified", schema="app")

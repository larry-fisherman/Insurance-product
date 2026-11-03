"""Durable temporary-upload cleanup intents and upload-scoped preservation holds."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_cleanup"
down_revision = "0008_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_session",
        sa.Column("cleanup_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="app",
    )
    op.add_column(
        "upload_session",
        sa.Column("cleanup_hold_version", sa.Integer(), nullable=False, server_default="1"),
        schema="app",
    )
    op.create_check_constraint(
        "positive_cleanup_hold_version", "upload_session", "cleanup_hold_version > 0", schema="app"
    )
    op.create_table(
        "upload_cleanup",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("objects", JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "completion_summary", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_upload_cleanup_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "upload_id"],
            ["app.upload_session.tenant_id", "app.upload_session.id"],
            name="fk_upload_cleanup_upload_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_upload_cleanup_requested_by",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(objects) = 'array' AND jsonb_array_length(objects) BETWEEN 1 AND 100",
            name="bounded_manifest",
        ),
        schema="app",
    )
    op.execute("ALTER TABLE app.upload_cleanup ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.upload_cleanup FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY maintenance ON app.upload_cleanup TO cgl_migrator USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY tenant_scope ON app.upload_cleanup TO cgl_runtime USING (tenant_id=app.current_tenant_id() AND app.active_member(tenant_id)) WITH CHECK (tenant_id=app.current_tenant_id() AND app.active_member(tenant_id))"
    )
    op.execute("GRANT SELECT,INSERT,UPDATE ON app.upload_cleanup TO cgl_runtime")
    op.execute("""
        CREATE FUNCTION app.guard_upload_cleanup() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
        BEGIN
          IF OLD.completed_at IS NOT NULL OR
             (to_jsonb(NEW) - 'completed_at' - 'completion_summary') IS DISTINCT FROM
             (to_jsonb(OLD) - 'completed_at' - 'completion_summary') THEN
            RAISE EXCEPTION 'Cleanup intent is immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER immutable_upload_cleanup BEFORE UPDATE ON app.upload_cleanup
        FOR EACH ROW EXECUTE FUNCTION app.guard_upload_cleanup();
    """)


def downgrade() -> None:
    op.drop_table("upload_cleanup", schema="app")
    op.execute("DROP FUNCTION app.guard_upload_cleanup()")
    op.drop_constraint(
        "positive_cleanup_hold_version", "upload_session", schema="app", type_="check"
    )
    op.drop_column("upload_session", "cleanup_hold_version", schema="app")
    op.drop_column("upload_session", "cleanup_hold", schema="app")

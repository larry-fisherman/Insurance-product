"""Unverified renewal candidates and concurrent task edits.

Revision ID: 0006_workspace
Revises: 0005_review
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006_workspace"
down_revision: str | None = "0005_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("package", "template_version", server_default="original-cgl-2", schema="app")
    op.add_column(
        "submission",
        sa.Column(
            "renewal_candidates", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        schema="app",
    )
    op.add_column(
        "work_item",
        sa.Column("row_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        schema="app",
    )
    op.add_column("work_item", sa.Column("resolution_reason", sa.Text()), schema="app")
    op.execute("""
        CREATE FUNCTION app.preserve_renewal_lineage() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$ BEGIN
          IF (OLD.insured_id,OLD.prior_submission_id,OLD.renewal_candidates)
             IS DISTINCT FROM (NEW.insured_id,NEW.prior_submission_id,NEW.renewal_candidates)
          THEN RAISE EXCEPTION 'Renewal lineage is immutable' USING ERRCODE='55000'; END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app.preserve_renewal_lineage() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER preserve_renewal_lineage BEFORE UPDATE ON app.submission FOR EACH ROW EXECUTE FUNCTION app.preserve_renewal_lineage()"
    )


def downgrade() -> None:
    op.alter_column("package", "template_version", server_default="original-cgl-1", schema="app")
    op.execute("DROP TRIGGER preserve_renewal_lineage ON app.submission")
    op.execute("DROP FUNCTION app.preserve_renewal_lineage()")
    op.drop_column("work_item", "resolution_reason", schema="app")
    op.drop_column("work_item", "row_version", schema="app")
    op.drop_column("submission", "renewal_candidates", schema="app")

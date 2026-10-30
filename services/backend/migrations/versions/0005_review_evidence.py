"""Preserve document dispositions, availability, and corrected-fact support.

Revision ID: 0005_review
Revises: 0004_seals
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_review"
down_revision: str | None = "0004_seals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_version",
        sa.Column("excluded_by", sa.Uuid(), sa.ForeignKey("app.app_user.id")),
        schema="app",
    )
    op.add_column(
        "document_version", sa.Column("excluded_at", sa.DateTime(timezone=True)), schema="app"
    )
    op.add_column(
        "document_version",
        sa.Column(
            "processing_inventory", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        schema="app",
    )
    op.add_column(
        "observation",
        sa.Column("availability", sa.Text(), nullable=False, server_default="available"),
        schema="app",
    )
    op.add_column(
        "observation",
        sa.Column("support_status", sa.Text(), nullable=False, server_default="legacy_unvalidated"),
        schema="app",
    )
    op.alter_column("observation", "support_status", server_default="supported", schema="app")
    op.add_column(
        "review_decision",
        sa.Column("support", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        schema="app",
    )
    # Do not let packages approved under the old evidence rules remain current.
    # Frozen bytes/history stay intact and are available only as acknowledged history.
    op.execute("""
        WITH affected AS (
          UPDATE app.submission s
          SET input_version=s.input_version+1, row_version=s.row_version+1,
              status=CASE WHEN s.status IN ('closed','cancelled') THEN s.status ELSE 'in_review' END
          WHERE EXISTS (SELECT 1 FROM app.document_version d WHERE d.submission_id=s.id)
             OR EXISTS (SELECT 1 FROM app.review_decision r WHERE r.submission_id=s.id)
          RETURNING s.id,s.tenant_id,s.input_version
        )
        INSERT INTO app.audit_event(id,tenant_id,actor_type,action,object_type,object_id,request_id,change_summary)
        SELECT gen_random_uuid(),tenant_id,'system','review.integrity_upgrade_required','submission',id,gen_random_uuid(),
            jsonb_build_object('migration','0005_review','input_version',input_version,'reason','Evidence and omission controls require fresh review')
        FROM affected
    """)


def downgrade() -> None:
    op.drop_column("review_decision", "support", schema="app")
    op.drop_column("observation", "support_status", schema="app")
    op.drop_column("observation", "availability", schema="app")
    op.drop_column("document_version", "processing_inventory", schema="app")
    op.drop_column("document_version", "excluded_at", schema="app")
    op.drop_column("document_version", "excluded_by", schema="app")

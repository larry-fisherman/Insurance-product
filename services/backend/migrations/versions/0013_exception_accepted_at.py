"""Record when a missing-information exception was accepted.

``discrepancy`` had no timestamp other than ``created_at``, which is when the row was first
written, not when (or which time) an exception was accepted. Package disclosures now render the
acceptance time, so it is recorded explicitly. Existing rows keep ``NULL``: no time is inferred
for historical acceptances, and historical snapshots, packages and approvals are not rewritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_exception_accepted_at"
down_revision = "0012_line_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discrepancy",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("discrepancy", "accepted_at", schema="app")

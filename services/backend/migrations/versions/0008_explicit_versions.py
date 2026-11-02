"""Require explicit version metadata for new revisions and findings.

Historical rows, snapshots, hashes and approvals are deliberately untouched.
"""

from alembic import op

revision = "0008_versions"
down_revision = "0007_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("canonical_revision", "schema_version", schema="app", server_default=None)
    op.alter_column("discrepancy", "rule_version", schema="app", server_default=None)


def downgrade() -> None:
    op.alter_column("canonical_revision", "schema_version", schema="app", server_default="cgl-1")
    op.alter_column("discrepancy", "rule_version", schema="app", server_default="cgl-1")

"""Fail closed on unsupported insurance lines.

``submission.line`` has defaulted to ``'cgl'`` since 0002 but was never constrained, so any
other value would silently receive CGL rules. Only CGL is implemented; the constraint rejects
every other line. Existing rows are untouched: they all carry the ``'cgl'`` default. Adding a
line later needs its own schema, rules and requirements, and a migration widening this check.
"""

from alembic import op

revision = "0012_line_guard"
down_revision = "0011_storage_narrow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_submission_line"), "submission", "line IN ('cgl')", schema="app"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_submission_line"), "submission", schema="app", type_="check")

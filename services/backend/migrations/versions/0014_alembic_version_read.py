"""Let the identity role read the applied schema revision for readiness.

``/api/v1/health/ready`` compares ``public.alembic_version`` with the code's expected head
over the identity (``cgl_auth``) pool, which the readiness check already uses for its
``SELECT 1``. Requests never use migration or admin credentials, so the identity role needs
exactly ``SELECT`` on this one table. No other privilege changes; the runtime role is not
granted access.
"""

from alembic import op

revision = "0014_alembic_version_read"
down_revision = "0013_exception_accepted_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON public.alembic_version TO cgl_auth")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.alembic_version FROM cgl_auth")

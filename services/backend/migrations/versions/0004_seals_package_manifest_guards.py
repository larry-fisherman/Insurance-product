"""Seal package content and extend document metadata immutability.

Revision ID: 0004_seals
Revises: 0003_requests
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_seals"
down_revision: str | None = "0003_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION app.preserve_package_seal() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$ BEGIN
          IF OLD.manifest_hash IS NOT NULL AND
             (OLD.manifest,OLD.manifest_hash,OLD.revision_id,OLD.input_version,OLD.template_version,OLD.submission_id,OLD.tenant_id)
             IS DISTINCT FROM
             (NEW.manifest,NEW.manifest_hash,NEW.revision_id,NEW.input_version,NEW.template_version,NEW.submission_id,NEW.tenant_id)
          THEN RAISE EXCEPTION 'Rendered package content is sealed' USING ERRCODE='55000'; END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app.preserve_package_seal() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER preserve_package_seal BEFORE UPDATE ON app.package FOR EACH ROW EXECUTE FUNCTION app.preserve_package_seal()"
    )
    op.execute("""
        CREATE FUNCTION app.preserve_document_metadata() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$ BEGIN
          IF (OLD.filename,OLD.media_type,OLD.fixture_signature,OLD.uploaded_by,OLD.supersedes_id)
              IS DISTINCT FROM
             (NEW.filename,NEW.media_type,NEW.fixture_signature,NEW.uploaded_by,NEW.supersedes_id)
          THEN RAISE EXCEPTION 'Original document metadata is immutable' USING ERRCODE='55000'; END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app.preserve_document_metadata() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER preserve_document_metadata BEFORE UPDATE ON app.document_version FOR EACH ROW EXECUTE FUNCTION app.preserve_document_metadata()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER preserve_document_metadata ON app.document_version")
    op.execute("DROP FUNCTION app.preserve_document_metadata()")
    op.execute("DROP TRIGGER preserve_package_seal ON app.package")
    op.execute("DROP FUNCTION app.preserve_package_seal()")

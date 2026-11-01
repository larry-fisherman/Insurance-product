from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine

from app.database.models import Base

pytestmark = pytest.mark.database


def test_upgrade_is_repeatable_and_matches_metadata(
    database_engine: Engine, migration_config: Callable[[Connection], Config]
) -> None:
    with database_engine.connect() as connection:
        config = migration_config(connection)
        command.upgrade(config, "head")
        command.check(config)
        assert set(sa.inspect(connection).get_table_names(schema="app")) == {
            table.name for table in Base.metadata.tables.values()
        }
        assert (
            connection.execute(sa.text("SELECT count(*) FROM public.alembic_version")).scalar_one()
            == 1
        )


def test_downgrade_and_reupgrade_only_on_disposable_database(
    database_factory: Callable[[], AbstractContextManager[Engine]],
    migration_config: Callable[[Connection], Config],
) -> None:
    with database_factory() as engine, engine.connect() as connection:
        assert connection.engine.url.database is not None
        assert connection.engine.url.database.startswith("cgl_test_")
        config = migration_config(connection)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        assert "app" not in sa.inspect(connection).get_schema_names()
        assert "alembic_version" in sa.inspect(connection).get_table_names(schema="public")
        connection.commit()
        command.upgrade(config, "head")
        command.check(config)
        assert connection.execute(sa.text("SELECT count(*) FROM app.role")).scalar_one() == 5


def test_migration_owner_and_runtime_role_are_unprivileged(connection: Connection) -> None:
    rows = (
        connection.execute(
            sa.text("""
        SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolcanlogin
        FROM pg_roles WHERE rolname IN ('cgl_migrator', 'cgl_runtime')
    """)
        )
        .mappings()
        .all()
    )
    assert len(rows) == 2
    for row in rows:
        assert not any(
            row[key] for key in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls")
        )
        assert row["rolcanlogin"]
    owners = (
        connection.execute(
            sa.text("""
        SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND c.relkind = 'r'
    """)
        )
        .scalars()
        .all()
    )
    assert owners == ["cgl_migrator"]
    assert connection.execute(
        sa.text("SELECT has_schema_privilege('cgl_runtime', 'app', 'USAGE')")
    ).scalar_one()


def test_fixed_roles_do_not_confer_implicit_approval_or_support_access(
    connection: Connection,
) -> None:
    rows = (
        connection.execute(
            sa.text("""
        SELECT r.key AS role, p.key AS permission FROM app.role r
        JOIN app.role_permission rp ON rp.role_id = r.id
        JOIN app.permission p ON p.id = rp.permission_id
    """)
        )
        .mappings()
        .all()
    )
    bundles: dict[str, set[str]] = {}
    for row in rows:
        bundles.setdefault(row["role"], set()).add(row["permission"])
    assert set(bundles) == {"viewer", "preparer", "approver", "tenant_admin", "billing_admin"}
    assert "packages.approve" in bundles["approver"]
    assert "exceptions.accept" in bundles["approver"]
    for role in ("viewer", "preparer", "tenant_admin"):
        assert "packages.approve" not in bundles[role]
        assert "exceptions.accept" not in bundles[role]
    assert "members.manage" in bundles["tenant_admin"]
    assert "documents.read" not in bundles["tenant_admin"]


def test_evidence_upgrade_invalidates_legacy_current_approvals_without_rewriting_history(
    database_factory: Callable[[], AbstractContextManager[Engine]],
    migration_config: Callable[[Connection], Config],
) -> None:
    from datetime import date
    from uuid import uuid4

    from app.database.models import AppUser, Membership, Tenant
    from app.domain.models import (
        DocumentVersion,
        InsuredAccount,
        Observation,
        ReviewDecision,
        SourceBlock,
        Submission,
    )

    (
        tenant_id,
        user_id,
        membership_id,
        insured_id,
        submission_id,
        document_id,
        source_id,
        observation_id,
        decision_id,
    ) = (uuid4() for _ in range(9))
    with database_factory() as engine, engine.connect() as connection:
        config = migration_config(connection)
        command.upgrade(config, "0004_seals")
        connection.execute(sa.insert(Tenant).values(id=tenant_id, name="Fictional upgrade agency"))
        connection.execute(
            sa.insert(AppUser).values(
                id=user_id,
                identity_issuer="synthetic-upgrade",
                identity_subject=str(user_id),
                display_name="Fictional upgrade broker",
            )
        )
        connection.execute(
            sa.insert(Membership).values(id=membership_id, tenant_id=tenant_id, user_id=user_id)
        )
        connection.execute(
            sa.insert(InsuredAccount).values(
                id=insured_id, tenant_id=tenant_id, name="Fictional upgrade insured"
            )
        )
        connection.execute(
            sa.insert(Submission).values(
                id=submission_id,
                tenant_id=tenant_id,
                insured_id=insured_id,
                assigned_membership_id=membership_id,
                title="Legacy synthetic placement",
                intended_start=date(2027, 1, 1),
                intended_end=date(2028, 1, 1),
                status="approved",
            )
        )
        connection.execute(
            sa.insert(DocumentVersion).values(
                id=document_id,
                tenant_id=tenant_id,
                submission_id=submission_id,
                filename="legacy.pdf",
                media_type="application/pdf",
                sha256="a" * 64,
                byte_size=10,
                object_key="legacy/test",
                uploaded_by=user_id,
            )
        )
        connection.execute(
            sa.insert(SourceBlock).values(
                id=source_id,
                tenant_id=tenant_id,
                document_id=document_id,
                locator={"kind": "pdf", "page": 1},
                text="Each occurrence limit: USD 1000000",
                parser_version="pdf-xlsx-1",
            )
        )
        connection.execute(
            sa.insert(Observation).values(
                id=observation_id,
                tenant_id=tenant_id,
                submission_id=submission_id,
                document_id=document_id,
                source_id=source_id,
                field="coverage.each_occurrence",
                group_key="legacy-group",
                value="1000000",
                context={"currency": "USD", "role": "existing"},
                raw_value="1000000",
                method="local-label-parser",
                pipeline_version="cgl-extraction-1",
            )
        )
        connection.execute(
            sa.insert(ReviewDecision).values(
                id=decision_id,
                tenant_id=tenant_id,
                submission_id=submission_id,
                sequence=1,
                group_key="legacy-group",
                input_fingerprint="old",
                action="correct",
                field="coverage.each_occurrence",
                value="2000000",
                context={"currency": "USD", "role": "existing"},
                observation_id=observation_id,
                reason="Legacy correction with inherited provenance",
                reviewer_id=user_id,
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        row = connection.execute(
            sa.select(Submission.input_version, Submission.row_version, Submission.status).where(
                Submission.id == submission_id
            )
        ).one()
        assert row == (2, 2, "in_review")
        assert (
            connection.scalar(
                sa.select(Observation.support_status).where(Observation.id == observation_id)
            )
            == "legacy_unvalidated"
        )
        assert (
            connection.scalar(
                sa.select(ReviewDecision.value).where(ReviewDecision.id == decision_id)
            )
            == "2000000"
        )
        assert (
            connection.scalar(sa.select(SourceBlock.text).where(SourceBlock.id == source_id))
            == "Each occurrence limit: USD 1000000"
        )
        assert (
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM app.audit_event WHERE action='review.integrity_upgrade_required'"
                )
            )
            == 1
        )

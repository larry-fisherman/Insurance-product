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
        assert connection.execute(sa.text("SELECT count(*) FROM app.role")).scalar_one() == 4


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
    assert set(bundles) == {"viewer", "preparer", "approver", "tenant_admin"}
    assert "packages.approve" in bundles["approver"]
    assert "exceptions.accept" in bundles["approver"]
    for role in ("viewer", "preparer", "tenant_admin"):
        assert "packages.approve" not in bundles[role]
        assert "exceptions.accept" not in bundles[role]
    assert "members.manage" in bundles["tenant_admin"]
    assert "documents.read" not in bundles["tenant_admin"]

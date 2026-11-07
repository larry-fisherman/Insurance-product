"""Catalog-wide tenancy invariants: every app table is tenant-scoped or explicitly global."""

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection

pytestmark = pytest.mark.database

# Tables deliberately outside tenant row scope. Each entry needs a reason; a new
# table must either carry forced RLS with a cgl_runtime tenant_scope policy or be
# added here after review.
GLOBAL_TABLES = {
    # Identity: users, sessions and credentials exist before any agency is selected
    # and are reached only through the cgl_auth identity path.
    "app_user",
    "auth_session",
    "credential",
    "recovery_code",
    "oauth_state",
    # Fixed role/permission catalog shared by every agency.
    "role",
    "permission",
    "role_permission",
    # Process-wide storage budget and capacity bookkeeping (0010/0011).
    "storage_limit",
    "storage_global_usage",
    "storage_legacy_part",
}

# Tenant-bearing tables the runtime role cannot touch at all: forced RLS, no
# cgl_runtime policy and no cgl_runtime table grant. Access goes only through the
# named SECURITY DEFINER function. Not global: rows carry tenancy, runtime is denied.
RUNTIME_DENIED = {
    # Operator-issued activation; consumed only by app.activate_agency(text,uuid,text)
    # (migrations/versions/0007_platform.py).
    "agency_activation",
}

# The runtime role may delete only role assignments (replaced as a set on edit).
RUNTIME_DELETABLE = {"membership_role"}


def app_tables(connection: Connection) -> list[sa.RowMapping]:
    return list(
        connection.execute(
            sa.text("""
        SELECT c.relname AS name, c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
    """)
        ).mappings()
    )


def test_every_app_table_is_forced_tenant_scoped_or_explicitly_global(
    connection: Connection,
) -> None:
    tables = app_tables(connection)
    names = {row["name"] for row in tables}
    assert names, "no app tables found"
    # A stale allowlist entry would silently exempt a future table of that name.
    assert names >= GLOBAL_TABLES, sorted(GLOBAL_TABLES - names)
    assert names >= RUNTIME_DENIED, sorted(RUNTIME_DENIED - names)
    assert not GLOBAL_TABLES & RUNTIME_DENIED
    scoped = set(
        connection.execute(
            sa.text("""
        SELECT c.relname FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND p.polname = 'tenant_scope'
          AND (SELECT oid FROM pg_roles WHERE rolname = 'cgl_runtime') = ANY (p.polroles)
    """)
        ).scalars()
    )
    violations = sorted(
        row["name"]
        for row in tables
        if row["name"] not in GLOBAL_TABLES | RUNTIME_DENIED
        and not (row["enabled"] and row["forced"] and row["name"] in scoped)
    )
    assert violations == [], (
        "tables without forced RLS and a cgl_runtime tenant_scope policy: " + ", ".join(violations)
    )


def test_runtime_role_cannot_delete_outside_role_assignments(connection: Connection) -> None:
    deletable = set(
        connection.execute(
            sa.text("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
          AND has_table_privilege('cgl_runtime', c.oid, 'DELETE')
    """)
        ).scalars()
    )
    assert deletable == RUNTIME_DELETABLE


def test_runtime_denied_tables_have_forced_rls_and_no_runtime_access(
    connection: Connection,
) -> None:
    tables = {row["name"]: row for row in app_tables(connection)}
    for name in sorted(RUNTIME_DENIED):
        assert tables[name]["enabled"] and tables[name]["forced"], name
        runtime_policies = connection.execute(
            sa.text("""
            SELECT p.polname FROM pg_policy p
            JOIN pg_class c ON c.oid = p.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'app' AND c.relname = :name
              AND ((SELECT oid FROM pg_roles WHERE rolname = 'cgl_runtime') = ANY (p.polroles)
                   OR 0 = ANY (p.polroles))
        """),
            {"name": name},
        ).scalars()
        assert list(runtime_policies) == [], name
        granted = {
            privilege
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            if connection.execute(
                sa.text("SELECT has_table_privilege('cgl_runtime', :table, :privilege)"),
                {"table": "app." + name, "privilege": privilege},
            ).scalar_one()
        }
        assert granted == set(), (name, granted)


def test_identity_role_reads_only_the_schema_revision_in_public(connection: Connection) -> None:
    # Readiness compares the applied head over the cgl_auth pool (0014); nothing else in
    # public is reachable by either request role.
    rows = connection.execute(
        sa.text("""
        SELECT c.relname, r.rolname,
               has_table_privilege(r.rolname, c.oid, 'SELECT') AS can_select,
               has_table_privilege(r.rolname, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')
                 AS can_write
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN (VALUES ('cgl_auth'), ('cgl_runtime')) AS r(rolname)
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    """)
    ).all()
    readable = {(row.relname, row.rolname) for row in rows if row.can_select}
    assert readable == {("alembic_version", "cgl_auth")}
    assert not any(row.can_write for row in rows)

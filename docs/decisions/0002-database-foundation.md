# ADR 0002: PostgreSQL schema, migration ownership, and ledgers

Date: 2026-10-29. Status: accepted for P1-02.

## Schema and versioning

Use SQLAlchemy 2.1, Alembic, and the psycopg driver against PostgreSQL 18. Direct versions and transitive dependencies are locked. Tables live in the dedicated `app` schema; Alembic history stays in `public.alembic_version`.

The initial revision explicitly creates eleven tables: tenant, app_user, membership, role, permission, role_permission, membership_role, invitation, auth_session, audit_event, and outbox_event. It freezes its own DDL and seed data rather than importing mutable ORM models. Future schema changes require new revisions.

Autogeneration compares only `app`, never public history or unrelated schemas. Tests check metadata drift as well as upgrade, repeated upgrade, downgrade, and re-upgrade. Downgrade is destructive and is exercised only on newly created disposable databases.

IDs are application-generated UUIDs. Timestamps are timezone-aware with database defaults. Mutable tenant/membership records have positive version counters for future optimistic updates. Constraints validate statuses, token-hash shape, expiry/acceptance state, and JSON object payloads. They do not replace future identity verification or authorization.

## Ownership and relationships

Users, sessions, roles, and permission catalogs are global. Tenant-owned records reference the tenant and use composite tenant/member foreign keys whenever they reference an agency membership. This prevents role grantors, invitation inviters/acceptors, and audit actors from linking across tenants.

History uses restrictive foreign keys rather than cascading deletion. Disabling users/memberships preserves attribution. Erasure, redaction, and retention exceptions require separately designed workflows.

Four fixed agency roles are seeded: viewer, preparer, approver, and tenant_admin. Approval and acceptance of material exceptions belong to the approver bundle. Administration does not imply document access or approval. Platform operators cannot be granted access through these agency role bundles; their future access workflow is separate.

Database roles are provisioned outside the schema migration by the local initialization script:

- `cgl_admin`: local superuser for first initialization and creation/deletion of isolated test databases.
- `cgl_migrator`: database/schema owner; no superuser, CREATEROLE, CREATEDB, or BYPASSRLS.
- `cgl_runtime`: non-owner, nonprivileged, NOLOGIN, with no app-schema grant yet.

The API receives none of these credentials in P1-02. Application sessions and row-level authorization are P1-03/P1-04 work. Composite foreign keys protect relationships; they do not restrict which rows a privileged database owner can read.

## Audit and outbox

Audit events record tenant, attributable member/system/platform actor, action, object, request ID, insertion time, and a minimal JSON summary. A PostgreSQL statement trigger rejects UPDATE, DELETE, and TRUNCATE. No application role has grants yet. Schema owners/superusers can still change DDL; this is append-only application history, not tamper-proof archival storage. Archive, access control, and retention handling remain planned work.

Outbox events record aggregate identity, event type/version, minimal metadata, tenant-scoped optional deduplication key, availability/publication timestamps, and attempt count. The pending index supports future tenant-scoped dispatch. Audit/outbox writes can participate in the same database transaction as a business write. No publisher, Redis delivery, or background processing is implemented here.

## Development and testing

A separate `cgl-database` Compose project avoids restarting the web stack. A standard-library helper creates private random local credentials with exclusive creation and does not overwrite existing files. PostgreSQL 18 stores its versioned data under a named volume mounted at `/var/lib/postgresql`. Initialization is a first-volume action; changing an environment file does not rotate existing database users.

Migrations are explicit CLI/one-off container operations, never API startup side effects. The migration container receives only the migration credential. The local administrator credential is reserved for bootstrap/tests.

Integration tests require the generated loopback `cgl_dev` configuration. A fixture creates unique `cgl_test_*` databases owned by the migrator and removes only databases it successfully created. It does not downgrade, truncate, or delete developer data. A dedicated CI job runs the real PostgreSQL suite so unit tests cannot silently substitute SQLite or skip isolation prerequisites.

Official references:

- [PostgreSQL constraints](https://www.postgresql.org/docs/current/ddl-constraints.html): composite keys, nullability, and referential integrity.
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html): explicit migration environments and revision lifecycle.
- [Official PostgreSQL container documentation](https://github.com/docker-library/docs/blob/master/postgres/README.md): initialization and PostgreSQL 18 volume layout.

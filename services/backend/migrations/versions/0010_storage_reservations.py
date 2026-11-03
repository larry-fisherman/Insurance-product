"""Storage reservation ledger, reservation-backed orphan cleanup intents and sweep reports."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_storage"
down_revision = "0009_cleanup"
branch_labels = None
depends_on = None

TENANT_POLICY = "tenant_id=app.current_tenant_id() AND app.active_member(tenant_id)"  # noqa: S105
RUNTIME_FUNCTIONS = (
    "reserve_storage(uuid,text,uuid,text,text,bigint,text,text,integer,integer,uuid)",
    "reconcile_storage(bigint,bigint)",
    "release_upload_cleanup(uuid,jsonb)",
    "release_object_cleanup(uuid,jsonb)",
)
INTERNAL_FUNCTIONS = ("release_storage(uuid,uuid)", "storage_ready()")


def protect(table: str, *, runtime: str | None) -> None:
    op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY maintenance ON app.{table} TO cgl_migrator USING (true) WITH CHECK (true)"
    )
    if runtime:
        op.execute(
            f"CREATE POLICY tenant_scope ON app.{table} TO cgl_runtime "
            f"USING ({TENANT_POLICY}) WITH CHECK ({TENANT_POLICY})"
        )
        op.execute(f"GRANT {runtime} ON app.{table} TO cgl_runtime")


def upgrade() -> None:
    op.create_table(
        "storage_limit",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("tenant_byte_limit", sa.BigInteger(), nullable=False),
        sa.Column("total_byte_limit", sa.BigInteger(), nullable=False),
        sa.Column("entry_limit", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint(
            "tenant_byte_limit > 0 AND total_byte_limit > 0 AND entry_limit > 0",
            name="positive_limits",
        ),
        schema="app",
    )
    op.execute(
        "INSERT INTO app.storage_limit(id,tenant_byte_limit,total_byte_limit,entry_limit) "
        "VALUES (1, 2147483648, 21474836480, 100000)"
    )
    op.create_table(
        "storage_global_usage",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("charged_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("entries", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("charged_bytes >= 0 AND entries >= 0", name="non_negative"),
        schema="app",
    )
    op.execute("INSERT INTO app.storage_global_usage(id) VALUES (1)")
    op.create_table(
        "storage_usage",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id"), primary_key=True),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("committed_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("entries", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("initialized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "reserved_bytes >= 0 AND committed_bytes >= 0 AND entries >= 0", name="non_negative"
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        schema="app",
    )
    # Existing tenants fail closed until the operator seed/reconcile command runs.
    op.execute("INSERT INTO app.storage_usage(tenant_id) SELECT id FROM app.tenant")
    # No foreign keys to tenant/membership: an FK check would take a key-share lock on
    # the tenant row, which the caller's own business transaction already holds FOR
    # UPDATE (require_capacity). The separate reservation transaction must never wait
    # on business rows. The definer derives the tenant and validates created_by.
    op.create_table(
        "storage_reservation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="reserved"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("linked_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_ref", sa.Uuid()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_reservation_tenant_id"),
        sa.UniqueConstraint("object_key", name="uq_storage_reservation_object_key"),
        sa.CheckConstraint(
            "purpose IN ('upload_part','document','artifact')", name="valid_purpose"
        ),
        sa.CheckConstraint(
            "state IN ('reserved','committed','cleanup_planned','released')", name="valid_state"
        ),
        sa.CheckConstraint("byte_size >= 0", name="non_negative_size"),
        sa.CheckConstraint("length(slot) BETWEEN 1 AND 100", name="bounded_slot"),
        schema="app",
    )
    op.create_index(
        "ix_storage_reservation_slot",
        "storage_reservation",
        ["tenant_id", "purpose", "owner_id", "slot"],
        schema="app",
    )
    op.create_index(
        "ix_storage_reservation_state",
        "storage_reservation",
        ["tenant_id", "state", "expires_at"],
        schema="app",
    )
    op.create_table(
        "object_cleanup",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("objects", JSONB(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "completion_summary", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_object_cleanup_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["app.membership.tenant_id", "app.membership.id"],
            name="fk_object_cleanup_requested_by",
        ),
        sa.CheckConstraint("category IN ('reservation_orphan')", name="valid_category"),
        sa.CheckConstraint(
            "jsonb_typeof(objects) = 'array' AND jsonb_array_length(objects) BETWEEN 1 AND 100",
            name="bounded_manifest",
        ),
        schema="app",
    )
    op.create_table(
        "storage_sweep",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("origin", sa.Text(), nullable=False, server_default="scheduler"),
        sa.Column("reported_at", sa.DateTime(timezone=True)),
        sa.Column("report_attempt", sa.Integer()),
        sa.Column("orphan_candidates", sa.BigInteger()),
        sa.Column("expired_absent", sa.BigInteger()),
        sa.Column("legacy_unreserved", sa.BigInteger()),
        sa.Column("legacy_unreserved_bytes", sa.BigInteger()),
        sa.Column("anomalies", sa.BigInteger()),
        sa.Column("drift_bytes", sa.BigInteger()),
        sa.Column("drift_entries", sa.BigInteger()),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_sweep_tenant_id"),
        sa.UniqueConstraint("tenant_id", name="uq_storage_sweep_single_tenant"),
        sa.CheckConstraint("attempt >= 0", name="non_negative_attempt"),
        sa.CheckConstraint("origin IN ('scheduler','operator')", name="valid_origin"),
        schema="app",
    )
    protect("storage_limit", runtime=None)
    protect("storage_global_usage", runtime=None)
    protect("storage_usage", runtime="SELECT")
    # Reservations are inserted and released only by definer functions; runtime
    # may only link (reserved -> committed) or plan (reserved -> cleanup_planned).
    protect("storage_reservation", runtime="SELECT")
    op.execute("GRANT UPDATE (state, linked_at) ON app.storage_reservation TO cgl_runtime")
    protect("object_cleanup", runtime="SELECT,INSERT")
    protect("storage_sweep", runtime="SELECT,INSERT,UPDATE")
    op.execute("""
        CREATE TRIGGER immutable_object_cleanup BEFORE UPDATE ON app.object_cleanup
        FOR EACH ROW EXECUTE FUNCTION app.guard_upload_cleanup();
    """)
    op.execute("""
        CREATE FUNCTION app.guard_storage_reservation() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
        DECLARE mutable text[] := ARRAY['state','expires_at','linked_at','released_at','release_ref'];
        BEGIN
          IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
            RAISE EXCEPTION 'Storage reservation identity is immutable';
          END IF;
          IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
               (OLD.state = 'reserved' AND NEW.state IN ('committed','cleanup_planned','released'))
            OR (OLD.state IN ('committed','cleanup_planned') AND NEW.state = 'released')) THEN
            RAISE EXCEPTION 'Storage reservation state may only move forward';
          END IF;
          IF NEW.state = 'committed' AND OLD.state = 'reserved'
             AND OLD.expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'Storage reservation expired';
          END IF;
          IF (NEW.state = 'released' OR NEW.released_at IS DISTINCT FROM OLD.released_at
              OR NEW.release_ref IS DISTINCT FROM OLD.release_ref)
             AND (SELECT relowner FROM pg_catalog.pg_class WHERE oid = TG_RELID)
                 <> (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
            RAISE EXCEPTION 'Storage release requires a verified cleanup completion';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER guard_storage_reservation BEFORE UPDATE ON app.storage_reservation
        FOR EACH ROW EXECUTE FUNCTION app.guard_storage_reservation();
    """)
    op.execute("""
        CREATE FUNCTION app.storage_ready() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,app AS $$
          SELECT NOT EXISTS (
            SELECT 1 FROM app.storage_usage s JOIN app.tenant a ON a.id = s.tenant_id
            WHERE NOT s.initialized AND a.status = 'active' AND EXISTS (
              SELECT 1 FROM app.membership m JOIN app.app_user u ON u.id = m.user_id
              WHERE m.tenant_id = a.id AND m.status = 'active' AND u.status = 'active'))
        $$
    """)
    op.execute("""
        CREATE FUNCTION app.reserve_storage(
          p_id uuid, p_purpose text, p_owner uuid, p_slot text, p_key text, p_bytes bigint,
          p_sha256 text, p_request_hash text, p_ttl_seconds integer, p_margin_seconds integer,
          p_created_by uuid)
        RETURNS TABLE(outcome text, reserved_id uuid, reserved_key text, reserved_until timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          category text;
          lim app.storage_limit%ROWTYPE;
          counter app.storage_usage%ROWTYPE;
          existing app.storage_reservation%ROWTYPE;
          created app.storage_reservation%ROWTYPE;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          category := CASE p_purpose WHEN 'upload_part' THEN 'uploads'
            WHEN 'document' THEN 'documents' WHEN 'artifact' THEN 'artifacts' END;
          IF category IS NULL OR p_bytes IS NULL OR p_bytes < 0 OR p_owner IS NULL
             OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 60 AND 86400
             OR p_margin_seconds IS NULL OR p_margin_seconds NOT BETWEEN 0 AND p_ttl_seconds / 2
             OR p_key !~ ('^tenants/' || t::text || '/' || category || '/[0-9a-f-]{36}/[a-zA-Z0-9._-]+$')
             OR p_sha256 !~ '^[0-9a-f]{64}$' OR p_request_hash !~ '^[0-9a-f]{64}$'
             OR length(p_slot) NOT BETWEEN 1 AND 100
             OR (p_created_by IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM app.membership m WHERE m.tenant_id = t AND m.id = p_created_by))
          THEN
            RAISE EXCEPTION 'Invalid storage reservation';
          END IF;
          -- Lock order: tenant counter, then global counter. Reservations are read only.
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t FOR UPDATE;
          IF NOT FOUND OR NOT counter.initialized OR NOT app.storage_ready() THEN
            RETURN QUERY SELECT 'unavailable'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          SELECT * INTO existing FROM app.storage_reservation r
            WHERE r.tenant_id = t AND r.purpose = p_purpose AND r.owner_id = p_owner
              AND r.slot = p_slot AND r.state = 'reserved'
              AND r.expires_at > clock_timestamp() + make_interval(secs => p_margin_seconds)
            ORDER BY r.created_at DESC LIMIT 1;
          IF FOUND THEN
            IF existing.request_hash = p_request_hash THEN
              RETURN QUERY SELECT 'reused'::text, existing.id, existing.object_key, existing.expires_at;
            ELSE
              RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            END IF;
            RETURN;
          END IF;
          SELECT * INTO lim FROM app.storage_limit l WHERE l.id = 1;
          IF counter.reserved_bytes + counter.committed_bytes + p_bytes > lim.tenant_byte_limit THEN
            RETURN QUERY SELECT 'capacity'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          UPDATE app.storage_global_usage g
            SET charged_bytes = g.charged_bytes + p_bytes, entries = g.entries + 1,
                version = g.version + 1
            WHERE g.id = 1 AND g.charged_bytes + p_bytes <= lim.total_byte_limit
              AND g.entries + 1 <= lim.entry_limit;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'capacity'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          UPDATE app.storage_usage s
            SET reserved_bytes = s.reserved_bytes + p_bytes, entries = s.entries + 1,
                version = s.version + 1, updated_at = clock_timestamp()
            WHERE s.tenant_id = t
              AND s.reserved_bytes + s.committed_bytes + p_bytes <= lim.tenant_byte_limit;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Storage counter changed under lock';
          END IF;
          INSERT INTO app.storage_reservation(
              id, tenant_id, purpose, owner_id, slot, object_key, byte_size, sha256,
              request_hash, state, expires_at, created_by)
            VALUES (p_id, t, p_purpose, p_owner, p_slot, p_key, p_bytes, p_sha256,
              p_request_hash, 'reserved',
              clock_timestamp() + make_interval(secs => p_ttl_seconds), p_created_by)
            RETURNING * INTO created;
          RETURN QUERY SELECT 'reserved'::text, created.id, created.object_key, created.expires_at;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION app.release_storage(p_reservation uuid, p_ref uuid) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          item app.storage_reservation%ROWTYPE;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          SELECT * INTO item FROM app.storage_reservation r
            WHERE r.id = p_reservation AND r.tenant_id = t FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Storage reservation unavailable';
          END IF;
          IF item.state = 'released' THEN
            RETURN 0;
          END IF;
          IF item.state = 'committed' AND item.purpose <> 'upload_part' THEN
            RAISE EXCEPTION 'Retained objects cannot be released';
          END IF;
          IF EXISTS (SELECT 1 FROM app.document_version d
                     WHERE d.tenant_id = t AND d.object_key = item.object_key)
             OR EXISTS (SELECT 1 FROM app.package_artifact a
                        WHERE a.tenant_id = t AND a.object_key = item.object_key) THEN
            RAISE EXCEPTION 'Retained objects cannot be released';
          END IF;
          UPDATE app.storage_usage s
            SET reserved_bytes = s.reserved_bytes - item.byte_size, entries = s.entries - 1,
                version = s.version + 1, updated_at = clock_timestamp()
            WHERE s.tenant_id = t;
          UPDATE app.storage_global_usage g
            SET charged_bytes = g.charged_bytes - item.byte_size, entries = g.entries - 1,
                version = g.version + 1
            WHERE g.id = 1;
          UPDATE app.storage_reservation r
            SET state = 'released', released_at = clock_timestamp(), release_ref = p_ref
            WHERE r.id = item.id;
          RETURN item.byte_size;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION app.release_upload_cleanup(p_cleanup uuid, p_summary jsonb)
        RETURNS TABLE(released_bytes bigint, legacy_bytes bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          intent app.upload_cleanup%ROWTYPE;
          counter app.storage_usage%ROWTYPE;
          planned jsonb;
          reservation uuid;
          released bigint := 0;
          legacy bigint := 0;
          legacy_count bigint := 0;
          outstanding bigint;
          lower_bytes bigint := 0;
          lower_entries bigint := 0;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          IF jsonb_typeof(p_summary) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Invalid cleanup summary';
          END IF;
          SELECT * INTO intent FROM app.upload_cleanup c
            WHERE c.id = p_cleanup AND c.tenant_id = t FOR UPDATE;
          IF NOT FOUND OR intent.completed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Cleanup intent unavailable';
          END IF;
          FOR planned IN SELECT value FROM jsonb_array_elements(intent.objects) LOOP
            SELECT r.id INTO reservation FROM app.storage_reservation r
              WHERE r.tenant_id = t AND r.object_key =
                'tenants/' || t::text || '/uploads/' || intent.upload_id::text || '/'
                || (planned->>'name');
            IF FOUND THEN
              released := released + app.release_storage(reservation, intent.id);
            ELSE
              legacy := legacy + (planned->>'byte_size')::bigint;
              legacy_count := legacy_count + 1;
            END IF;
          END LOOP;
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t FOR UPDATE;
          IF FOUND AND counter.initialized AND legacy_count > 0 THEN
            -- Pre-ledger parts lower only the seeded baseline, never below zero.
            SELECT count(*) INTO outstanding FROM app.storage_reservation r
              WHERE r.tenant_id = t AND r.state <> 'released';
            lower_bytes := LEAST(legacy, counter.committed_bytes);
            lower_entries := LEAST(legacy_count, GREATEST(counter.entries - outstanding, 0));
            UPDATE app.storage_usage s
              SET committed_bytes = s.committed_bytes - lower_bytes,
                  entries = s.entries - lower_entries,
                  version = s.version + 1, updated_at = clock_timestamp()
              WHERE s.tenant_id = t;
            UPDATE app.storage_global_usage g
              SET charged_bytes = GREATEST(g.charged_bytes - lower_bytes, 0),
                  entries = GREATEST(g.entries - lower_entries, 0), version = g.version + 1
              WHERE g.id = 1;
          END IF;
          UPDATE app.upload_cleanup c
            SET completed_at = clock_timestamp(), completion_summary = p_summary
            WHERE c.id = intent.id;
          RETURN QUERY SELECT released, lower_bytes;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION app.release_object_cleanup(p_cleanup uuid, p_summary jsonb)
        RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          intent app.object_cleanup%ROWTYPE;
          planned jsonb;
          item app.storage_reservation%ROWTYPE;
          released bigint := 0;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          IF jsonb_typeof(p_summary) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Invalid cleanup summary';
          END IF;
          SELECT * INTO intent FROM app.object_cleanup c
            WHERE c.id = p_cleanup AND c.tenant_id = t FOR UPDATE;
          IF NOT FOUND OR intent.completed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Cleanup intent unavailable';
          END IF;
          FOR planned IN SELECT value FROM jsonb_array_elements(intent.objects) LOOP
            SELECT * INTO item FROM app.storage_reservation r
              WHERE r.tenant_id = t AND r.id = (planned->>'reservation_id')::uuid FOR UPDATE;
            IF NOT FOUND OR item.state NOT IN ('cleanup_planned', 'released') THEN
              RAISE EXCEPTION 'Cleanup reservation changed';
            END IF;
            released := released + app.release_storage(item.id, intent.id);
          END LOOP;
          UPDATE app.object_cleanup c
            SET completed_at = clock_timestamp(), completion_summary = p_summary
            WHERE c.id = intent.id;
          RETURN released;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION app.reconcile_storage(p_bytes bigint, p_entries bigint)
        RETURNS TABLE(was_initialized boolean, ledger_bytes bigint, ledger_entries bigint,
                      raised_bytes bigint, raised_entries bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          counter app.storage_usage%ROWTYPE;
          add_bytes bigint;
          add_entries bigint;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          IF p_bytes IS NULL OR p_entries IS NULL OR p_bytes < 0 OR p_entries < 0 THEN
            RAISE EXCEPTION 'Invalid storage measurement';
          END IF;
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Storage ledger unavailable';
          END IF;
          -- Raise to the measured retained usage; never lower a charge.
          add_bytes := GREATEST(p_bytes - (counter.reserved_bytes + counter.committed_bytes), 0);
          add_entries := GREATEST(p_entries - counter.entries, 0);
          UPDATE app.storage_usage s
            SET committed_bytes = s.committed_bytes + add_bytes,
                entries = s.entries + add_entries, initialized = true,
                version = s.version + 1, updated_at = clock_timestamp()
            WHERE s.tenant_id = t;
          UPDATE app.storage_global_usage g
            SET charged_bytes = g.charged_bytes + add_bytes, entries = g.entries + add_entries,
                version = g.version + 1
            WHERE g.id = 1;
          RETURN QUERY SELECT counter.initialized, counter.reserved_bytes + counter.committed_bytes,
            counter.entries, add_bytes, add_entries;
        END $$
    """)
    for signature in (*RUNTIME_FUNCTIONS, *INTERNAL_FUNCTIONS):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
    for signature in RUNTIME_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO cgl_runtime")
    op.execute("""
        CREATE OR REPLACE FUNCTION app.initialize_agency_platform() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$ BEGIN
          INSERT INTO app.agency_profile(id,tenant_id) VALUES(gen_random_uuid(),NEW.id);
          INSERT INTO app.agency_subscription(id,tenant_id) VALUES(gen_random_uuid(),NEW.id);
          INSERT INTO app.storage_usage(tenant_id,initialized) VALUES(NEW.id,true);
          RETURN NEW;
        END; $$
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION app.initialize_agency_platform() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$ BEGIN
          INSERT INTO app.agency_profile(id,tenant_id) VALUES(gen_random_uuid(),NEW.id);
          INSERT INTO app.agency_subscription(id,tenant_id) VALUES(gen_random_uuid(),NEW.id);
          RETURN NEW;
        END; $$
    """)
    for signature in (*RUNTIME_FUNCTIONS, "release_storage(uuid,uuid)"):
        op.execute(f"DROP FUNCTION app.{signature}")
    op.execute("DROP FUNCTION app.storage_ready()")
    for table in (
        "storage_sweep",
        "object_cleanup",
        "storage_reservation",
        "storage_usage",
        "storage_global_usage",
        "storage_limit",
    ):
        op.drop_table(table, schema="app")
    op.execute("DROP FUNCTION app.guard_storage_reservation()")

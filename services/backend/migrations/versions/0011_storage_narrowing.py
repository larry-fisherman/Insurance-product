"""Narrow the storage definers, restore reservation foreign keys, exact entry charges.

The fixes behind ADR 0008's review:

- Cleanup intents can no longer be inserted directly by the runtime role. Only the
  planning definers create them, and those verify eligibility against database state.
- ``reconcile_storage`` (which accepts a measured byte count) moves to a dedicated
  operator role that API and worker processes hold no credentials for.
- Pre-ledger part bytes are lowered against a trusted per-object record written by
  reconcile, once per object, however many plans list that object.
- Entries charge the directories a write creates, so reconcile no longer ratchets.
- Counter and reservation lock waits are bounded inside the functions themselves.
- ``storage_reservation`` regains its tenant and membership foreign keys.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0011_storage_narrow"
down_revision = "0010_storage"
branch_labels = None
depends_on = None

OPERATOR = "cgl_storage_operator"
LOCK_TIMEOUT = "SET lock_timeout='5s'"
PLANNING_FUNCTIONS = (
    "plan_object_cleanup(uuid,uuid,text,jsonb)",
    "plan_upload_cleanup(uuid,uuid,uuid,text,jsonb)",
)
RECONCILE = "reconcile_storage(bigint,bigint,text[],jsonb)"


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{OPERATOR}') THEN
            RAISE EXCEPTION 'Role {OPERATOR} is missing: run make app-config and make app-roles';
          END IF;
        END $$
    """)
    op.add_column(
        "storage_reservation",
        sa.Column("entry_charge", sa.Integer(), nullable=False, server_default="1"),
        schema="app",
    )
    op.create_check_constraint(
        "positive_entry_charge", "storage_reservation", "entry_charge > 0", schema="app"
    )
    op.add_column(
        "storage_usage",
        sa.Column(
            "charged_directories",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        schema="app",
    )
    op.add_column(
        "storage_sweep", sa.Column("unlinked_upload_parts", sa.BigInteger()), schema="app"
    )
    op.add_column("storage_sweep", sa.Column("cleanup_planned", sa.BigInteger()), schema="app")
    # Validated against every existing row: the reservation definer already bound both.
    op.create_foreign_key(
        "fk_storage_reservation_tenant_id",
        "storage_reservation",
        "tenant",
        ["tenant_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
    )
    op.create_foreign_key(
        "fk_storage_reservation_created_by",
        "storage_reservation",
        "membership",
        ["tenant_id", "created_by"],
        ["tenant_id", "id"],
        source_schema="app",
        referent_schema="app",
    )
    op.create_table(
        "storage_legacy_part",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("app.tenant.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_ref", sa.Uuid()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_legacy_part_tenant_id"),
        sa.UniqueConstraint("object_key", name="uq_storage_legacy_part_object_key"),
        sa.CheckConstraint("byte_size >= 0", name="non_negative_size"),
        schema="app",
    )
    op.execute("ALTER TABLE app.storage_legacy_part ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.storage_legacy_part FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY maintenance ON app.storage_legacy_part TO cgl_migrator "
        "USING (true) WITH CHECK (true)"
    )
    # Intents come only from the planning definers below.
    op.execute("REVOKE INSERT ON app.upload_cleanup FROM cgl_runtime")
    op.execute("REVOKE INSERT ON app.object_cleanup FROM cgl_runtime")
    op.execute("""
        CREATE OR REPLACE FUNCTION app.guard_storage_reservation() RETURNS trigger
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
          IF (NEW.state IN ('released','cleanup_planned') AND NEW.state IS DISTINCT FROM OLD.state
              OR NEW.released_at IS DISTINCT FROM OLD.released_at
              OR NEW.release_ref IS DISTINCT FROM OLD.release_ref)
             AND (SELECT relowner FROM pg_catalog.pg_class WHERE oid = TG_RELID)
                 <> (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
            RAISE EXCEPTION 'Storage cleanup transitions require a planning or completion function';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION app.reserve_storage(
          p_id uuid, p_purpose text, p_owner uuid, p_slot text, p_key text, p_bytes bigint,
          p_sha256 text, p_request_hash text, p_ttl_seconds integer, p_margin_seconds integer,
          p_created_by uuid)
        RETURNS TABLE(outcome text, reserved_id uuid, reserved_key text, reserved_until timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app {LOCK_TIMEOUT} AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          category text;
          lim app.storage_limit%ROWTYPE;
          counter app.storage_usage%ROWTYPE;
          existing app.storage_reservation%ROWTYPE;
          created app.storage_reservation%ROWTYPE;
          renewed timestamptz;
          directory text;
          charge integer := 1;
          missing text[];
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          category := CASE p_purpose WHEN 'upload_part' THEN 'uploads'
            WHEN 'document' THEN 'documents' WHEN 'artifact' THEN 'artifacts' END;
          IF category IS NULL OR p_bytes IS NULL OR p_bytes < 0 OR p_owner IS NULL
             OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 60 AND 86400
             OR p_margin_seconds IS NULL OR p_margin_seconds NOT BETWEEN 0 AND p_ttl_seconds / 2
             OR p_key !~ ('^tenants/' || t::text || '/' || category || '/[0-9a-f-]{{36}}/[a-zA-Z0-9._-]+$')
             OR p_sha256 !~ '^[0-9a-f]{{64}}$' OR p_request_hash !~ '^[0-9a-f]{{64}}$'
             OR length(p_slot) NOT BETWEEN 1 AND 100
             OR (p_created_by IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM app.membership m
                   WHERE m.tenant_id = t AND m.id = p_created_by
                     AND m.user_id = app.current_user_id() AND m.status = 'active'))
          THEN
            RAISE EXCEPTION 'Invalid storage reservation';
          END IF;
          -- Lock order: logical slot, reservation row, tenant counter, global counter.
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'storage-slot:' || t::text || ':' || p_purpose || ':' || p_owner::text || ':' || p_slot, 0));
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t;
          IF NOT FOUND OR NOT counter.initialized OR NOT app.storage_ready() THEN
            RETURN QUERY SELECT 'unavailable'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          SELECT * INTO existing FROM app.storage_reservation r
            WHERE r.tenant_id = t AND r.purpose = p_purpose AND r.owner_id = p_owner
              AND r.slot = p_slot AND r.state = 'reserved'
            ORDER BY r.created_at DESC LIMIT 1 FOR UPDATE;
          IF FOUND THEN
            IF existing.request_hash = p_request_hash THEN
              -- The same logical write: keep its charge and key, renewing a lapsed TTL.
              renewed := existing.expires_at;
              IF renewed <= clock_timestamp() + make_interval(secs => p_margin_seconds) THEN
                renewed := clock_timestamp() + make_interval(secs => p_ttl_seconds);
                UPDATE app.storage_reservation r SET expires_at = renewed WHERE r.id = existing.id;
              END IF;
              RETURN QUERY SELECT 'reused'::text, existing.id, existing.object_key, renewed;
              RETURN;
            ELSIF existing.expires_at > clock_timestamp() + make_interval(secs => p_margin_seconds) THEN
              RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::text, NULL::timestamptz;
              RETURN;
            END IF;
          END IF;
          IF EXISTS (SELECT 1 FROM app.storage_reservation r WHERE r.object_key = p_key) THEN
            -- A stable key already planned, released or linked is never rebound.
            RETURN QUERY SELECT 'conflict'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          -- Entries bound directories too: charge the ones this write would create.
          directory := substring(p_key from '^.*/');
          IF NOT EXISTS (
            SELECT 1 FROM app.storage_reservation r
            WHERE r.tenant_id = t AND r.purpose = p_purpose AND r.owner_id = p_owner
              AND r.state <> 'released' AND left(r.object_key, length(directory)) = directory
          ) THEN
            charge := charge + 1;
          END IF;
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t FOR UPDATE;
          missing := ARRAY(SELECT d FROM unnest(ARRAY['tenant', category]) d
                           WHERE NOT d = ANY(counter.charged_directories));
          charge := charge + cardinality(missing);
          SELECT * INTO lim FROM app.storage_limit l WHERE l.id = 1;
          IF counter.reserved_bytes + counter.committed_bytes + p_bytes > lim.tenant_byte_limit THEN
            RETURN QUERY SELECT 'capacity'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          UPDATE app.storage_global_usage g
            SET charged_bytes = g.charged_bytes + p_bytes, entries = g.entries + charge,
                version = g.version + 1
            WHERE g.id = 1 AND g.charged_bytes + p_bytes <= lim.total_byte_limit
              AND g.entries + charge <= lim.entry_limit;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'capacity'::text, NULL::uuid, NULL::text, NULL::timestamptz;
            RETURN;
          END IF;
          UPDATE app.storage_usage s
            SET reserved_bytes = s.reserved_bytes + p_bytes, entries = s.entries + charge,
                charged_directories = s.charged_directories || missing,
                version = s.version + 1, updated_at = clock_timestamp()
            WHERE s.tenant_id = t
              AND s.reserved_bytes + s.committed_bytes + p_bytes <= lim.tenant_byte_limit;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Storage counter changed under lock';
          END IF;
          INSERT INTO app.storage_reservation(
              id, tenant_id, purpose, owner_id, slot, object_key, byte_size, sha256,
              request_hash, state, expires_at, created_by, entry_charge)
            VALUES (p_id, t, p_purpose, p_owner, p_slot, p_key, p_bytes, p_sha256,
              p_request_hash, 'reserved',
              clock_timestamp() + make_interval(secs => p_ttl_seconds), p_created_by, charge)
            RETURNING * INTO created;
          RETURN QUERY SELECT 'reserved'::text, created.id, created.object_key, created.expires_at;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION app.release_storage(p_reservation uuid, p_ref uuid) RETURNS bigint
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
            SET reserved_bytes = s.reserved_bytes - item.byte_size,
                entries = s.entries - item.entry_charge,
                version = s.version + 1, updated_at = clock_timestamp()
            WHERE s.tenant_id = t;
          UPDATE app.storage_global_usage g
            SET charged_bytes = g.charged_bytes - item.byte_size,
                entries = g.entries - item.entry_charge,
                version = g.version + 1
            WHERE g.id = 1;
          UPDATE app.storage_reservation r
            SET state = 'released', released_at = clock_timestamp(), release_ref = p_ref
            WHERE r.id = item.id;
          RETURN item.byte_size;
        END $$
    """)
    op.execute(f"""
        CREATE FUNCTION app.plan_upload_cleanup(
          p_id uuid, p_upload uuid, p_requested_by uuid, p_reason text, p_objects jsonb)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app {LOCK_TIMEOUT} AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          upload app.upload_session%ROWTYPE;
          keys text[];
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          IF p_id IS NULL OR length(p_reason) NOT BETWEEN 10 AND 500
             OR NOT EXISTS (SELECT 1 FROM app.membership m
                            WHERE m.tenant_id = t AND m.id = p_requested_by
                              AND m.user_id = app.current_user_id() AND m.status = 'active')
             OR jsonb_typeof(p_objects) IS DISTINCT FROM 'array'
             OR jsonb_array_length(p_objects) NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION 'Invalid cleanup plan';
          END IF;
          IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_objects) o
                     WHERE (o.value->>'name') IS NULL
                        OR (o.value->>'name') !~ '^([0-9]|[1-4][0-9])-[0-9a-f]{{32}}$'
                        OR (o.value->>'sha256') !~ '^[0-9a-f]{{64}}$'
                        OR (o.value->>'byte_size')::bigint NOT BETWEEN 0 AND 1048576) THEN
            RAISE EXCEPTION 'Invalid cleanup plan';
          END IF;
          SELECT * INTO upload FROM app.upload_session u WHERE u.id = p_upload AND u.tenant_id = t;
          IF NOT FOUND OR upload.cleanup_hold
             OR upload.expires_at > clock_timestamp() - interval '24 hours'
             OR upload.status NOT IN ('open','aborted','finalized')
             OR (upload.status = 'finalized') <> (upload.document_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Upload is not eligible for cleanup';
          END IF;
          keys := ARRAY(SELECT 'tenants/' || t::text || '/uploads/' || p_upload::text || '/'
                               || (o.value->>'name')
                        FROM jsonb_array_elements(p_objects) o);
          IF EXISTS (SELECT 1 FROM app.document_version d
                     WHERE d.tenant_id = t AND d.object_key = ANY(keys))
             OR EXISTS (SELECT 1 FROM app.package_artifact a
                        WHERE a.tenant_id = t AND a.object_key = ANY(keys)) THEN
            RAISE EXCEPTION 'Retained objects cannot be planned for cleanup';
          END IF;
          INSERT INTO app.upload_cleanup(
              id, tenant_id, upload_id, requested_by, reason, policy_version, objects)
            VALUES (p_id, t, p_upload, p_requested_by, p_reason, 'local-upload-cleanup-1', p_objects);
        END $$
    """)
    op.execute(f"""
        CREATE FUNCTION app.plan_object_cleanup(
          p_id uuid, p_requested_by uuid, p_reason text, p_objects jsonb)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app {LOCK_TIMEOUT} AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          ids uuid[];
          item app.storage_reservation%ROWTYPE;
          seen integer := 0;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          IF p_id IS NULL OR length(p_reason) NOT BETWEEN 10 AND 500
             OR NOT EXISTS (SELECT 1 FROM app.membership m
                            WHERE m.tenant_id = t AND m.id = p_requested_by
                              AND m.user_id = app.current_user_id() AND m.status = 'active')
             OR jsonb_typeof(p_objects) IS DISTINCT FROM 'array'
             OR jsonb_array_length(p_objects) NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION 'Invalid cleanup plan';
          END IF;
          ids := ARRAY(SELECT DISTINCT (o.value->>'reservation_id')::uuid
                       FROM jsonb_array_elements(p_objects) o);
          IF cardinality(ids) <> jsonb_array_length(p_objects) THEN
            RAISE EXCEPTION 'Invalid cleanup plan';
          END IF;
          -- Eligibility is decided here from the rows, never from the caller's manifest.
          FOR item IN SELECT * FROM app.storage_reservation r
                      WHERE r.tenant_id = t AND r.id = ANY(ids) ORDER BY r.id FOR UPDATE LOOP
            seen := seen + 1;
            IF item.state <> 'reserved' OR item.purpose = 'upload_part'
               OR item.expires_at >= clock_timestamp() - interval '24 hours'
               OR EXISTS (SELECT 1 FROM app.document_version d
                          WHERE d.tenant_id = t AND d.object_key = item.object_key)
               OR EXISTS (SELECT 1 FROM app.package_artifact a
                          WHERE a.tenant_id = t AND a.object_key = item.object_key)
               OR EXISTS (SELECT 1 FROM app.upload_part p
                          WHERE p.tenant_id = t AND p.object_key = item.object_key) THEN
              RAISE EXCEPTION 'Reservation is not an eligible orphan';
            END IF;
            UPDATE app.storage_reservation r SET state = 'cleanup_planned' WHERE r.id = item.id;
          END LOOP;
          IF seen <> cardinality(ids) THEN
            RAISE EXCEPTION 'Reservation is not an eligible orphan';
          END IF;
          INSERT INTO app.object_cleanup(
              id, tenant_id, category, requested_by, reason, policy_version, objects)
            VALUES (p_id, t, 'reservation_orphan', p_requested_by, p_reason,
                    'storage-orphan-cleanup-1', p_objects);
        END $$
    """)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION app.release_upload_cleanup(p_cleanup uuid, p_summary jsonb)
        RETURNS TABLE(released_bytes bigint, legacy_bytes bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app {LOCK_TIMEOUT} AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          intent app.upload_cleanup%ROWTYPE;
          upload app.upload_session%ROWTYPE;
          counter app.storage_usage%ROWTYPE;
          keys text[];
          reservation uuid;
          bytes bigint;
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
          SELECT * INTO upload FROM app.upload_session u
            WHERE u.id = intent.upload_id AND u.tenant_id = t;
          IF NOT FOUND OR upload.cleanup_hold
             OR upload.expires_at > clock_timestamp() - interval '24 hours' THEN
            RAISE EXCEPTION 'Upload is not eligible for cleanup';
          END IF;
          keys := ARRAY(SELECT 'tenants/' || t::text || '/uploads/' || intent.upload_id::text
                               || '/' || (o.value->>'name')
                        FROM jsonb_array_elements(intent.objects) o);
          -- Every reservation row is locked, in id order, before any counter row.
          PERFORM 1 FROM app.storage_reservation r
            WHERE r.tenant_id = t AND r.object_key = ANY(keys) ORDER BY r.id FOR UPDATE;
          -- Pre-ledger bytes lower only a charge reconcile recorded, once per object, so a
          -- second plan listing the same part finds nothing left to lower.
          FOR bytes IN
            UPDATE app.storage_legacy_part l
              SET released_at = clock_timestamp(), release_ref = intent.id
              WHERE l.tenant_id = t AND l.object_key = ANY(keys) AND l.released_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM app.storage_reservation r
                                WHERE r.object_key = l.object_key)
              RETURNING l.byte_size
          LOOP
            legacy := legacy + bytes;
            legacy_count := legacy_count + 1;
          END LOOP;
          FOR reservation IN SELECT r.id FROM app.storage_reservation r
                             WHERE r.tenant_id = t AND r.object_key = ANY(keys) ORDER BY r.id LOOP
            released := released + app.release_storage(reservation, intent.id);
          END LOOP;
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t FOR UPDATE;
          IF FOUND AND counter.initialized AND legacy_count > 0 THEN
            -- A wholly pre-ledger upload directory was charged by the baseline too.
            IF NOT EXISTS (SELECT 1 FROM app.storage_reservation r
                           WHERE r.tenant_id = t AND r.owner_id = intent.upload_id) THEN
              legacy_count := legacy_count + 1;
            END IF;
            SELECT coalesce(sum(r.entry_charge), 0) INTO outstanding
              FROM app.storage_reservation r WHERE r.tenant_id = t AND r.state <> 'released';
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
    op.execute(f"""
        CREATE OR REPLACE FUNCTION app.release_object_cleanup(p_cleanup uuid, p_summary jsonb)
        RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app {LOCK_TIMEOUT} AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          intent app.object_cleanup%ROWTYPE;
          ids uuid[];
          item app.storage_reservation%ROWTYPE;
          reservation uuid;
          seen integer := 0;
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
          ids := ARRAY(SELECT (o.value->>'reservation_id')::uuid
                       FROM jsonb_array_elements(intent.objects) o);
          -- Every reservation row is locked, in id order, before any counter row.
          FOR item IN SELECT * FROM app.storage_reservation r
                      WHERE r.tenant_id = t AND r.id = ANY(ids) ORDER BY r.id FOR UPDATE LOOP
            seen := seen + 1;
            IF item.state NOT IN ('cleanup_planned', 'released')
               OR (item.state = 'cleanup_planned' AND item.purpose = 'upload_part') THEN
              RAISE EXCEPTION 'Cleanup reservation changed';
            END IF;
          END LOOP;
          IF seen <> cardinality(ids) THEN
            RAISE EXCEPTION 'Cleanup reservation changed';
          END IF;
          FOREACH reservation IN ARRAY ARRAY(SELECT unnest(ids) ORDER BY 1) LOOP
            released := released + app.release_storage(reservation, intent.id);
          END LOOP;
          UPDATE app.object_cleanup c
            SET completed_at = clock_timestamp(), completion_summary = p_summary
            WHERE c.id = intent.id;
          RETURN released;
        END $$
    """)
    op.execute("DROP FUNCTION app.reconcile_storage(bigint,bigint)")
    op.execute(f"""
        CREATE FUNCTION app.reconcile_storage(
          p_bytes bigint, p_entries bigint, p_directories text[], p_legacy_parts jsonb)
        RETURNS TABLE(was_initialized boolean, ledger_bytes bigint, ledger_entries bigint,
                      raised_bytes bigint, raised_entries bigint)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app {LOCK_TIMEOUT} AS $$
        DECLARE
          t uuid := app.current_tenant_id();
          counter app.storage_usage%ROWTYPE;
          add_bytes bigint;
          add_entries bigint;
        BEGIN
          IF t IS NULL OR NOT app.active_member(t) THEN
            RAISE EXCEPTION 'Storage scope unavailable';
          END IF;
          IF p_bytes IS NULL OR p_entries IS NULL OR p_bytes < 0 OR p_entries < 0
             OR p_directories IS NULL
             OR jsonb_typeof(p_legacy_parts) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Invalid storage measurement';
          END IF;
          IF EXISTS (SELECT 1 FROM jsonb_array_elements(p_legacy_parts) o
                     WHERE (o.value->>'object_key') IS NULL
                        OR (o.value->>'object_key') !~
                           ('^tenants/' || t::text || '/uploads/[0-9a-f-]{{36}}/[a-zA-Z0-9._-]+$')
                        OR (o.value->>'byte_size')::bigint < 0) THEN
            RAISE EXCEPTION 'Invalid storage measurement';
          END IF;
          SELECT * INTO counter FROM app.storage_usage s WHERE s.tenant_id = t FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Storage ledger unavailable';
          END IF;
          -- Raise to the measured retained usage; never lower a charge.
          add_bytes := GREATEST(p_bytes - (counter.reserved_bytes + counter.committed_bytes), 0);
          add_entries := GREATEST(p_entries - counter.entries, 0);
          INSERT INTO app.storage_legacy_part(id, tenant_id, object_key, byte_size)
            SELECT gen_random_uuid(), t, o.value->>'object_key', (o.value->>'byte_size')::bigint
            FROM jsonb_array_elements(p_legacy_parts) o
            WHERE NOT EXISTS (SELECT 1 FROM app.storage_reservation r
                              WHERE r.object_key = o.value->>'object_key')
            ON CONFLICT (object_key) DO NOTHING;
          UPDATE app.storage_usage s
            SET committed_bytes = s.committed_bytes + add_bytes,
                entries = s.entries + add_entries, initialized = true,
                charged_directories = ARRAY(
                  SELECT DISTINCT d FROM unnest(s.charged_directories || p_directories) d ORDER BY 1),
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
    for signature in (*PLANNING_FUNCTIONS, RECONCILE):
        op.execute(f"REVOKE ALL ON FUNCTION app.{signature} FROM PUBLIC")
    for signature in PLANNING_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION app.{signature} TO cgl_runtime")
    # The operator role can reach exactly one function and no table.
    op.execute(f"GRANT USAGE ON SCHEMA app TO {OPERATOR}")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.{RECONCILE} TO {OPERATOR}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION app.{RECONCILE} FROM {OPERATOR}")
    op.execute(f"REVOKE USAGE ON SCHEMA app FROM {OPERATOR}")
    op.execute(f"DROP FUNCTION app.{RECONCILE}")
    for signature in PLANNING_FUNCTIONS:
        op.execute(f"DROP FUNCTION app.{signature}")
    op.execute("""
        CREATE OR REPLACE FUNCTION app.guard_storage_reservation() RETURNS trigger
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
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION app.reserve_storage(
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
        CREATE OR REPLACE FUNCTION app.release_storage(p_reservation uuid, p_ref uuid) RETURNS bigint
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
        CREATE OR REPLACE FUNCTION app.release_upload_cleanup(p_cleanup uuid, p_summary jsonb)
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
        CREATE OR REPLACE FUNCTION app.release_object_cleanup(p_cleanup uuid, p_summary jsonb)
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
    op.execute("REVOKE ALL ON FUNCTION app.reconcile_storage(bigint,bigint) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.reconcile_storage(bigint,bigint) TO cgl_runtime")
    op.execute("GRANT INSERT ON app.upload_cleanup TO cgl_runtime")
    op.execute("GRANT INSERT ON app.object_cleanup TO cgl_runtime")
    op.drop_table("storage_legacy_part", schema="app")
    op.drop_constraint(
        "fk_storage_reservation_created_by", "storage_reservation", schema="app", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_storage_reservation_tenant_id", "storage_reservation", schema="app", type_="foreignkey"
    )
    op.drop_column("storage_sweep", "cleanup_planned", schema="app")
    op.drop_column("storage_sweep", "unlinked_upload_parts", schema="app")
    op.drop_column("storage_usage", "charged_directories", schema="app")
    op.drop_constraint("positive_entry_charge", "storage_reservation", schema="app", type_="check")
    op.drop_column("storage_reservation", "entry_charge", schema="app")

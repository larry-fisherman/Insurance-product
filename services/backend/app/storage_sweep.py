"""Report-only storage sweep (ADR 0008): per-tenant lease, counts only, never deletes.

Each run borrows an active member for RLS scope (the dispatch ``agency_actors``
pattern; production service principals remain open). It walks only
``tenants/{id}/`` for the tenant being reported and writes counts, never keys,
paths or contents. Unknown tenant directories are counted and logged, never opened.
"""

import logging
import os
import random
from datetime import timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.common import now
from app.config import Settings
from app.database.models import Tenant
from app.database.runtime import Database
from app.domain.models import DocumentVersion, PackageArtifact
from app.platform_models import StorageReservation, StorageSweep, StorageUsage, UploadPart
from app.storage import FileStore, TenantScan, storage

LEASE = timedelta(minutes=10)
log = logging.getLogger("cgl.storage")


def sweep_row(
    session: Session, tenant_id: UUID, *, skip_locked: bool = False
) -> StorageSweep | None:
    """Create this tenant's single report row if absent, then lock it."""
    session.execute(
        insert(StorageSweep)
        .values(tenant_id=tenant_id)
        .on_conflict_do_nothing(constraint="uq_storage_sweep_single_tenant")
    )
    return session.scalar(
        sa.select(StorageSweep)
        .where(StorageSweep.tenant_id == tenant_id)
        .with_for_update(skip_locked=skip_locked)
    )


def claim(configuration: Settings, db: Database, tenant_id: UUID, user_id: UUID) -> int | None:
    """Claim the tenant lease; None when another sweeper holds it or a report is fresh."""
    with db.tenant(tenant_id, user_id) as session:
        row = sweep_row(session, tenant_id, skip_locked=True)
        if row is None or (row.lease_until is not None and row.lease_until > now()):
            return None
        fresh = timedelta(seconds=configuration.storage_sweep_interval_seconds // 2)
        if row.reported_at is not None and row.reported_at > now() - fresh:
            return None  # Repeated broker delivery: the latest report is still current.
        row.attempt += 1
        row.lease_until = now() + LEASE
        return row.attempt


def counts(session: Session, tenant_id: UUID, scan: TenantScan) -> dict[str, int]:
    from app.orphan_cleanup import candidates, unlinked_upload_parts

    reserved = set(
        session.scalars(
            sa.select(StorageReservation.object_key).where(StorageReservation.state != "released")
        )
    )
    referenced: set[str] = set()
    for model in (DocumentVersion, PackageArtifact, UploadPart):
        referenced.update(session.scalars(sa.select(model.object_key)))
    orphans = list(session.scalars(candidates(now())))
    legacy = {key: size for key, size in scan.files.items() if key not in reserved | referenced}
    parts = len(list(session.scalars(unlinked_upload_parts(now()))))
    planned = session.scalar(
        sa.select(sa.func.count())
        .select_from(StorageReservation)
        .where(StorageReservation.state == "cleanup_planned")
    )
    usage = session.get(StorageUsage, tenant_id)
    ledger_bytes = (usage.reserved_bytes + usage.committed_bytes) if usage else 0
    return {
        "orphan_candidates": len(orphans),
        "expired_absent": sum(1 for row in orphans if row.object_key not in scan.files),
        "unlinked_upload_parts": parts,
        "cleanup_planned": int(planned or 0),
        "legacy_unreserved": len(legacy),
        "legacy_unreserved_bytes": sum(legacy.values()),
        "anomalies": scan.anomalies,
        "drift_bytes": scan.byte_size - ledger_bytes,
        "drift_entries": scan.entries - (usage.entries if usage else 0),
    }


def report(db: Database, store: FileStore, tenant_id: UUID, user_id: UUID, attempt: int) -> bool:
    """Measure and record counts; a stale attempt never overwrites a newer report."""
    with store.write_lock():
        scan = store.scan_tenant(tenant_id)
    with db.tenant(tenant_id, user_id) as session:
        row = sweep_row(session, tenant_id)
        if (
            row is None
            or row.attempt != attempt
            or row.lease_until is None
            or row.lease_until <= now()
        ):
            return False
        for name, value in counts(session, tenant_id, scan).items():
            setattr(row, name, value)
        row.origin, row.reported_at, row.report_attempt = "scheduler", now(), attempt
        row.lease_until = None
    return True


def sweep_tenant(
    configuration: Settings, db: Database, store: FileStore, tenant_id: UUID, user_id: UUID
) -> bool:
    attempt = claim(configuration, db, tenant_id, user_id)
    return attempt is not None and report(db, store, tenant_id, user_id, attempt)


def unknown_tenant_directories(db: Database, store: FileStore) -> int:
    with db.identity() as session:
        known = {str(tenant) for tenant in session.scalars(sa.select(Tenant.id))}
    try:
        names = os.listdir(store.root / "tenants")  # Names only; never opened or walked.
    except FileNotFoundError:
        return 0
    return sum(1 for name in names if name not in known)


def run(configuration: Settings) -> int:
    if configuration.storage_sweep_mode != "report":
        raise ValueError("Only the report-only storage sweep is supported")
    store = storage(configuration)
    if not isinstance(store, FileStore):
        return 0  # S3 stays gated; nothing local to report.
    from app.worker import agency_actors

    db = Database(configuration)
    try:
        pairs = agency_actors(db)
        selected = random.sample(pairs, min(len(pairs), configuration.storage_sweep_batch))
        reported = sum(sweep_tenant(configuration, db, store, *pair) for pair in selected)
        unknown = unknown_tenant_directories(db, store)
        if unknown:
            log.warning("storage_sweep unknown_tenant_directories=%s", unknown)
        return reported
    finally:
        db.close()

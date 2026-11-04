"""Operator storage-ledger commands; never migration or admin credentials.

``reconcile`` seeds each tenant's ledger from a locked scan of ``tenants/{id}/`` on
the first run and afterwards only raises counters when retained bytes exceed the
ledger. Only this command submits a measurement, through the dedicated
``cgl_storage_operator`` role that API and worker processes hold no credentials for.
``report`` prints counts and pending plan IDs through the runtime role. Neither
command deletes anything.
"""

import argparse
import sys
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from app.common import now
from app.config import Settings
from app.database.config import StorageOperatorSettings
from app.database.models import Tenant
from app.database.runtime import Database
from app.platform_models import ObjectCleanup, StorageReservation, StorageSweep, StorageUsage
from app.storage import FileStore, storage
from app.storage_quota import reconcile, tenant_session
from app.storage_sweep import sweep_row

MAX_LISTED_PLANS = 20


@dataclass(frozen=True)
class Reconciled:
    tenant_id: UUID
    seeded: bool
    measured_bytes: int
    measured_entries: int
    ledger_bytes: int
    ledger_entries: int
    raised_bytes: int
    raised_entries: int
    anomalies: int

    # Seeding establishes the baseline; drift is measured only against a seeded ledger.
    @property
    def drift_bytes(self) -> int:
        return 0 if self.seeded else self.measured_bytes - self.ledger_bytes

    @property
    def drift_entries(self) -> int:
        return 0 if self.seeded else self.measured_entries - self.ledger_entries


def operator_engine(db: Database) -> Engine:
    """The operator role against the database this runtime pool uses; no pooled reuse."""
    url = make_url(StorageOperatorSettings().storage_operator_database_url.get_secret_value())
    return sa.create_engine(
        url.set(database=db.runtime_engine.url.database),
        poolclass=NullPool,
        hide_parameters=True,
    )


def reconcile_tenant(db: Database, store: FileStore, tenant_id: UUID, user_id: UUID) -> Reconciled:
    # Hold the storage lock across the scan and the counter update, so a cleanup
    # cannot unlink and release between them (ADR 0008 lock-order exception;
    # nothing that holds a counter row ever waits for this lock).
    with store.write_lock():
        scan = store.scan_tenant(tenant_id)
        if scan.anomalies:
            # Fail closed: an unexpected symlink/special file needs investigation,
            # and an unseeded tenant keeps reservations unavailable.
            with db.tenant(tenant_id, user_id) as session:
                current = session.get(StorageUsage, tenant_id)
                return Reconciled(
                    tenant_id=tenant_id,
                    seeded=False,
                    measured_bytes=scan.byte_size,
                    measured_entries=scan.entries,
                    ledger_bytes=current.reserved_bytes + current.committed_bytes if current else 0,
                    ledger_entries=current.entries if current else 0,
                    raised_bytes=0,
                    raised_entries=0,
                    anomalies=scan.anomalies,
                )
        engine = operator_engine(db)
        try:
            with tenant_session(engine, tenant_id, user_id) as session:
                row = reconcile(session, scan)
        finally:
            engine.dispose()
        result = Reconciled(
            tenant_id,
            not row.was_initialized,
            scan.byte_size,
            scan.entries,
            int(row.ledger_bytes),
            int(row.ledger_entries),
            int(row.raised_bytes),
            int(row.raised_entries),
            0,
        )
    with db.tenant(tenant_id, user_id) as session:
        report = sweep_row(session, tenant_id)
        assert report is not None
        report.origin = "operator"
        report.drift_bytes, report.drift_entries = result.drift_bytes, result.drift_entries
        report.reconciled_at = now()
    return result


def actors(db: Database) -> tuple[list[tuple[UUID, UUID]], int]:
    from app.worker import agency_actors

    pairs = agency_actors(db)
    with db.identity() as session:
        tenants = session.scalar(sa.select(sa.func.count()).select_from(Tenant)) or 0
    return pairs, int(tenants) - len(pairs)


def run_reconcile(configuration: Settings) -> int:
    store = storage(configuration)
    if not isinstance(store, FileStore):
        print("Reservation seeding requires the local filesystem adapter; S3 stays gated.")
        return 2
    db = Database(configuration)
    status = 0
    try:
        pairs, unseedable = actors(db)
        for tenant_id, user_id in pairs:
            result = reconcile_tenant(db, store, tenant_id, user_id)
            if result.anomalies:
                status = 1
            print(
                f"tenant={tenant_id} seeded_now={'yes' if result.seeded else 'no'}"
                f" measured_bytes={result.measured_bytes} ledger_bytes={result.ledger_bytes}"
                f" raised_bytes={result.raised_bytes} drift_bytes={result.drift_bytes}"
                f" drift_entries={result.drift_entries} anomalies={result.anomalies}"
            )
        if unseedable:
            print(
                f"{unseedable} tenant(s) without an active member were not seeded;"
                " their bytes are outside the global total until seeded."
            )
    finally:
        db.close()
    return status


def run_report(configuration: Settings) -> int:
    db = Database(configuration)
    try:
        pairs, unseedable = actors(db)
        for tenant_id, user_id in pairs:
            with db.tenant(tenant_id, user_id) as session:
                usage = session.get(StorageUsage, tenant_id)
                sweep = session.scalar(
                    sa.select(StorageSweep).where(StorageSweep.tenant_id == tenant_id)
                )
                planned = session.scalar(
                    sa.select(sa.func.count())
                    .select_from(StorageReservation)
                    .where(StorageReservation.state == "cleanup_planned")
                )
                # A client that lost its plan ID can finish the plan through its status route.
                pending = list(
                    session.scalars(
                        sa.select(ObjectCleanup.id)
                        .where(ObjectCleanup.completed_at.is_(None))
                        .order_by(ObjectCleanup.created_at)
                        .limit(MAX_LISTED_PLANS)
                    )
                )
            line = f"tenant={tenant_id} cleanup_planned={planned or 0}"
            if pending:
                line += " pending_plans=" + ",".join(str(plan) for plan in pending)
            if usage:
                line += (
                    f" initialized={'yes' if usage.initialized else 'no'}"
                    f" reserved_bytes={usage.reserved_bytes}"
                    f" committed_bytes={usage.committed_bytes} entries={usage.entries}"
                )
            if sweep and sweep.reported_at:
                line += (
                    f" reported_at={sweep.reported_at.isoformat()}"
                    f" orphan_candidates={sweep.orphan_candidates}"
                    f" expired_absent={sweep.expired_absent}"
                    f" unlinked_upload_parts={sweep.unlinked_upload_parts}"
                    f" legacy_unreserved={sweep.legacy_unreserved}"
                    f" legacy_unreserved_bytes={sweep.legacy_unreserved_bytes}"
                    f" anomalies={sweep.anomalies} drift_bytes={sweep.drift_bytes}"
                )
            print(line)
        if unseedable:
            print(f"{unseedable} tenant(s) without an active member are not reported.")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.storage_admin")
    parser.add_argument("command", choices=["reconcile", "report"])
    arguments = parser.parse_args(argv)
    configuration = Settings()
    if arguments.command == "reconcile":
        return run_reconcile(configuration)
    return run_report(configuration)


if __name__ == "__main__":
    sys.exit(main())

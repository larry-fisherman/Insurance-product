"""Reservation-ledger admission, write sites and reconciliation on disposable databases."""

import multiprocessing
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import Connection, Engine
from test_workspace import client as client
from test_workspace import demo, post
from test_workspace import workspace_settings as workspace_settings

from app import storage_quota
from app.common import digest, now
from app.config import Settings
from app.platform_models import (
    StorageGlobalUsage,
    StorageLimit,
    StorageReservation,
    StorageUsage,
    UploadPart,
    UploadSession,
)
from app.storage import StorageCapacityError

pytestmark = pytest.mark.database


@pytest.fixture(scope="module")
def database_engine(
    database_factory: Callable[[], AbstractContextManager[Engine]],
    migration_config: Callable[[Connection], Config],
) -> Iterator[Engine]:
    # A private database: these tests lower the migration-managed limits.
    with database_factory() as engine:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine


@contextmanager
def limits(engine: Engine, **values: int) -> Iterator[None]:
    with engine.begin() as connection:
        before = connection.execute(sa.select(StorageLimit).where(StorageLimit.id == 1)).one()
        connection.execute(sa.update(StorageLimit).where(StorageLimit.id == 1).values(**values))
    try:
        yield
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.update(StorageLimit)
                .where(StorageLimit.id == 1)
                .values(
                    tenant_byte_limit=before.tenant_byte_limit,
                    total_byte_limit=before.total_byte_limit,
                    entry_limit=before.entry_limit,
                )
            )


def usage(engine: Engine, tenant: str | UUID) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(StorageUsage).where(StorageUsage.tenant_id == UUID(str(tenant)))
        ).one()


def charged(engine: Engine, tenant: str | UUID) -> int:
    row = usage(engine, tenant)
    return int(row.reserved_bytes + row.committed_bytes)


def global_usage(engine: Engine) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(StorageGlobalUsage).where(StorageGlobalUsage.id == 1)
        ).one()


def part_key(tenant: str, owner: UUID, number: int = 0) -> str:
    return f"tenants/{tenant}/uploads/{owner}/{number}-{uuid4().hex}"


def reserve(
    configuration: Settings,
    actor: dict[str, Any],
    body: bytes,
    *,
    owner: UUID | None = None,
    slot: str = "0",
) -> storage_quota.Reservation:
    owner = owner or uuid4()
    return storage_quota.reserve(
        storage_quota.reservation_engine(configuration),
        configuration,
        UUID(actor["tenant_id"]),
        UUID(actor["user_id"]),
        purpose="upload_part",
        owner_id=owner,
        slot=slot,
        key=part_key(actor["tenant_id"], owner),
        body=body,
    )


def concurrent_reserve(
    configuration: Settings, tenant: str, user: str, size: int, purpose: str = "upload_part"
) -> bool:
    owner = uuid4()
    category = {"upload_part": "uploads", "artifact": "artifacts"}[purpose]
    try:
        storage_quota.reserve(
            storage_quota.reservation_engine(configuration),
            configuration,
            UUID(tenant),
            UUID(user),
            purpose=purpose,  # type: ignore[arg-type]
            owner_id=owner,
            slot="1:pdf",
            key=f"tenants/{tenant}/{category}/{uuid4()}/fictional.pdf",
            body=b"x" * size,
        )
        return True
    except StorageCapacityError:
        return False


def test_admission_is_exact_at_the_tenant_limit_boundary(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    base = charged(database_engine, actor["tenant_id"])
    before = global_usage(database_engine)
    with limits(database_engine, tenant_byte_limit=base + 10):
        first = reserve(workspace_settings, actor, b"1234567890")  # Exactly the limit.
        with pytest.raises(StorageCapacityError, match="^STORAGE_CAPACITY_REACHED$"):
            reserve(workspace_settings, actor, b"1")  # limit + 1
        with pytest.raises(StorageCapacityError, match="^STORAGE_CAPACITY_REACHED$"):
            reserve(workspace_settings, actor, b"1234567890")
    assert charged(database_engine, actor["tenant_id"]) == base + 10
    after = global_usage(database_engine)
    assert after.charged_bytes == before.charged_bytes + 10
    # The object, its upload directory and the tenant's first uploads/ directory.
    assert after.entries == before.entries + 3
    with database_engine.connect() as connection:
        row = connection.execute(
            sa.select(StorageReservation).where(StorageReservation.id == first.id)
        ).one()
    assert (row.state, row.byte_size, row.sha256) == ("reserved", 10, first.sha256)


def test_tenant_and_global_limits_are_independent(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, first = demo(client)
    _, second = demo(client)
    # Generated fixtures can differ by a few bytes between tenants: pad to one baseline.
    difference = charged(database_engine, first["tenant_id"]) - charged(
        database_engine, second["tenant_id"]
    )
    if difference:
        reserve(workspace_settings, second if difference > 0 else first, b"x" * abs(difference))
    base = charged(database_engine, first["tenant_id"])
    assert charged(database_engine, second["tenant_id"]) == base  # Same fictional fixtures.
    total = global_usage(database_engine).charged_bytes
    with limits(database_engine, tenant_byte_limit=base + 8, total_byte_limit=total + 12):
        reserve(workspace_settings, first, b"x" * 8)
        with pytest.raises(StorageCapacityError):
            reserve(workspace_settings, first, b"x")  # Tenant budget exhausted.
        reserve(workspace_settings, second, b"x" * 4)  # Other tenant still admitted.
        with pytest.raises(StorageCapacityError):
            reserve(workspace_settings, second, b"x")  # Global budget exhausted.
    with (
        limits(database_engine, entry_limit=global_usage(database_engine).entries),
        pytest.raises(StorageCapacityError),
    ):
        reserve(workspace_settings, second, b"")  # Entries bound empty objects too.


@pytest.mark.parametrize(
    ("bound", "purpose"),
    [("tenant", "upload_part"), ("global", "upload_part"), ("tenant", "artifact")],
)
def test_concurrent_processes_cannot_overbook_exact_boundaries(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    bound: str,
    purpose: str,
) -> None:
    # The artifact case is two-or-more workers rendering against a tenant's last bytes.
    _, actor = demo(client)
    base = charged(database_engine, actor["tenant_id"])
    total = global_usage(database_engine).charged_bytes
    values = (
        {"tenant_byte_limit": base + 24}
        if bound == "tenant"
        else {"total_byte_limit": total + 24, "tenant_byte_limit": base + 1000}
    )
    with (
        limits(database_engine, **values),
        ProcessPoolExecutor(max_workers=6, mp_context=multiprocessing.get_context("spawn")) as pool,
    ):
        futures = [
            pool.submit(
                concurrent_reserve,
                workspace_settings,
                actor["tenant_id"],
                actor["user_id"],
                8,
                purpose,
            )
            for _ in range(6)
        ]
        assert sum(future.result(timeout=60) for future in futures) == 3
    assert charged(database_engine, actor["tenant_id"]) == base + 24
    assert global_usage(database_engine).charged_bytes == total + 24


def test_retry_reuses_the_reservation_and_different_bytes_conflict(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    owner = uuid4()
    first = reserve(workspace_settings, actor, b"fictional part", owner=owner)
    base = charged(database_engine, actor["tenant_id"])
    again = reserve(workspace_settings, actor, b"fictional part", owner=owner)
    assert again.reused and (again.id, again.object_key) == (first.id, first.object_key)
    assert charged(database_engine, actor["tenant_id"]) == base  # No double charge.
    with pytest.raises(storage_quota.ReservationConflict):
        reserve(workspace_settings, actor, b"different bytes", owner=owner)
    other_slot = reserve(workspace_settings, actor, b"different bytes", owner=owner, slot="1")
    assert other_slot.id != first.id and not other_slot.reused


def test_uninitialized_ledger_fails_closed(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    _, other = demo(client)
    for tenant in (actor["tenant_id"], other["tenant_id"]):
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(StorageUsage)
                .where(StorageUsage.tenant_id == UUID(tenant))
                .values(initialized=False)
            )
        try:
            # Own row unseeded, or another active tenant unseeded (global total incomplete).
            with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
                reserve(workspace_settings, actor, b"fictional")
        finally:
            with database_engine.begin() as connection:
                connection.execute(
                    sa.update(StorageUsage)
                    .where(StorageUsage.tenant_id == UUID(tenant))
                    .values(initialized=True)
                )
    reserve(workspace_settings, actor, b"fictional")


def test_runtime_cannot_insert_release_or_read_global_counters(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    _, other = demo(client)
    reservation = reserve(workspace_settings, actor, b"fictional")
    engine = storage_quota.reservation_engine(workspace_settings)
    with storage_quota.tenant_session(
        engine, UUID(other["tenant_id"]), UUID(other["user_id"])
    ) as session:
        # Tenant B sees neither tenant A's reservation nor its usage row.
        assert session.get(StorageReservation, reservation.id) is None
        assert session.scalar(sa.select(sa.func.count()).select_from(StorageUsage)) == 1
        for privilege, table in (
            ("SELECT", "storage_global_usage"),
            ("SELECT", "storage_limit"),
            ("INSERT", "storage_reservation"),
            ("DELETE", "storage_reservation"),
            ("UPDATE", "storage_usage"),
        ):
            assert not session.scalar(
                sa.text(f"SELECT has_table_privilege(current_user,'app.{table}','{privilege}')")
            )
        assert not session.scalar(
            sa.text(
                "SELECT has_function_privilege(current_user,"
                "'app.release_storage(uuid,uuid)','EXECUTE')"
            )
        )
    with (
        pytest.raises(sa.exc.DBAPIError),
        storage_quota.tenant_session(
            engine, UUID(actor["tenant_id"]), UUID(actor["user_id"])
        ) as session,
    ):
        # Runtime may link or plan, but never mark a charge released.
        session.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id == reservation.id)
            .values(state="released")
        )


def test_seed_charges_preexisting_orphans_then_only_raises_and_reports_drift(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.database.runtime import Database
    from app.platform_models import StorageSweep
    from app.storage import FileStore, storage
    from app.storage_admin import reconcile_tenant

    _, actor = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    store = storage(workspace_settings)
    assert isinstance(store, FileStore)
    # Simulate a tenant that predates migration 0010, with a pre-ledger orphan
    # (no reservation, no business row) already on the private volume.
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageUsage)
            .where(StorageUsage.tenant_id == tenant)
            .values(initialized=False)
        )
    orphan = b"fictional pre-ledger orphan"
    store.put(f"tenants/{tenant}/documents/{uuid4()}/original", orphan, "application/pdf")
    with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
        reserve(workspace_settings, actor, b"blocked until seeded")
    db = Database(workspace_settings)
    try:
        seeded = reconcile_tenant(db, store, tenant, user)
        with store.write_lock():
            scan = store.scan_tenant(tenant)
        assert seeded.seeded and seeded.raised_bytes >= len(orphan)
        assert (seeded.drift_bytes, seeded.drift_entries) == (0, 0)
        assert charged(database_engine, tenant) == scan.byte_size  # Ledger equals retained bytes.
        assert usage(database_engine, tenant).entries == scan.entries
        assert usage(database_engine, tenant).initialized
        again = reconcile_tenant(db, store, tenant, user)
        assert (again.seeded, again.raised_bytes, again.raised_entries, again.drift_bytes) == (
            False,
            0,
            0,
            0,
        )
        late = b"fictional legacy write outside the ledger"
        store.put(f"tenants/{tenant}/artifacts/{uuid4()}/late.pdf", late, "application/pdf")
        raised = reconcile_tenant(db, store, tenant, user)
        assert (raised.raised_bytes, raised.drift_bytes) == (len(late), len(late))
        pending = reserve(workspace_settings, actor, b"reserved but never written")
        lower = reconcile_tenant(db, store, tenant, user)
        # Actual < ledger is reported, never used to lower a charge.
        assert (lower.raised_bytes, lower.drift_bytes) == (0, -pending.byte_size)
        assert charged(database_engine, tenant) == scan.byte_size + len(late) + pending.byte_size
    finally:
        db.close()
    with database_engine.connect() as connection:
        report = connection.execute(
            sa.select(StorageSweep).where(StorageSweep.tenant_id == tenant)
        ).one()
    assert (report.origin, report.drift_bytes) == ("operator", -pending.byte_size)
    assert report.reconciled_at is not None


def start_upload(client: TestClient, submission: str, body: bytes) -> str:
    result = post(
        client,
        f"/submissions/{submission}/uploads",
        {"filename": "fictional.pdf", "sha256": digest(body), "byte_size": len(body)},
    )
    assert result.status_code == 201, result.text
    return str(result.json()["id"])


def put_part(client: TestClient, upload_id: str, body: bytes, number: int = 0) -> Any:
    return client.put(
        f"/api/v1/uploads/{upload_id}/parts/{number}",
        content=body,
        headers={"X-CSRF-Token": client.cookies.get("cgl_csrf", "") or ""},
    )


def reservations(engine: Engine, owner: str | UUID) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(StorageReservation).where(StorageReservation.owner_id == UUID(str(owner)))
            )
        )


def test_concurrent_part_uploads_at_the_boundary_admit_exactly_what_fits(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    submission, actor = demo(client)
    body = b"fictional concurrent part"
    uploads = [start_upload(client, submission, body) for _ in range(3)]
    base = charged(database_engine, actor["tenant_id"])

    def concurrent_put(upload: str) -> int:
        # Independent clients run on independent event loops, as separate API
        # processes would; the shared session cookies keep the same agency scope.
        independent = TestClient(client.app, headers=dict(client.headers), cookies=client.cookies)
        return int(put_part(independent, upload, body).status_code)

    with (
        limits(database_engine, tenant_byte_limit=base + 2 * len(body)),
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        codes = list(pool.map(concurrent_put, uploads))
    assert sorted(codes) == [200, 200, 507]
    assert charged(database_engine, actor["tenant_id"]) == base + 2 * len(body)


def test_rolled_back_part_stays_charged_and_identical_retry_reuses_it(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission, actor = demo(client)
    body = b"fictional part written before a rollback"
    upload_id = start_upload(client, submission, body)
    base = charged(database_engine, actor["tenant_id"])

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Synthetic failure after the object write")

    monkeypatch.setattr(storage_quota, "link", broken)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        put_part(client, upload_id, body)
    monkeypatch.undo()
    [orphan] = reservations(database_engine, upload_id)
    assert orphan.state == "reserved"  # Durable write intent survives the rollback.
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)
    directory = workspace_settings.storage_path / orphan.object_key.rsplit("/", 1)[0]
    assert [path.name for path in directory.iterdir()] == [orphan.object_key.rsplit("/", 1)[1]]
    assert put_part(client, upload_id, body).status_code == 200
    [linked] = reservations(database_engine, upload_id)
    assert (linked.id, linked.state) == (orphan.id, "committed")
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)  # No double charge.
    assert len(list(directory.iterdir())) == 1
    with database_engine.connect() as connection:
        assert connection.scalar(
            sa.select(UploadPart.object_key).where(UploadPart.upload_id == UUID(upload_id))
        ) == (linked.object_key)


def test_aborted_expired_and_finalized_parts_stay_charged(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from test_upload_cleanup import fictional_pdf

    submission, actor = demo(client)
    tenant = actor["tenant_id"]
    body = b"fictional abandoned part"
    aborted = start_upload(client, submission, body)
    assert put_part(client, aborted, body).status_code == 200
    base = charged(database_engine, tenant)
    assert post(client, f"/uploads/{aborted}/abort", {}).status_code == 200
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(UploadSession)
            .where(UploadSession.id == UUID(aborted))
            .values(expires_at=now() - timedelta(days=3))
        )
    assert charged(database_engine, tenant) == base
    original = fictional_pdf(database_engine, workspace_settings, submission)
    finalized = start_upload(client, submission, original)
    assert put_part(client, finalized, original).status_code == 200
    assert post(client, f"/uploads/{finalized}/finalize", {}).status_code == 200
    # The finalized parts and the new original are both retained and charged.
    assert charged(database_engine, tenant) == base + 2 * len(original)
    assert {row.state for row in reservations(database_engine, aborted)} == {"committed"}


def queued_package(client: TestClient, configuration: Settings) -> tuple[str, str, dict[str, Any]]:
    from test_workspace import reviewed_workspace

    submission, actor = reviewed_workspace(client, configuration)
    version = client.get("/api/v1/submissions/" + submission).json()["submission"]["row_version"]
    generated = post(client, f"/submissions/{submission}/packages", {"expected_version": version})
    assert generated.status_code == 202, generated.text
    return submission, str(generated.json()["id"]), actor


def package_job(engine: Engine, package: str) -> Any:
    from app.domain.models import ProcessingJob

    with engine.connect() as connection:
        return connection.execute(
            sa.select(ProcessingJob).where(ProcessingJob.package_id == UUID(package))
        ).one()


def test_lease_lost_render_leaves_a_charged_unlinked_reservation(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker
    from app.domain.models import PackageArtifact, ProcessingJob

    _, package, actor = queued_package(client, workspace_settings)
    job = package_job(database_engine, package)
    base = charged(database_engine, actor["tenant_id"])
    from app.rendering import render as original

    def lease_lost(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        # Another worker claims a newer attempt after this render started.
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(ProcessingJob)
                .where(ProcessingJob.id == job.id)
                .values(attempts=ProcessingJob.attempts + 1)
            )
        return result

    monkeypatch.setattr("app.worker.render", lease_lost)
    worker.process_job(workspace_settings, UUID(actor["tenant_id"]), UUID(actor["user_id"]), job.id)
    rows = reservations(database_engine, package)
    assert len(rows) == 3 and {row.state for row in rows} == {"reserved"}
    assert {row.slot.split(":")[0] for row in rows} == {"1"}
    total = sum(row.byte_size for row in rows)
    assert total > 0 and charged(database_engine, actor["tenant_id"]) == base + total
    for row in rows:
        assert (workspace_settings.storage_path / row.object_key).stat().st_size == row.byte_size
    with database_engine.connect() as connection:
        assert not connection.scalar(
            sa.select(sa.func.count())
            .select_from(PackageArtifact)
            .where(PackageArtifact.package_id == UUID(package))
        )


def test_completed_render_links_once_and_redelivery_does_not_relink(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from test_workspace import process_pending

    from app.worker import process_job

    _, package, actor = queued_package(client, workspace_settings)
    process_pending(workspace_settings, actor)
    rows = reservations(database_engine, package)
    assert len(rows) == 3 and {row.state for row in rows} == {"committed"}
    before = charged(database_engine, actor["tenant_id"])
    job = package_job(database_engine, package)
    process_job(workspace_settings, UUID(actor["tenant_id"]), UUID(actor["user_id"]), job.id)
    assert len(reservations(database_engine, package)) == 3
    assert charged(database_engine, actor["tenant_id"]) == before
    reservation = storage_quota.Reservation(
        rows[0].id, rows[0].object_key, rows[0].expires_at, rows[0].byte_size, rows[0].sha256, False
    )
    with (
        pytest.raises(StorageCapacityError),
        storage_quota.tenant_session(
            storage_quota.reservation_engine(workspace_settings),
            UUID(actor["tenant_id"]),
            UUID(actor["user_id"]),
        ) as session,
    ):
        storage_quota.link(session, reservation)  # Already committed: never relinked.


def test_over_quota_render_fails_without_retry(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from test_workspace import process_pending

    _, package, actor = queued_package(client, workspace_settings)
    with limits(database_engine, tenant_byte_limit=charged(database_engine, actor["tenant_id"])):
        process_pending(workspace_settings, actor)
    job = package_job(database_engine, package)
    assert (job.status, job.error_code, job.attempts) == ("failed", "STORAGE_CAPACITY_REACHED", 1)
    assert client.get("/api/v1/packages/" + package).json()["package"]["status"] == "failed"
    assert reservations(database_engine, package) == []


def test_excluded_failed_and_quarantined_documents_stay_charged(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.domain.models import DocumentVersion

    submission, actor = demo(client)
    before = charged(database_engine, actor["tenant_id"])
    with database_engine.connect() as connection:
        documents = list(
            connection.execute(
                sa.select(DocumentVersion).where(DocumentVersion.submission_id == UUID(submission))
            )
        )
    assert documents and before >= sum(doc.byte_size for doc in documents)
    excluded = post(
        client,
        f"/documents/{documents[0].id}/exclude",
        {
            "reason": "Synthetic test: exclude a fictional source from review.",
            "expected_version": client.get(f"/api/v1/submissions/{submission}").json()[
                "submission"
            ]["row_version"],
        },
    )
    assert excluded.status_code == 200, excluded.text
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(DocumentVersion)
            .where(DocumentVersion.id == documents[-1].id)
            .values(processing_status="failed", scan_status="rejected")
        )
    assert charged(database_engine, actor["tenant_id"]) == before
    for document in documents:
        [row] = reservations(database_engine, document.id)
        assert (row.state, row.object_key, row.byte_size) == (
            "committed",
            document.object_key,
            document.byte_size,
        )


def crash_after_reserving(configuration: Settings, tenant: str, user: str) -> None:
    import os

    from app.storage import FileStore

    reservation = reserve(configuration, {"tenant_id": tenant, "user_id": user}, b"12345678")
    store = FileStore(configuration.storage_path)
    with store.write_lock():
        path = store.path(reservation.object_key)
        path.parent.mkdir(parents=True)
        with path.open("xb") as output:
            output.write(b"123")
            output.flush()
            os._exit(23)  # Die mid-write: only fictional bytes in disposable storage.


def test_crashed_writer_leaves_a_charged_reservation_and_partial_bytes(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    base = charged(database_engine, actor["tenant_id"])
    process = multiprocessing.get_context("spawn").Process(
        target=crash_after_reserving,
        args=(workspace_settings, actor["tenant_id"], actor["user_id"]),
    )
    process.start()
    process.join(timeout=60)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Synthetic crashing writer did not terminate")
    assert process.exitcode == 23
    assert charged(database_engine, actor["tenant_id"]) == base + 8
    with database_engine.connect() as connection:
        [row] = connection.execute(
            sa.select(StorageReservation).where(
                StorageReservation.tenant_id == UUID(actor["tenant_id"]),
                StorageReservation.purpose == "upload_part",
            )
        )
    assert row.state == "reserved"
    assert (workspace_settings.storage_path / row.object_key).read_bytes() == b"123"


def test_preexisting_over_budget_history_stays_readable(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.domain.models import DocumentVersion
    from app.storage import storage

    submission, actor = demo(client)
    body = b"fictional part over a lowered limit"
    upload_id = start_upload(client, submission, body)
    with limits(database_engine, tenant_byte_limit=1):
        rejected = put_part(client, upload_id, body)
        assert rejected.status_code == 507
        assert rejected.json()["detail"]["code"] == "STORAGE_CAPACITY_REACHED"
        with database_engine.connect() as connection:
            history = list(
                connection.execute(
                    sa.select(DocumentVersion).where(
                        DocumentVersion.submission_id == UUID(submission)
                    )
                )
            )
        for document in history:
            assert digest(storage(workspace_settings).get(document.object_key)) == document.sha256
    assert reservations(database_engine, upload_id) == []
    assert put_part(client, upload_id, body).status_code == 200


def test_report_only_sweep_honors_the_lease_and_never_deletes(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from app import storage_sweep
    from app.database.runtime import Database
    from app.platform_models import StorageSweep
    from app.storage import FileStore, storage

    _, actor = demo(client)
    _, other = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    store = storage(workspace_settings)
    assert isinstance(store, FileStore)
    # One reservation-backed orphan past grace (never written), one rolled-back upload
    # part that is reported but is not a candidate, and one legacy file.
    owner = uuid4()
    never = storage_quota.reserve(
        storage_quota.reservation_engine(workspace_settings),
        workspace_settings,
        tenant,
        user,
        purpose="document",
        owner_id=owner,
        slot="original",
        key=f"tenants/{tenant}/documents/{owner}/original",
        body=b"fictional never written",
    )
    part = reserve(workspace_settings, actor, b"fictional rolled-back part")
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id.in_([never.id, part.id]))
            .values(expires_at=now() - timedelta(days=3))
        )
    legacy = b"fictional legacy file"
    store.put(f"tenants/{tenant}/artifacts/{uuid4()}/legacy.pdf", legacy, "application/pdf")
    store.put(f"tenants/{other['tenant_id']}/artifacts/{uuid4()}/b.pdf", b"b", "application/pdf")
    before = {path: path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    db = Database(workspace_settings)
    try:
        first = storage_sweep.claim(workspace_settings, db, tenant, user)
        assert first == 1
        # A repeated broker delivery cannot claim a held lease.
        assert storage_sweep.claim(workspace_settings, db, tenant, user) is None
        scanned: list[str] = []
        original = os.scandir

        def recording(path: Any) -> Any:
            scanned.append(str(path))
            return original(path)

        monkeypatch.setattr("app.storage.os.scandir", recording)
        assert storage_sweep.report(db, store, tenant, user, first)
        monkeypatch.undo()
        tenant_root = str(store.root / "tenants" / str(tenant))
        assert scanned and all(path.startswith(tenant_root) for path in scanned)
        with database_engine.connect() as connection:
            row = connection.execute(
                sa.select(StorageSweep).where(StorageSweep.tenant_id == tenant)
            ).one()
        assert (row.orphan_candidates, row.expired_absent) == (1, 1)
        assert (row.unlinked_upload_parts, row.cleanup_planned) == (1, 0)
        assert (row.legacy_unreserved, row.legacy_unreserved_bytes) == (1, len(legacy))
        assert (row.origin, row.report_attempt, row.lease_until) == ("scheduler", 1, None)
        assert row.drift_bytes == len(legacy) - never.byte_size - part.byte_size
        # A fresh report makes a repeated delivery a no-op.
        assert not storage_sweep.sweep_tenant(workspace_settings, db, store, tenant, user)
        # A stale attempt cannot overwrite a newer report.
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(StorageSweep)
                .where(StorageSweep.tenant_id == tenant)
                .values(reported_at=now() - timedelta(days=1))
            )
        stale = storage_sweep.claim(workspace_settings, db, tenant, user)
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(StorageSweep)
                .where(StorageSweep.tenant_id == tenant)
                .values(lease_until=now() - timedelta(seconds=1))
            )
        newer = storage_sweep.claim(workspace_settings, db, tenant, user)
        assert stale == 2 and newer == 3
        assert storage_sweep.report(db, store, tenant, user, newer)
        assert not storage_sweep.report(db, store, tenant, user, stale)
        with database_engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(StorageSweep.report_attempt).where(StorageSweep.tenant_id == tenant)
                )
                == 3
            )
        unknown = store.root / "tenants" / str(uuid4())
        unknown.mkdir(parents=True)
        (unknown / "untouched").write_bytes(b"fictional unknown")
        assert storage_sweep.unknown_tenant_directories(db, store) == 1
    finally:
        db.close()
    after = {path: path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    assert {path: body for path, body in after.items() if path in before} == before
    assert state_of(database_engine, never.id) == "reserved"  # Report-only: nothing released.


def state_of(engine: Engine, reservation: UUID) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(
                sa.select(StorageReservation.state).where(StorageReservation.id == reservation)
            )
        )


def upload_document(client: TestClient, submission: str, body: bytes, key: str) -> Any:
    return client.post(
        f"/api/v1/submissions/{submission}/documents",
        files={"file": ("fictional-retry.pdf", body, "application/pdf")},
        headers={"X-CSRF-Token": client.cookies.get("cgl_csrf", "") or "", "Idempotency-Key": key},
    )


def test_retried_document_upload_after_rollback_reuses_its_reservation(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_upload_cleanup import fictional_pdf

    submission, actor = demo(client)
    body = fictional_pdf(database_engine, workspace_settings, submission)
    base = charged(database_engine, actor["tenant_id"])
    entries = usage(database_engine, actor["tenant_id"]).entries
    key = str(uuid4())

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Synthetic failure after the object write")

    monkeypatch.setattr(storage_quota, "link", broken)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        upload_document(client, submission, body, key)
    monkeypatch.undo()
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)
    charged_entries = usage(database_engine, actor["tenant_id"]).entries
    retried = upload_document(client, submission, body, key)
    assert retried.status_code == 202, retried.text
    # The retry binds to the same write intent and object, and charges nothing new.
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)
    assert usage(database_engine, actor["tenant_id"]).entries == charged_entries > entries
    [linked] = reservations(database_engine, retried.json()["id"])
    assert linked.state == "committed"
    assert (workspace_settings.storage_path / linked.object_key).read_bytes() == body
    # A replay of the committed request returns the same document.
    assert upload_document(client, submission, body, key).json()["id"] == retried.json()["id"]
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)


def test_retried_finalize_after_rollback_reuses_its_reservation(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_upload_cleanup import fictional_pdf

    submission, actor = demo(client)
    body = fictional_pdf(database_engine, workspace_settings, submission)
    upload_id = start_upload(client, submission, body)
    assert put_part(client, upload_id, body).status_code == 200
    base = charged(database_engine, actor["tenant_id"])
    original = storage_quota.link

    def broken(session: Any, reservation: storage_quota.Reservation) -> None:
        raise RuntimeError("Synthetic failure after the object write")

    monkeypatch.setattr(storage_quota, "link", broken)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        post(client, f"/uploads/{upload_id}/finalize", {})
    monkeypatch.setattr(storage_quota, "link", original)
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)
    finalized = post(client, f"/uploads/{upload_id}/finalize", {})
    assert finalized.status_code == 200, finalized.text
    assert charged(database_engine, actor["tenant_id"]) == base + len(body)  # No double charge.
    with database_engine.connect() as connection:
        documents = list(
            connection.execute(
                sa.select(StorageReservation)
                .where(StorageReservation.purpose == "document")
                .where(StorageReservation.tenant_id == UUID(actor["tenant_id"]))
                .where(StorageReservation.state == "reserved")
            )
        )
    assert documents == []  # No stranded write intent from the failed attempt.


def wait_for_blocked_backend(engine: Engine, holder_pid: int, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            waiting = connection.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_locks WHERE NOT granted"
                    " AND :holder = ANY(pg_blocking_pids(pid))"
                ),
                {"holder": holder_pid},
            )
            connection.rollback()
            if waiting:
                return
            time.sleep(0.02)
    pytest.fail("No backend ever waited on the held counter row")


def event_loop_runs_work(client: TestClient, seconds: float = 3) -> bool:
    """Whether the client's one event loop runs a task in time; never blocks the caller."""
    ran = threading.Event()

    async def mark() -> None:
        ran.set()

    portal = client.portal
    assert portal is not None
    threading.Thread(target=portal.call, args=(mark,), daemon=True).start()
    return ran.wait(seconds)


def test_part_waiting_on_a_counter_row_cannot_stall_the_shared_event_loop(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    # Finding 4: every request here shares the TestClient's single event loop, like one
    # uvicorn process. A request holding the counter row commits in its teardown, which
    # needs that same loop, so a part blocking the loop could never be released.
    submission, _ = demo(client)
    body = b"fictional part waiting on the global counter"
    upload_id = start_upload(client, submission, body)
    holder = database_engine.connect()
    transaction = holder.begin()
    try:
        holder_pid = holder.scalar(sa.text("SELECT pg_backend_pid()"))
        holder.execute(sa.select(StorageGlobalUsage).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            part = pool.submit(put_part, client, upload_id, body)
            try:
                wait_for_blocked_backend(database_engine, holder_pid)
                responsive = event_loop_runs_work(client)
            finally:
                transaction.commit()
            assert responsive, "A database wait blocked the shared event loop"
            assert part.result(timeout=20).status_code == 200
    finally:
        holder.close()


def test_part_lock_wait_is_bounded_and_fails_cleanly(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    submission, actor = demo(client)
    body = b"fictional part that gives up waiting"
    upload_id = start_upload(client, submission, body)
    base = charged(database_engine, actor["tenant_id"])
    holder = database_engine.connect()
    transaction = holder.begin()
    try:
        holder.execute(sa.select(StorageGlobalUsage).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            part = pool.submit(put_part, client, upload_id, body)
            try:
                refused = part.result(timeout=15)
            finally:
                transaction.commit()
        assert refused.status_code == 503, refused.text
        assert refused.json()["detail"]["code"] == "STORAGE_QUOTA_UNAVAILABLE"
    finally:
        holder.close()
    assert charged(database_engine, actor["tenant_id"]) == base
    assert reservations(database_engine, upload_id) == []
    assert put_part(client, upload_id, body).status_code == 200


def test_concurrent_sweepers_skip_a_locked_row_and_claim_one_lease(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app import storage_sweep
    from app.database.runtime import Database
    from app.platform_models import StorageSweep

    _, actor = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    db = Database(workspace_settings)
    try:
        with db.tenant(tenant, user) as session:
            assert storage_sweep.sweep_row(session, tenant) is not None  # Create the row.
        # A sweeper mid-claim holds the row: the next one must skip it, not wait for it.
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            db.tenant(tenant, user) as session,
        ):
            assert storage_sweep.sweep_row(session, tenant) is not None
            skipped = pool.submit(storage_sweep.claim, workspace_settings, db, tenant, user)
            assert skipped.result(timeout=5) is None
        # Sweepers racing on a free row: exactly one claims the lease.
        barrier = threading.Barrier(4)

        def racing_claim(_: int) -> int | None:
            barrier.wait(timeout=10)
            return storage_sweep.claim(workspace_settings, db, tenant, user)

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(racing_claim, range(4)))
        assert sorted(claim for claim in claims if claim is not None) == [1]
    finally:
        db.close()
    with database_engine.connect() as connection:
        row = connection.execute(
            sa.select(StorageSweep).where(StorageSweep.tenant_id == tenant)
        ).one()
    assert row.attempt == 1 and row.lease_until is not None


def test_other_tenants_cannot_read_a_sweep_report(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app import storage_sweep
    from app.database.runtime import Database
    from app.platform_models import StorageSweep

    _, actor = demo(client)
    _, other = demo(client)
    db = Database(workspace_settings)
    try:
        with db.tenant(UUID(actor["tenant_id"]), UUID(actor["user_id"])) as session:
            assert storage_sweep.sweep_row(session, UUID(actor["tenant_id"])) is not None
        with db.tenant(UUID(other["tenant_id"]), UUID(other["user_id"])) as session:
            assert (
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(StorageSweep)
                    .where(StorageSweep.tenant_id == UUID(actor["tenant_id"]))
                )
                == 0
            )
    finally:
        db.close()


def artifact_rows(engine: Engine, package: str) -> list[Any]:
    from app.domain.models import PackageArtifact

    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(PackageArtifact).where(PackageArtifact.package_id == UUID(package))
            )
        )


def test_two_workers_rendering_against_the_last_bytes_admit_at_most_the_limit(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rendering import render as original
    from app.worker import process_job

    submission, first, actor = queued_package(client, workspace_settings)
    version = client.get("/api/v1/submissions/" + submission).json()["submission"]["row_version"]
    generated = post(client, f"/submissions/{submission}/packages", {"expected_version": version})
    assert generated.status_code == 202, generated.text
    second = str(generated.json()["id"])
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    base = charged(database_engine, tenant)
    totals: list[int] = []

    def fit_one_render() -> None:
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(StorageLimit)
                .where(StorageLimit.id == 1)
                .values(tenant_byte_limit=base + max(totals))
            )

    # Both workers hold real claims and rendered bytes before either reserves anything.
    barrier = threading.Barrier(2, action=fit_one_render)

    def rendered_then_race(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        totals.append(sum(len(data) for data, _, _ in result[0].values()))
        barrier.wait(timeout=30)
        return result

    monkeypatch.setattr("app.worker.render", rendered_then_race)
    jobs = [package_job(database_engine, package).id for package in (first, second)]
    with limits(database_engine, tenant_byte_limit=2**40), ThreadPoolExecutor(2) as pool:
        for finished in [
            pool.submit(process_job, workspace_settings, tenant, user, job) for job in jobs
        ]:
            finished.result(timeout=60)
        limit = base + max(totals)
        assert charged(database_engine, tenant) <= limit
    results = [package_job(database_engine, package) for package in (first, second)]
    assert {row.status for row in results} <= {"completed", "failed"}
    failed = [row for row in results if row.status == "failed"]
    # Neither render can fit beside the other: at least one fails, without a retry.
    assert failed
    assert all((row.error_code, row.attempts) == ("STORAGE_CAPACITY_REACHED", 1) for row in failed)
    for package, row in zip((first, second), results, strict=True):
        linked = [item for item in reservations(database_engine, package)]
        artifacts = artifact_rows(database_engine, package)
        if row.status == "completed":
            assert len(artifacts) == 3 and {item.state for item in linked} == {"committed"}
        else:
            assert artifacts == [] and {item.state for item in linked} <= {"reserved"}
        for item in linked:
            path = workspace_settings.storage_path / item.object_key
            assert path.stat().st_size == item.byte_size


def test_render_over_quota_on_a_later_format_keeps_the_first_charged_and_links_nothing(
    client: TestClient,
    workspace_settings: Settings,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_workspace import process_pending

    from app.rendering import render as original

    _, package, actor = queued_package(client, workspace_settings)
    base = charged(database_engine, actor["tenant_id"])
    first_size: list[int] = []

    def admit_only_the_first_format(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        first_size.append(len(next(iter(result[0].values()))[0]))
        with database_engine.begin() as connection:
            connection.execute(
                sa.update(StorageLimit)
                .where(StorageLimit.id == 1)
                .values(tenant_byte_limit=base + first_size[0])
            )
        return result

    monkeypatch.setattr("app.worker.render", admit_only_the_first_format)
    with limits(database_engine, tenant_byte_limit=2**40):
        process_pending(workspace_settings, actor)
    job = package_job(database_engine, package)
    assert (job.status, job.error_code, job.attempts) == ("failed", "STORAGE_CAPACITY_REACHED", 1)
    [admitted] = reservations(database_engine, package)
    assert (admitted.state, admitted.byte_size) == ("reserved", first_size[0])
    assert charged(database_engine, actor["tenant_id"]) == base + first_size[0]
    assert (workspace_settings.storage_path / admitted.object_key).exists()
    assert artifact_rows(database_engine, package) == []


def test_reserve_rejects_forged_keys_and_other_members_as_creator(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    _, other = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    engine = storage_quota.reservation_engine(workspace_settings)
    base = charged(database_engine, tenant)

    def attempt(key: str, created_by: UUID | None = None, purpose: str = "upload_part") -> None:
        storage_quota.reserve(
            engine,
            workspace_settings,
            tenant,
            user,
            purpose=purpose,  # type: ignore[arg-type]
            owner_id=uuid4(),
            slot="0",
            key=key,
            body=b"fictional",
            created_by=created_by,
        )

    forged = (
        f"tenants/{other['tenant_id']}/uploads/{uuid4()}/0-part",  # Another tenant's tree.
        f"tenants/{tenant}/documents/{uuid4()}/original",  # Another purpose's tree.
        f"tenants/{tenant}/uploads/{uuid4()}/../../escape",
        "tenants/not-a-tenant/uploads/x/y",
    )
    for key in forged:
        with pytest.raises(sa.exc.DBAPIError, match="Invalid storage reservation"):
            attempt(key)
    valid = f"tenants/{tenant}/uploads/{uuid4()}/0-part"
    with pytest.raises(sa.exc.DBAPIError, match="Invalid storage reservation"):
        attempt(valid, UUID(other["membership_id"]))  # Another tenant's member.
    with pytest.raises(sa.exc.DBAPIError, match="Invalid storage reservation"):
        attempt(valid, uuid4())  # Nobody.
    assert charged(database_engine, tenant) == base
    # A different member of the same tenant is not this caller either.
    second_user, second_member = uuid4(), uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO app.app_user(id, identity_issuer, identity_subject, display_name)"
                " VALUES (:id, 'synthetic-test', :subject, 'Fictional colleague')"
            ),
            {"id": second_user, "subject": str(second_user)},
        )
        connection.execute(
            sa.text("INSERT INTO app.membership(id, tenant_id, user_id) VALUES (:id, :t, :u)"),
            {"id": second_member, "t": tenant, "u": second_user},
        )
    with pytest.raises(sa.exc.DBAPIError, match="Invalid storage reservation"):
        attempt(valid, second_member)
    attempt(valid, UUID(actor["membership_id"]))  # The caller's own membership is accepted.


def test_release_storage_refuses_retained_objects(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.domain.models import DocumentVersion

    submission, actor = demo(client)
    tenant = UUID(actor["tenant_id"])
    with database_engine.connect() as connection:
        [document, *_] = connection.execute(
            sa.select(DocumentVersion).where(DocumentVersion.submission_id == UUID(submission))
        ).all()
    [committed] = reservations(database_engine, document.id)
    assert committed.state == "committed"
    # A reserved row a retained document nevertheless references is refused too.
    stray = reserve_document(workspace_settings, actor, b"fictional stray reference")
    with database_engine.begin() as connection:
        connection.execute(
            sa.insert(DocumentVersion).values(
                id=uuid4(),
                tenant_id=tenant,
                submission_id=UUID(submission),
                filename="fictional-reference.pdf",
                media_type="application/pdf",
                sha256=stray.sha256,
                byte_size=stray.byte_size,
                object_key=stray.object_key,
                uploaded_by=UUID(actor["user_id"]),
            )
        )
    before = usage(database_engine, tenant)
    for reservation in (committed.id, stray.id):
        # Run as the function owner: runtime cannot reach this internal function at all.
        with (
            database_engine.begin() as connection,
            pytest.raises(sa.exc.DBAPIError, match="Retained objects cannot be released"),
        ):
            connection.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
            )
            connection.execute(
                sa.text("SELECT set_config('app.user_id', :u, true)"), {"u": actor["user_id"]}
            )
            connection.execute(
                sa.text("SELECT app.release_storage(:id, :ref)"),
                {"id": reservation, "ref": uuid4()},
            )
    after = usage(database_engine, tenant)
    assert (after.reserved_bytes, after.entries) == (before.reserved_bytes, before.entries)
    assert state_of(database_engine, committed.id) == "committed"


def reserve_document(
    configuration: Settings, actor: dict[str, Any], body: bytes, *, owner: UUID | None = None
) -> storage_quota.Reservation:
    owner = owner or uuid4()
    return storage_quota.reserve(
        storage_quota.reservation_engine(configuration),
        configuration,
        UUID(actor["tenant_id"]),
        UUID(actor["user_id"]),
        purpose="document",
        owner_id=owner,
        slot="original",
        key=f"tenants/{actor['tenant_id']}/documents/{owner}/original",
        body=body,
    )


def test_reserve_on_the_s3_adapter_fails_before_any_charge(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    before = usage(database_engine, actor["tenant_id"])
    configuration = workspace_settings.model_copy(update={"storage_backend": "s3"})
    owner = uuid4()
    with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
        storage_quota.reserve(
            storage_quota.reservation_engine(workspace_settings),
            configuration,
            UUID(actor["tenant_id"]),
            UUID(actor["user_id"]),
            purpose="document",
            owner_id=owner,
            slot="original",
            key=f"tenants/{actor['tenant_id']}/documents/{owner}/original",
            body=b"fictional",
        )
    after = usage(database_engine, actor["tenant_id"])
    assert (after.reserved_bytes, after.entries) == (before.reserved_bytes, before.entries)
    assert reservations(database_engine, owner) == []


def test_entry_limit_also_bounds_empty_objects_and_directories(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.storage import storage

    _, actor = demo(client)
    tenant = actor["tenant_id"]
    used = global_usage(database_engine).entries
    first_owner, second_owner = uuid4(), uuid4()
    # The first empty document needs two entries: its object and its directory.
    with limits(database_engine, entry_limit=used + 1), pytest.raises(StorageCapacityError):
        reserve_document(workspace_settings, actor, b"", owner=first_owner)
    with limits(database_engine, entry_limit=used + 3):
        admitted = reserve_document(workspace_settings, actor, b"", owner=first_owner)
        assert global_usage(database_engine).entries == used + 2
        storage_quota.write(storage(workspace_settings), admitted, b"", "application/pdf")
        before = set(workspace_settings.storage_path.rglob("*"))
        # One entry is left, but a second object also needs a new directory.
        with pytest.raises(StorageCapacityError, match="^STORAGE_CAPACITY_REACHED$"):
            reserve_document(workspace_settings, actor, b"", owner=second_owner)
    assert set(workspace_settings.storage_path.rglob("*")) == before
    assert reservations(database_engine, second_owner) == []
    assert usage(database_engine, tenant).entries == usage_after_scan(workspace_settings, tenant)


def usage_after_scan(configuration: Settings, tenant: str) -> int:
    from app.storage import FileStore

    return FileStore(configuration.storage_path).scan_tenant(UUID(tenant)).entries


def test_entries_match_the_scan_so_reconcile_never_ratchets(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.database.runtime import Database
    from app.storage import FileStore, storage
    from app.storage_admin import reconcile_tenant

    # Finding 5: a new tenant used to be raised by its directories on every first reconcile.
    submission, actor = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    store = storage(workspace_settings)
    assert isinstance(store, FileStore)
    body = b"fictional part sharing one upload directory"
    upload_id = start_upload(client, submission, body)
    assert put_part(client, upload_id, body).status_code == 200
    assert usage(database_engine, tenant).entries == store.scan_tenant(tenant).entries
    db = Database(workspace_settings)
    try:
        reconciled = reconcile_tenant(db, store, tenant, user)
        assert (reconciled.raised_entries, reconciled.drift_entries) == (0, 0)
        assert (reconciled.raised_bytes, reconciled.drift_bytes) == (0, 0)
    finally:
        db.close()
    # A reservation charges top-level directories once and releases its exact charge.
    with database_engine.connect() as connection:
        directories = connection.scalar(
            sa.select(StorageUsage.charged_directories).where(StorageUsage.tenant_id == tenant)
        )
    assert sorted(directories or []) == ["documents", "tenant", "uploads"]


def test_stable_document_key_is_never_rebound_and_a_lapsed_retry_renews(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.upload_cleanup import WRITE_MARGIN

    _, actor = demo(client)
    owner = uuid4()
    first = reserve_document(workspace_settings, actor, b"fictional bytes", owner=owner)
    base = charged(database_engine, actor["tenant_id"])
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id == first.id)
            .values(expires_at=now() + WRITE_MARGIN - timedelta(seconds=1))
        )
    # The same logical write keeps its charge and key, with a usable TTL again.
    renewed = reserve_document(workspace_settings, actor, b"fictional bytes", owner=owner)
    assert (
        renewed.reused
        and renewed.id == first.id
        and renewed.expires_at > first.expires_at - (timedelta(minutes=1))
    )
    assert charged(database_engine, actor["tenant_id"]) == base
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id == first.id)
            .values(expires_at=now() - timedelta(days=3), state="cleanup_planned")
        )
    # A planned key cannot be rebound by a retry, whatever its bytes.
    with pytest.raises(storage_quota.ReservationConflict):
        reserve_document(workspace_settings, actor, b"fictional bytes", owner=owner)
    assert charged(database_engine, actor["tenant_id"]) == base


def hold_tenant_row(
    engine: Engine, tenant: UUID, clause: str
) -> tuple[Connection, sa.engine.Transaction]:
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(
        sa.text(f"SELECT id FROM app.tenant WHERE id = :id {clause}"), {"id": tenant}
    )
    return connection, transaction


def test_foreign_keys_bind_reservations_without_waiting_on_the_capacity_lock(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    _, actor = demo(client)
    tenant = UUID(actor["tenant_id"])
    membership = UUID(actor["membership_id"])
    engine = storage_quota.reservation_engine(workspace_settings)

    def reserve_as_member() -> storage_quota.Reservation:
        owner = uuid4()
        return storage_quota.reserve(
            engine,
            workspace_settings,
            tenant,
            UUID(actor["user_id"]),
            purpose="upload_part",
            owner_id=owner,
            slot="0",
            key=part_key(actor["tenant_id"], owner),
            body=b"fictional",
            created_by=membership,
        )

    # The lock require_capacity now takes leaves the foreign key check free to proceed.
    holder, transaction = hold_tenant_row(database_engine, tenant, "FOR NO KEY UPDATE")
    started = time.monotonic()
    try:
        reservation = reserve_as_member()
    finally:
        transaction.rollback()
        holder.close()
    assert time.monotonic() - started < 3
    # FOR UPDATE, the lock it used to take, would wait on the caller's own request.
    holder, transaction = hold_tenant_row(database_engine, tenant, "FOR UPDATE")
    try:
        with pytest.raises(StorageCapacityError, match="^STORAGE_QUOTA_UNAVAILABLE$"):
            reserve_as_member()
    finally:
        transaction.rollback()
        holder.close()
    # Migrator and maintenance writes are bound as well.
    values = {
        "purpose": "upload_part",
        "owner_id": uuid4(),
        "slot": "0",
        "object_key": f"tenants/{tenant}/uploads/{uuid4()}/0-maintenance",
        "byte_size": 0,
        "sha256": "0" * 64,
        "request_hash": "0" * 64,
        "expires_at": now(),
    }
    with database_engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
        connection.execute(
            sa.insert(StorageReservation).values(id=uuid4(), tenant_id=uuid4(), **values)
        )
    with database_engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
        connection.execute(
            sa.insert(StorageReservation).values(
                id=uuid4(), tenant_id=tenant, created_by=uuid4(), **values
            )
        )
    # So are later deletes: nothing can leave a dangling reference behind.
    for statement, identifier in (
        ("DELETE FROM app.membership WHERE id = :id", membership),
        ("DELETE FROM app.tenant WHERE id = :id", tenant),
    ):
        with database_engine.begin() as connection, pytest.raises(sa.exc.DBAPIError):
            connection.execute(sa.text(statement), {"id": identifier})
    assert state_of(database_engine, reservation.id) == "reserved"


def test_part_waiting_on_a_tenant_row_is_bounded_and_fails_cleanly(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    submission, actor = demo(client)
    body = b"fictional part behind a held tenant row"
    upload_id = start_upload(client, submission, body)
    holder, transaction = hold_tenant_row(
        database_engine, UUID(actor["tenant_id"]), "FOR NO KEY UPDATE"
    )
    try:
        refused = put_part(client, upload_id, body)
    finally:
        transaction.rollback()
        holder.close()
    assert refused.status_code == 503, refused.text
    assert refused.json()["detail"]["code"] == "STORAGE_QUOTA_UNAVAILABLE"
    assert put_part(client, upload_id, body).status_code == 200


def test_operator_role_is_the_only_reconciler_and_reaches_nothing_else(
    client: TestClient, workspace_settings: Settings, database_engine: Engine
) -> None:
    from app.database.runtime import Database
    from app.storage import TenantScan
    from app.storage_admin import operator_engine

    _, actor = demo(client)
    tenant, user = UUID(actor["tenant_id"]), UUID(actor["user_id"])
    before = charged(database_engine, tenant)
    forged = TenantScan(byte_size=2**40, entries=1)
    db = Database(workspace_settings)
    try:
        # A tenant-scoped runtime session cannot submit a measurement at all.
        with (
            pytest.raises(sa.exc.DBAPIError, match="permission denied"),
            db.tenant(tenant, user) as session,
        ):
            storage_quota.reconcile(session, forged)
        with db.tenant(tenant, user) as session:
            assert not session.scalar(
                sa.text(
                    "SELECT has_function_privilege(current_user,"
                    "'app.reconcile_storage(bigint,bigint,text[],jsonb)','EXECUTE')"
                )
            )
        engine = operator_engine(db)
        try:
            with storage_quota.tenant_session(engine, tenant, user) as session:
                for table in (
                    "storage_usage",
                    "storage_reservation",
                    "storage_legacy_part",
                    "tenant",
                ):
                    for privilege in ("SELECT", "UPDATE"):
                        assert not session.scalar(
                            sa.text(
                                "SELECT has_table_privilege(current_user,"
                                f"'app.{table}','{privilege}')"
                            )
                        )
                for function in (
                    "reserve_storage(uuid,text,uuid,text,text,bigint,text,text,integer,integer,uuid)",
                    "release_upload_cleanup(uuid,jsonb)",
                    "release_object_cleanup(uuid,jsonb)",
                    "plan_object_cleanup(uuid,uuid,text,jsonb)",
                    "plan_upload_cleanup(uuid,uuid,uuid,text,jsonb)",
                ):
                    assert not session.scalar(
                        sa.text(
                            f"SELECT has_function_privilege(current_user,'app.{function}','EXECUTE')"
                        )
                    )
        finally:
            engine.dispose()
    finally:
        db.close()
    assert charged(database_engine, tenant) == before

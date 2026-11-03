"""Reservation-ledger admission, write sites and reconciliation on disposable databases."""

import multiprocessing
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
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
    assert after.entries == before.entries + 1
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
    from concurrent.futures import ThreadPoolExecutor

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
        {"reason": "Synthetic test: exclude a fictional source from review."},
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
    # One reservation-backed orphan past grace (never written), one legacy file.
    never = reserve(workspace_settings, actor, b"fictional never written")
    with database_engine.begin() as connection:
        connection.execute(
            sa.update(StorageReservation)
            .where(StorageReservation.id == never.id)
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
        # A concurrent sweeper (or a repeated broker delivery) cannot claim a held lease.
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
        assert (row.legacy_unreserved, row.legacy_unreserved_bytes) == (1, len(legacy))
        assert (row.origin, row.report_attempt, row.lease_until) == ("scheduler", 1, None)
        assert row.drift_bytes == len(legacy) - never.byte_size
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

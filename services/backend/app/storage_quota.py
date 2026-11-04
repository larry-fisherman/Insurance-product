"""PostgreSQL storage reservation ledger (ADR 0008).

Every object write is reserve -> write -> link. ``reserve`` commits its own short
transaction before the write, so a rolled-back business transaction leaves a
charged reservation (a durable write intent). ``link`` runs inside the business
transaction and only flips the reservation row. Counters move only inside the
definer functions. Callers supply the session scope and a server-generated key; the
definer rebinds the key to the session's tenant, and limits are never accepted.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NoReturn
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.common import canonical_json, digest, now
from app.config import Settings
from app.platform_models import StorageReservation
from app.storage import StorageCapacityError, Store, TenantScan
from app.upload_cleanup import RESERVATION_TTL, WRITE_MARGIN

Purpose = Literal["upload_part", "document", "artifact"]
_engines: dict[str, Engine] = {}
_engine_lock = threading.Lock()
LOCK_TIMEOUT = "5s"  # Matches the SET clause on the storage definer functions (migration 0011).
LOCK_FAILURES = {"55P03", "40P01"}  # lock_not_available, deadlock_detected.


class ReservationConflict(Exception):
    """The same logical write slot is already reserved for different bytes."""


@dataclass(frozen=True)
class Reservation:
    id: UUID
    object_key: str
    expires_at: datetime
    byte_size: int
    sha256: str
    reused: bool


def request_hash(purpose: Purpose, owner_id: UUID, slot: str, byte_size: int, sha256: str) -> str:
    return digest(
        canonical_json(
            {
                "purpose": purpose,
                "owner": owner_id,
                "slot": slot,
                "byte_size": byte_size,
                "sha256": sha256,
            }
        )
    )


def reservation_engine(configuration: Settings) -> Engine:
    """A small dedicated runtime pool, so reservations never wait on business connections."""
    if configuration.runtime_database_url is None:
        raise ValueError("Workspace database configuration is required")
    url = configuration.runtime_database_url.get_secret_value()
    with _engine_lock:
        engine = _engines.get(url)
        if engine is None:
            engine = sa.create_engine(
                url,
                pool_pre_ping=True,
                pool_size=2,
                max_overflow=3,
                pool_timeout=10,
                hide_parameters=True,
            )
            _engines[url] = engine
    return engine


def reraise_lock_failure(error: DBAPIError) -> NoReturn:
    """A bounded lock wait gave up: fail closed with the existing retryable storage code."""
    if getattr(error.orig, "sqlstate", None) in LOCK_FAILURES:
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE") from None
    raise error


@contextmanager
def bounded_locks(session: Session) -> Iterator[None]:
    """Bound every lock wait in this block, restoring the caller's setting afterwards."""
    previous = session.scalar(sa.text("SELECT current_setting('lock_timeout')"))
    session.execute(
        sa.text("SELECT set_config('lock_timeout', :value, true)"), {"value": LOCK_TIMEOUT}
    )
    try:
        yield
    except DBAPIError as error:
        reraise_lock_failure(error)
    session.execute(sa.text("SELECT set_config('lock_timeout', :value, true)"), {"value": previous})


@contextmanager
def tenant_session(engine: Engine, tenant_id: UUID, user_id: UUID) -> Iterator[Session]:
    # Same transaction-local scope as Database.tenant(); commits on exit.
    with Session(engine, expire_on_commit=False) as session, session.begin():
        session.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        session.execute(
            sa.text("SELECT set_config('app.user_id', :user, true)"), {"user": str(user_id)}
        )
        yield session


def reserve(
    engine: Engine,
    configuration: Settings,
    tenant_id: UUID,
    user_id: UUID,
    *,
    purpose: Purpose,
    owner_id: UUID,
    slot: str,
    key: str,
    body: bytes,
    created_by: UUID | None = None,
) -> Reservation:
    if configuration.storage_backend != "filesystem":
        # The S3 gate stays closed: fail before any charge or provider call.
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")
    sha = digest(body)
    try:
        with tenant_session(engine, tenant_id, user_id) as session:
            row = session.execute(
                sa.text(
                    "SELECT * FROM app.reserve_storage(:id, :purpose, :owner, :slot, :key, :bytes,"
                    " :sha, :hash, :ttl, :margin, :created_by)"
                ),
                {
                    "id": uuid4(),
                    "purpose": purpose,
                    "owner": owner_id,
                    "slot": slot,
                    "key": key,
                    "bytes": len(body),
                    "sha": sha,
                    "hash": request_hash(purpose, owner_id, slot, len(body), sha),
                    "ttl": int(RESERVATION_TTL.total_seconds()),
                    "margin": int(WRITE_MARGIN.total_seconds()),
                    "created_by": created_by,
                },
            ).one()
    except DBAPIError as error:
        reraise_lock_failure(error)
    if row.outcome == "capacity":
        raise StorageCapacityError("STORAGE_CAPACITY_REACHED")
    if row.outcome == "unavailable":
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")
    if row.outcome == "conflict":
        raise ReservationConflict()
    return Reservation(
        id=row.reserved_id,
        object_key=row.reserved_key,
        expires_at=row.reserved_until,
        byte_size=len(body),
        sha256=sha,
        reused=row.outcome == "reused",
    )


def write(store: Store, reservation: Reservation, body: bytes, content_type: str) -> str | None:
    """Write the reserved bytes; refuse once the reservation is within the safety margin."""
    deadline = reservation.expires_at - WRITE_MARGIN
    if now() >= deadline:
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")
    if len(body) != reservation.byte_size or digest(body) != reservation.sha256:
        raise ValueError("Reserved bytes differ from the write")
    try:
        return store.put(reservation.object_key, body, content_type, not_after=deadline)
    except FileExistsError:
        # A retry reusing its reservation may find its own earlier, identical write.
        if reservation.reused and store.probe(reservation.object_key) == (
            reservation.byte_size,
            reservation.sha256,
        ):
            return None
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE") from None


def link(session: Session, reservation: Reservation) -> None:
    """Commit the reservation inside the business transaction that records the object."""
    with bounded_locks(session):
        row = session.scalar(
            sa.select(StorageReservation)
            .where(StorageReservation.id == reservation.id)
            .with_for_update()
        )
    if (
        row is None
        or row.state != "reserved"
        or row.object_key != reservation.object_key
        or row.expires_at <= now()
    ):
        # Expired, planned for cleanup or otherwise changed: never race a cleanup.
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")
    row.state, row.linked_at = "committed", now()
    session.flush()


def plan_upload_cleanup(
    session: Session,
    plan_id: UUID,
    upload_id: UUID,
    requested_by: UUID,
    reason: str,
    objects: list[dict[str, Any]],
) -> None:
    """Commit an ADR 0007 intent; the definer rechecks hold, expiry and references."""
    try:
        session.execute(
            sa.text(
                "SELECT app.plan_upload_cleanup(:id, :upload, :member, :reason,"
                " CAST(:objects AS jsonb))"
            ),
            {
                "id": plan_id,
                "upload": upload_id,
                "member": requested_by,
                "reason": reason,
                "objects": canonical_json(objects),
            },
        )
    except DBAPIError as error:
        reraise_lock_failure(error)


def plan_object_cleanup(
    session: Session,
    plan_id: UUID,
    requested_by: UUID,
    reason: str,
    objects: list[dict[str, Any]],
) -> None:
    """Commit an orphan intent; the definer decides eligibility from the reservation rows."""
    try:
        session.execute(
            sa.text(
                "SELECT app.plan_object_cleanup(:id, :member, :reason, CAST(:objects AS jsonb))"
            ),
            {
                "id": plan_id,
                "member": requested_by,
                "reason": reason,
                "objects": canonical_json(objects),
            },
        )
    except DBAPIError as error:
        reraise_lock_failure(error)


def release_upload_cleanup(
    session: Session, cleanup_id: UUID, summary: dict[str, Any]
) -> tuple[int, int]:
    """Complete an ADR 0007 intent and release its parts exactly once (one definer call)."""
    try:
        row = session.execute(
            sa.text("SELECT * FROM app.release_upload_cleanup(:id, CAST(:summary AS jsonb))"),
            {"id": cleanup_id, "summary": canonical_json(summary)},
        ).one()
    except DBAPIError as error:
        reraise_lock_failure(error)
    return int(row.released_bytes), int(row.legacy_bytes)


def release_object_cleanup(session: Session, cleanup_id: UUID, summary: dict[str, Any]) -> int:
    """Complete an orphan intent and release its reservations exactly once."""
    try:
        return int(
            session.scalar(
                sa.text("SELECT app.release_object_cleanup(:id, CAST(:summary AS jsonb))"),
                {"id": cleanup_id, "summary": canonical_json(summary)},
            )
        )
    except DBAPIError as error:
        reraise_lock_failure(error)


def reconcile(session: Session, scan: TenantScan) -> Any:
    """Seed or raise a tenant's baseline; needs the operator role, runtime cannot call it."""
    legacy_parts = [
        {"object_key": key, "byte_size": size}
        for key, size in scan.files.items()
        if key.split("/")[2] == "uploads"
    ]
    return session.execute(
        sa.text(
            "SELECT * FROM app.reconcile_storage(:bytes, :entries, :directories,"
            " CAST(:legacy_parts AS jsonb))"
        ),
        {
            "bytes": scan.byte_size,
            "entries": scan.entries,
            "directories": scan.directories,
            "legacy_parts": canonical_json(legacy_parts),
        },
    ).one()

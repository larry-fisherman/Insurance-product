"""Explicit tenant-admin cleanup of reservation-backed orphan objects (ADR 0008).

A two-request plan/execute flow like ADR 0007. It accepts IDs only, never keys or
paths. Only ``reserved`` (never linked) document and artifact reservations past
expiry plus grace whose key no retained row references are candidates. Upload parts
are out of scope entirely: ADR 0007, which honors preservation holds, is the only
path that deletes them, and a rolled-back part with no upload session stays on disk
and is only reported. Pre-ledger files without a reservation are never eligible.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app import storage_quota
from app.common import audit, fail, now
from app.domain.models import DocumentVersion, PackageArtifact
from app.identity import Input, Scope, Scoped, require_recent
from app.platform_models import ObjectCleanup, StorageReservation, UploadPart
from app.storage import FileStore, storage
from app.temporary_storage import (
    MAX_CLEANUP_OBJECTS,
    ReservedObject,
    TemporaryStorageError,
    inventory_reserved,
    remove_reserved,
)
from app.upload_cleanup import ORPHAN_GRACE, ORPHAN_POLICY_VERSION
from app.workflow import get, idempotent

router = APIRouter(prefix="/api/v1/storage/orphan-cleanup", tags=["storage maintenance"])


class OrphanCleanupRequest(Input):
    reason: str = Field(min_length=10, max_length=500)


class OrphanCleanupResponse(BaseModel):
    id: UUID
    policy_version: str
    object_count: int
    present_count: int
    byte_size: int
    completed_at: datetime | None
    completion_summary: dict[str, int]


def response(plan: ObjectCleanup) -> OrphanCleanupResponse:
    return OrphanCleanupResponse(
        id=plan.id,
        policy_version=plan.policy_version,
        object_count=len(plan.objects),
        present_count=sum(1 for item in plan.objects if item["present"]),
        byte_size=sum(int(item["byte_size"]) for item in plan.objects),
        completed_at=plan.completed_at,
        completion_summary=plan.completion_summary,
    )


def unreferenced() -> sa.ColumnElement[bool]:
    # Retained documents/artifacts (any status) and recorded parts are permanent references.
    return sa.and_(
        *(
            ~sa.exists().where(model.object_key == StorageReservation.object_key)
            for model in (DocumentVersion, PackageArtifact, UploadPart)
        )
    )


def candidates(at: datetime) -> sa.Select[StorageReservation]:
    """Policy storage-orphan-cleanup-1, within the caller's RLS tenant scope."""
    return (
        sa.select(StorageReservation)
        .where(
            StorageReservation.state == "reserved",
            StorageReservation.purpose != "upload_part",
            StorageReservation.expires_at < at - ORPHAN_GRACE,
            unreferenced(),
        )
        .order_by(StorageReservation.expires_at, StorageReservation.id)
    )


def unlinked_upload_parts(at: datetime) -> sa.Select[StorageReservation]:
    """Report-only: rolled-back parts past grace, which this flow never touches."""
    return sa.select(StorageReservation).where(
        StorageReservation.state == "reserved",
        StorageReservation.purpose == "upload_part",
        StorageReservation.expires_at < at - ORPHAN_GRACE,
        unreferenced(),
    )


def authorize(scope: Scope) -> FileStore:
    scope.require("retention.manage")
    require_recent(scope.actor)
    if scope.configuration.storage_backend != "filesystem":
        fail(409, "ORPHAN_CLEANUP_UNSUPPORTED", "Cleanup requires the local filesystem adapter.")
    store = storage(scope.configuration)
    assert isinstance(store, FileStore)
    return store


@router.post("/plan", response_model=OrphanCleanupResponse, status_code=201)
def prepare(
    payload: OrphanCleanupRequest,
    scope: Scoped,
    idempotency_key: str | None = Header(default=None),
) -> OrphanCleanupResponse:
    store = authorize(scope)
    object_id, exists = idempotent(
        scope, "storage.orphan_cleanup", idempotency_key, payload.model_dump()
    )
    if exists:
        return response(get(scope.session, ObjectCleanup, object_id))
    # Lock order: reservation rows, then the storage lock (inside inventory).
    rows: list[StorageReservation] = list(
        scope.session.scalars(
            candidates(now()).limit(MAX_CLEANUP_OBJECTS).with_for_update(skip_locked=True)
        )
    )
    if not rows:
        fail(409, "ORPHAN_CLEANUP_EMPTY", "There are no eligible orphaned objects.")
    # An unsafe object stays reserved and untouched, without blocking the rest of the batch.
    objects, unsafe = inventory_reserved(
        store, [(row.object_key, row.id, row.byte_size, row.expires_at) for row in rows]
    )
    if not objects:
        fail(409, "ORPHAN_CLEANUP_UNSAFE", "Objects changed or cannot be safely inspected.")
    # The definer moves the reservations to cleanup_planned, so a late link() fails instead
    # of racing deletion, and records the intent. Runtime can do neither directly.
    storage_quota.plan_object_cleanup(
        scope.session,
        object_id,
        scope.membership_id,
        payload.reason,
        [item.model_dump(mode="json") for item in objects],
    )
    plan = get(scope.session, ObjectCleanup, object_id)
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "storage.orphan_cleanup_planned",
        "object_cleanup",
        plan.id,
        {"objects": len(objects), "skipped_unsafe": unsafe, "policy": ORPHAN_POLICY_VERSION},
    )
    return response(plan)


@router.get("/{plan_id}", response_model=OrphanCleanupResponse)
def status(plan_id: UUID, scope: Scoped) -> OrphanCleanupResponse:
    authorize(scope)
    return response(get(scope.session, ObjectCleanup, plan_id))


@router.post("/{plan_id}/execute", response_model=OrphanCleanupResponse)
def execute(plan_id: UUID, scope: Scoped) -> OrphanCleanupResponse:
    store = authorize(scope)
    plan = get(scope.session, ObjectCleanup, plan_id)
    if plan.completed_at is not None:
        return response(plan)
    if plan.policy_version != ORPHAN_POLICY_VERSION:
        fail(409, "ORPHAN_CLEANUP_POLICY", "This cleanup plan requires its original policy.")
    objects = [ReservedObject.model_validate(item) for item in plan.objects]
    # Runtime cannot update (so cannot row-lock) the immutable intent. Concurrent
    # executions serialize on the planned reservation rows instead; the definer
    # locks the intent last and completes it only once.
    rows = {
        row.id: row
        for row in scope.session.scalars(
            sa.select(StorageReservation)
            .where(StorageReservation.id.in_([item.reservation_id for item in objects]))
            .order_by(StorageReservation.id)
            .with_for_update()
        )
    }
    scope.session.refresh(plan)
    if plan.completed_at is not None:
        return response(plan)
    pending = []
    for item in objects:
        row = rows.get(item.reservation_id)
        if row is None or row.state not in {"cleanup_planned", "released"}:
            fail(409, "ORPHAN_CLEANUP_CHANGED", "The planned reservations changed.")
        if row.state == "released":
            continue  # Already released by an ADR 0007 upload cleanup of the same part.
        if row.purpose == "upload_part":
            # Only a plan from before migration 0011 can list a part: ADR 0007 owns it.
            fail(409, "ORPHAN_CLEANUP_CHANGED", "The planned reservations changed.")
        if row.expires_at >= now() - ORPHAN_GRACE:
            fail(409, "ORPHAN_CLEANUP_NOT_READY", "Wait until the orphan grace period passes.")
        pending.append((row.object_key, row.byte_size, row.expires_at, item))
    if pending:
        keys = [key for key, *_ in pending]
        for model in (DocumentVersion, PackageArtifact, UploadPart):
            if scope.session.scalar(sa.select(model.id).where(model.object_key.in_(keys)).limit(1)):
                fail(
                    409,
                    "ORPHAN_CLEANUP_REFERENCED",
                    "A retained record references these bytes.",
                )
        try:
            summary = remove_reserved(store, pending)
        except (OSError, ValueError, TemporaryStorageError):
            fail(
                409,
                "ORPHAN_CLEANUP_UNSAFE",
                "Cleanup is pending; inspect private storage before retrying.",
            )
    else:
        summary = {
            "removed_objects": 0,
            "removed_bytes": 0,
            "already_absent_objects": 0,
            "planned_absent_objects": 0,
        }
    summary["already_released_objects"] = len(objects) - len(pending)
    # One definer call completes the intent and releases each reservation exactly once.
    released = storage_quota.release_object_cleanup(scope.session, plan.id, summary)
    scope.session.refresh(plan)
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "storage.orphan_cleanup_completed",
        "object_cleanup",
        plan.id,
        {**summary, "released_bytes": released},
    )
    return response(plan)

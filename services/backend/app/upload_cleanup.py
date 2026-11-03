"""Explicit tenant-admin maintenance of expired local transfer bytes, with durable intent."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.common import audit, digest, fail, now
from app.domain.models import DocumentVersion, PackageArtifact
from app.identity import Input, Scope, Scoped, require_recent
from app.platform_models import UploadCleanup, UploadSession
from app.storage import FileStore, storage
from app.temporary_storage import TemporaryObject, TemporaryStorageError, inventory, remove_planned
from app.workflow import get, idempotent

router = APIRouter(prefix="/api/v1/uploads", tags=["temporary upload maintenance"])
POLICY_VERSION = "local-upload-cleanup-1"
GRACE = timedelta(hours=24)


class CleanupRequest(Input):
    reason: str = Field(min_length=10, max_length=500)


class CleanupHold(CleanupRequest):
    held: bool
    expected_version: int = Field(ge=1)


class HoldResponse(BaseModel):
    upload_id: UUID
    held: bool
    version: int


class CleanupResponse(BaseModel):
    id: UUID
    upload_id: UUID
    policy_version: str
    object_count: int
    byte_size: int
    completed_at: datetime | None
    completion_summary: dict[str, int]


def response(plan: UploadCleanup) -> CleanupResponse:
    return CleanupResponse(
        id=plan.id,
        upload_id=plan.upload_id,
        policy_version=plan.policy_version,
        object_count=len(plan.objects),
        byte_size=sum(int(item["byte_size"]) for item in plan.objects),
        completed_at=plan.completed_at,
        completion_summary=plan.completion_summary,
    )


def authorize(scope: Scope) -> None:
    scope.require("retention.manage")
    require_recent(scope.actor)


def eligible(scope: Scope, row: UploadSession) -> FileStore:
    if row.cleanup_hold:
        fail(409, "UPLOAD_CLEANUP_HELD", "Temporary upload bytes are on a preservation hold.")
    if row.expires_at > now() - GRACE or row.status not in {"open", "aborted", "finalized"}:
        fail(409, "UPLOAD_CLEANUP_NOT_READY", "Wait at least 24 hours after upload expiry.")
    if scope.configuration.storage_backend != "filesystem":
        fail(409, "UPLOAD_CLEANUP_UNSUPPORTED", "Cleanup requires the local filesystem adapter.")
    store = storage(scope.configuration)
    assert isinstance(store, FileStore)
    if row.status == "finalized":
        if row.document_id is None:
            fail(409, "UPLOAD_CLEANUP_INTEGRITY", "The finalized original must be verified first.")
        document = get(scope.session, DocumentVersion, row.document_id)
        try:
            with store.path(document.object_key).open("rb") as source:
                body = source.read(row.byte_size + 1)
        except (OSError, ValueError):
            fail(409, "UPLOAD_CLEANUP_INTEGRITY", "The finalized original must be verified first.")
        if (
            document.submission_id != row.submission_id
            or document.sha256 != row.sha256
            or document.byte_size != row.byte_size
            or len(body) != row.byte_size
            or digest(body) != row.sha256
        ):
            fail(409, "UPLOAD_CLEANUP_INTEGRITY", "The finalized original must be verified first.")
    elif row.document_id is not None:
        fail(409, "UPLOAD_CLEANUP_INTEGRITY", "The upload state must be reconciled first.")
    return store


def protect_references(scope: Scope, row: UploadSession, objects: list[TemporaryObject]) -> None:
    keys = [f"tenants/{scope.tenant_id}/uploads/{row.id}/{item.name}" for item in objects]
    for model in (DocumentVersion, PackageArtifact):
        if scope.session.scalar(sa.select(model.id).where(model.object_key.in_(keys)).limit(1)):
            fail(
                409,
                "UPLOAD_CLEANUP_REFERENCED",
                "A retained document or artifact references these bytes.",
            )


@router.get("/{upload_id}/cleanup/hold", response_model=HoldResponse)
def hold_status(upload_id: UUID, scope: Scoped) -> HoldResponse:
    authorize(scope)
    row = get(scope.session, UploadSession, upload_id)
    return HoldResponse(upload_id=row.id, held=row.cleanup_hold, version=row.cleanup_hold_version)


@router.post("/{upload_id}/cleanup/hold", response_model=HoldResponse)
def hold(upload_id: UUID, payload: CleanupHold, scope: Scoped) -> HoldResponse:
    authorize(scope)
    row = get(scope.session, UploadSession, upload_id, lock=True)
    if row.cleanup_hold_version != payload.expected_version:
        fail(
            409,
            "UPLOAD_HOLD_CHANGED",
            "The preservation hold changed. Refresh and review it first.",
        )
    row.cleanup_hold = payload.held
    row.cleanup_hold_version += 1
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "upload.cleanup_hold",
        "upload",
        row.id,
        {"held": payload.held, "reason": payload.reason, "version": row.cleanup_hold_version},
    )
    return HoldResponse(upload_id=row.id, held=row.cleanup_hold, version=row.cleanup_hold_version)


@router.post("/{upload_id}/cleanup/plan", response_model=CleanupResponse, status_code=201)
def prepare(
    upload_id: UUID,
    payload: CleanupRequest,
    scope: Scoped,
    idempotency_key: str | None = Header(default=None),
) -> CleanupResponse:
    authorize(scope)
    row = get(scope.session, UploadSession, upload_id, lock=True)
    object_id, exists = idempotent(
        scope, "upload.cleanup", idempotency_key, {"upload_id": upload_id, **payload.model_dump()}
    )
    if exists:
        return response(get(scope.session, UploadCleanup, object_id))
    store = eligible(scope, row)
    try:
        objects = inventory(store, scope.tenant_id, row.id, now() - GRACE)
    except (OSError, TemporaryStorageError):
        fail(409, "UPLOAD_CLEANUP_UNSAFE", "Temporary files changed or cannot be safely inspected.")
    if not objects:
        fail(409, "UPLOAD_CLEANUP_EMPTY", "There are no eligible temporary files in this upload.")
    protect_references(scope, row, objects)
    plan = UploadCleanup(
        id=object_id,
        tenant_id=scope.tenant_id,
        upload_id=row.id,
        requested_by=scope.membership_id,
        reason=payload.reason,
        policy_version=POLICY_VERSION,
        objects=[item.model_dump() for item in objects],
    )
    scope.session.add(plan)
    scope.session.flush()
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "upload.cleanup_planned",
        "upload_cleanup",
        plan.id,
        {"upload_id": str(row.id), "objects": len(objects), "policy": POLICY_VERSION},
    )
    return response(plan)


@router.get("/{upload_id}/cleanup/{plan_id}", response_model=CleanupResponse)
def status(upload_id: UUID, plan_id: UUID, scope: Scoped) -> CleanupResponse:
    authorize(scope)
    plan = get(scope.session, UploadCleanup, plan_id)
    if plan.upload_id != upload_id:
        fail(404, "NOT_FOUND", "Cleanup plan not found.")
    return response(plan)


@router.post("/{upload_id}/cleanup/{plan_id}/execute", response_model=CleanupResponse)
def execute(upload_id: UUID, plan_id: UUID, scope: Scoped) -> CleanupResponse:
    authorize(scope)
    # Same lock/order as upload part writes and finalization. The plan was
    # committed by a separate request before any irreversible storage effect.
    row = get(scope.session, UploadSession, upload_id, lock=True)
    plan = get(scope.session, UploadCleanup, plan_id, lock=True)
    if plan.upload_id != row.id:
        fail(404, "NOT_FOUND", "Cleanup plan not found.")
    if plan.completed_at is not None:
        return response(plan)
    store = eligible(scope, row)
    if plan.policy_version != POLICY_VERSION:
        fail(409, "UPLOAD_CLEANUP_POLICY", "This cleanup plan requires its original policy.")
    objects = [TemporaryObject.model_validate(item) for item in plan.objects]
    protect_references(scope, row, objects)
    try:
        summary = remove_planned(store, scope.tenant_id, row.id, objects, now() - GRACE)
    except (OSError, TemporaryStorageError):
        fail(
            409,
            "UPLOAD_CLEANUP_UNSAFE",
            "Cleanup is pending; inspect temporary storage before retrying.",
        )
    plan.completed_at, plan.completion_summary = now(), summary
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "upload.cleanup_completed",
        "upload_cleanup",
        plan.id,
        summary,
    )
    return response(plan)

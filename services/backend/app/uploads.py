"""Tenant-bound chunked transfer. Finalization reuses the existing intake invariants."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.common import digest, fail, now, record
from app.domain.models import DocumentVersion, Submission
from app.identity import Input, Scoped
from app.platform import require_capacity
from app.platform_models import UploadPart, UploadSession
from app.storage import storage
from app.workflow import ensure_open, get, idempotent

CHUNK_SIZE = 1024 * 1024
router = APIRouter(prefix="/api/v1", tags=["resumable intake"])


class UploadInit(Input):
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00\r\n]+\.(?i:pdf|xlsx)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0, le=50 * 1024 * 1024)
    supersedes_id: UUID | None = None


class UploadResponse(BaseModel):
    id: UUID
    submission_id: UUID
    filename: str
    sha256: str
    byte_size: int
    status: str
    expires_at: datetime
    document_id: UUID | None
    chunk_size: int = CHUNK_SIZE
    uploaded_parts: list[int]


def response(scope: Scoped, row: UploadSession) -> dict[str, Any]:
    return {
        **record(row),
        "chunk_size": CHUNK_SIZE,
        "uploaded_parts": list(
            scope.session.scalars(
                sa.select(UploadPart.number)
                .where(UploadPart.upload_id == row.id)
                .order_by(UploadPart.number)
            )
        ),
    }


def accessible(scope: Scoped, upload_id: UUID) -> UploadSession:
    scope.require("documents.write")
    row = get(scope.session, UploadSession, upload_id, lock=True)
    if row.uploaded_by != scope.actor.user_id:
        fail(404, "NOT_FOUND", "Upload session not found.")
    if row.status == "aborted" or (row.expires_at <= now() and row.document_id is None):
        fail(410, "UPLOAD_EXPIRED", "Start a new upload for this file.")
    return row


@router.post("/submissions/{submission_id}/uploads", status_code=201, response_model=UploadResponse)
def initialize(
    submission_id: UUID,
    payload: UploadInit,
    scope: Scoped,
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    scope.require("documents.write")
    require_capacity(scope)
    submission = get(scope.session, Submission, submission_id, lock=True)
    ensure_open(submission)
    object_id, exists = idempotent(
        scope,
        "upload.initialize",
        idempotency_key,
        {"submission": submission_id, **payload.model_dump()},
    )
    if exists:
        return response(scope, accessible(scope, object_id))
    if (
        payload.supersedes_id
        and get(scope.session, DocumentVersion, payload.supersedes_id).submission_id
        != submission_id
    ):
        fail(422, "INVALID_DOCUMENT_VERSION", "Replace a document in this submission.")
    pending = (
        scope.session.scalar(
            sa.select(sa.func.count())
            .select_from(UploadSession)
            .where(UploadSession.status == "open", UploadSession.expires_at > now())
        )
        or 0
    )
    if pending >= 30:
        fail(429, "UPLOAD_LIMIT", "Complete or cancel existing uploads first.")
    row = UploadSession(
        id=object_id,
        tenant_id=scope.tenant_id,
        submission_id=submission_id,
        uploaded_by=scope.actor.user_id,
        expires_at=now() + timedelta(hours=24),
        **payload.model_dump(),
    )
    scope.session.add(row)
    scope.session.flush()
    return response(scope, row)


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
def upload_status(upload_id: UUID, scope: Scoped) -> dict[str, Any]:
    return response(scope, accessible(scope, upload_id))


@router.put("/uploads/{upload_id}/parts/{number}", response_model=UploadResponse)
async def put_part(upload_id: UUID, number: int, request: Request, scope: Scoped) -> dict[str, Any]:
    require_capacity(scope)
    row = accessible(scope, upload_id)
    if row.document_id:
        fail(409, "UPLOAD_FINALIZED", "The upload is already finalized.")
    if not 0 <= number < (row.byte_size + CHUNK_SIZE - 1) // CHUNK_SIZE:
        fail(422, "INVALID_PART", "Invalid upload part.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > CHUNK_SIZE:
            fail(413, "PART_TOO_LARGE", "Upload parts are limited to 1 MiB.")
    expected = min(CHUNK_SIZE, row.byte_size - number * CHUNK_SIZE)
    if len(body) != expected:
        fail(422, "PART_SIZE", "The part size does not match this upload.")
    sha = digest(bytes(body))
    previous = scope.session.scalar(
        sa.select(UploadPart).where(UploadPart.upload_id == row.id, UploadPart.number == number)
    )
    if previous:
        if previous.sha256 != sha:
            fail(409, "PART_CONFLICT", "This part was already uploaded with different bytes.")
        return response(scope, row)
    # Unique immutable keys avoid replacing any bytes, including after a rolled-back transaction.
    key = f"tenants/{scope.tenant_id}/uploads/{row.id}/{number}-{uuid4().hex}"
    version = storage(scope.configuration).put(key, bytes(body), "application/octet-stream")
    scope.session.add(
        UploadPart(
            tenant_id=scope.tenant_id,
            upload_id=row.id,
            number=number,
            sha256=sha,
            byte_size=len(body),
            object_key=key,
            storage_version=version,
        )
    )
    scope.session.flush()
    return response(scope, row)


@router.post("/uploads/{upload_id}/finalize", response_model=UploadResponse)
def finalize(upload_id: UUID, scope: Scoped) -> dict[str, Any]:
    from app.api import store_document

    require_capacity(scope)
    row = accessible(scope, upload_id)
    if row.document_id:
        return response(scope, row)
    submission = get(scope.session, Submission, row.submission_id, lock=True)
    ensure_open(submission)
    parts = list(
        scope.session.scalars(
            sa.select(UploadPart).where(UploadPart.upload_id == row.id).order_by(UploadPart.number)
        )
    )
    if [p.number for p in parts] != list(range((row.byte_size + CHUNK_SIZE - 1) // CHUNK_SIZE)):
        fail(409, "UPLOAD_INCOMPLETE", "Upload every part before finalizing.")
    store = storage(scope.configuration)
    body = bytearray()
    for part in parts:
        data = store.get(part.object_key, part.storage_version)
        if len(data) != part.byte_size or digest(data) != part.sha256:
            fail(409, "INTEGRITY_FAILURE", "An upload part failed integrity verification.")
        body.extend(data)
    if len(body) != row.byte_size or digest(bytes(body)) != row.sha256:
        fail(422, "UPLOAD_HASH_MISMATCH", "File bytes differ from the initialized upload.")
    document = store_document(
        scope, submission, row.filename, bytes(body), uuid4(), row.supersedes_id
    )
    row.document_id = document.id
    row.status = "finalized"
    scope.session.flush()
    return response(scope, row)


@router.post("/uploads/{upload_id}/abort", response_model=UploadResponse)
def abort(upload_id: UUID, scope: Scoped) -> dict[str, Any]:
    require_capacity(scope)
    row = accessible(scope, upload_id)
    if row.document_id:
        fail(409, "UPLOAD_FINALIZED", "Finalized source documents remain in submission history.")
    row.status = "aborted"
    return response(scope, row)

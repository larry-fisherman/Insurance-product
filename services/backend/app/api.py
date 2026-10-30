"""Authorized REST workflows. Domain writes always use tenant-scoped transactions."""

import hmac
import secrets
from datetime import date, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, File, Form, Header, Query, Request, Response, UploadFile
from pydantic import Field, model_validator

from app.common import audit, canonical_json, digest, fail, now, record
from app.contracts import (
    DocumentResponse,
    InsuredResponse,
    JobResponse,
    PackageResponse,
    SubmissionResponse,
    TaskResponse,
    WorkspaceResponse,
)
from app.database.models import (
    AppUser,
    AuditEvent,
    Invitation,
    Membership,
    MembershipRole,
    Role,
    Tenant,
)
from app.documents import MAX_FILE, DocumentError, fixture_signature, validate_file
from app.domain.cgl import FIELD_LABELS
from app.domain.models import (
    Approval,
    CanonicalRevision,
    Discrepancy,
    DocumentVersion,
    ExportEvent,
    InsuredAccount,
    Package,
    PackageArtifact,
    ProcessingJob,
    SourceBlock,
    Submission,
    UsageEvent,
    WorkItem,
)
from app.identity import Authenticated, Input, Scoped, database
from app.rendering import TEMPLATE_VERSION
from app.storage import storage
from app.workflow import (
    active_documents,
    enqueue,
    ensure_open,
    get,
    idempotent,
    readiness,
    refresh_issues,
    review,
    save_revision,
    verify_approval_authority,
    version_check,
)

router = APIRouter(prefix="/api/v1", tags=["workspace"])


class InsuredInput(Input):
    name: str = Field(min_length=2, max_length=200)
    dba: str | None = Field(default=None, max_length=200)
    entity_type: str | None = Field(default=None, max_length=100)
    mailing_address: str | None = Field(default=None, max_length=1000)
    operations: str | None = Field(default=None, max_length=4000)
    external_reference: str | None = Field(default=None, max_length=200)


class InsuredEdit(InsuredInput):
    expected_version: int = Field(ge=1)
    status: Literal["active", "archived"] = "active"


class SubmissionInput(Input):
    insured_id: UUID
    title: str = Field(min_length=2, max_length=200)
    kind: Literal["new_business", "renewal"] = "new_business"
    intended_start: date
    intended_end: date
    due_date: date | None = None
    loss_required_start: date | None = None
    loss_required_end: date | None = None
    assigned_membership_id: UUID | None = None
    prior_submission_id: UUID | None = None

    @model_validator(mode="after")
    def dates(self) -> "SubmissionInput":
        if self.intended_end <= self.intended_start:
            raise ValueError("Policy expiration must follow its effective date")
        if bool(self.loss_required_start) != bool(self.loss_required_end):
            raise ValueError("Specify both endpoints of the requested loss-history period")
        if (
            self.loss_required_start
            and self.loss_required_end
            and self.loss_required_end < self.loss_required_start
        ):
            raise ValueError("Loss-history end must follow its beginning")
        return self


class SubmissionEdit(SubmissionInput):
    expected_version: int = Field(ge=1)
    loss_freshness_days: int = Field(default=60, ge=1, le=730)


class DecisionInput(Input):
    group_key: str = Field(min_length=1, max_length=200)
    fingerprint: str = ""
    action: Literal["approve", "correct", "omit", "reject"]
    field: str | None = None
    observation_id: UUID | None = None
    value: Any = None
    context: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=2000)
    support_type: Literal["document", "broker_attestation"] | None = None
    support_source_ids: list[UUID] = Field(default_factory=list, max_length=20)
    attestation_origin: str = Field(default="", max_length=2000)


class ReviewInput(Input):
    expected_version: int = Field(ge=1)
    decisions: list[DecisionInput] = Field(min_length=1, max_length=100)


class ReasonInput(Input):
    reason: str = Field(min_length=5, max_length=2000)


class ExceptionInput(ReasonInput):
    key: str = Field(max_length=200)
    fingerprint: str
    expected_version: int


class ClassificationInput(ReasonInput):
    document_type: Literal[
        "application", "policy", "loss_runs", "exposure_schedule", "endorsement", "mixed", "unknown"
    ]


class VersionInput(Input):
    expected_version: int = Field(ge=1)


class ApprovalInput(Input):
    manifest_hash: str = Field(min_length=64, max_length=64)
    attestation: str = Field(min_length=20, max_length=1000)


class InvitationInput(Input):
    email: str = Field(min_length=3, max_length=254)
    roles: list[Literal["viewer", "preparer", "approver", "tenant_admin"]] = Field(
        min_length=1, max_length=4
    )


class InvitationAcceptance(Input):
    token: str = Field(min_length=20, max_length=100)


class MembershipEdit(Input):
    status: Literal["active", "disabled"]
    roles: list[Literal["viewer", "preparer", "approver", "tenant_admin"]] = Field(max_length=4)
    expected_version: int


class TaskInput(Input):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    due_date: date | None = None
    owner_membership_id: UUID | None = None


class TaskEdit(Input):
    expected_version: int = Field(ge=1)
    status: Literal["open", "completed"] | None = None
    due_date: date | None = None
    owner_membership_id: UUID | None = None
    resolution_reason: str = Field(default="", max_length=2000)


class TransitionInput(ReasonInput):
    expected_version: int = Field(ge=1)
    status: Literal["draft", "collecting", "in_review", "ready_for_package", "closed", "cancelled"]


def document_record(doc: DocumentVersion) -> dict[str, Any]:
    return record(doc, {"object_key", "storage_version", "fixture_signature"})


@router.get("/dashboard")
def dashboard(scope: Scoped, owner_membership_id: UUID | None = None) -> dict[str, Any]:
    scope.require("agency.read")
    tenant = get(scope.session, Tenant, scope.tenant_id)
    can_view = "submissions.read" in scope.actor.permissions
    query = sa.select(Submission).order_by(Submission.due_date.asc().nulls_last(), Submission.id)
    if owner_membership_id:
        get(scope.session, Membership, owner_membership_id)
        query = query.where(Submission.assigned_membership_id == owner_membership_id)
    submissions = list(scope.session.scalars(query)) if can_view else []
    insureds = (
        {row.id: row.name for row in scope.session.scalars(sa.select(InsuredAccount))}
        if can_view
        else {}
    )
    queue: list[dict[str, Any]] = []
    issue_count = 0
    for submission in submissions:
        if submission.status in {"closed", "cancelled"}:
            continue
        blockers = readiness(scope.session, submission)["blockers"]
        issue_count += len(blockers)
        queue.extend(
            {
                **item,
                "submission_id": str(submission.id),
                "title": submission.title,
                "owner_membership_id": str(submission.assigned_membership_id),
            }
            for item in blockers[: max(0, 100 - len(queue))]
        )
    ids = [s.id for s in submissions]
    failures = (
        scope.session.scalar(
            sa.select(sa.func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.status == "failed", ProcessingJob.submission_id.in_(ids))
        )
        if can_view
        else 0
    )
    return {
        "agency": record(tenant),
        "submissions": [
            {**record(s), "insured_name": insureds.get(s.insured_id, "Insured")}
            for s in submissions[:100]
        ],
        "submission_count": len(submissions),
        "blocking_queue": queue,
        "metrics": {
            "active_submissions": sum(s.status not in {"closed", "cancelled"} for s in submissions),
            "approved_packages": sum(s.status in {"approved", "exported"} for s in submissions),
            "open_discrepancies": issue_count,
            "processing_failures": failures,
        },
        "synthetic_only": True,
    }


@router.get("/insureds")
def list_insureds(
    scope: Scoped,
    q: str = Query(default="", max_length=200),
    after: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    scope.require("insureds.read")
    query = sa.select(InsuredAccount).order_by(InsuredAccount.id).limit(limit + 1)
    if q:
        query = query.where(
            InsuredAccount.name.ilike(
                "%" + q.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
            )
        )
    if after:
        query = query.where(InsuredAccount.id > after)
    rows = list(scope.session.scalars(query))
    return {
        "items": [record(row) for row in rows[:limit]],
        "next": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/insureds", status_code=201, response_model=InsuredResponse)
def create_insured(
    payload: InsuredInput, scope: Scoped, idempotency_key: str | None = Header(default=None)
) -> dict[str, Any]:
    scope.require("insureds.write")
    object_id, exists = idempotent(
        scope, "insured.create", idempotency_key, payload.model_dump(mode="json")
    )
    if exists:
        return record(get(scope.session, InsuredAccount, object_id))
    row = InsuredAccount(id=object_id, tenant_id=scope.tenant_id, **payload.model_dump())
    scope.session.add(row)
    scope.session.flush()
    audit(scope.session, scope.tenant_id, scope.membership_id, "insured.created", "insured", row.id)
    return record(row)


@router.get("/insureds/{insured_id}")
def insured_detail(insured_id: UUID, scope: Scoped) -> dict[str, Any]:
    scope.require("insureds.read")
    row = get(scope.session, InsuredAccount, insured_id)
    submissions = scope.session.scalars(
        sa.select(Submission)
        .where(Submission.insured_id == row.id)
        .order_by(Submission.created_at.desc())
    ).all()
    return {"insured": record(row), "submissions": [record(s) for s in submissions]}


@router.patch("/insureds/{insured_id}", response_model=InsuredResponse)
def edit_insured(insured_id: UUID, payload: InsuredEdit, scope: Scoped) -> dict[str, Any]:
    scope.require("insureds.write")
    row = get(scope.session, InsuredAccount, insured_id, lock=True)
    if row.row_version != payload.expected_version:
        fail(409, "STALE_RECORD", "This insured account changed. Refresh before saving.")
    changes = payload.model_dump(exclude={"expected_version"})
    material = any(
        getattr(row, key) != changes[key]
        for key in ("name", "dba", "entity_type", "mailing_address", "operations")
    )
    for key, value in changes.items():
        setattr(row, key, value)
    if material:
        scope.session.execute(
            sa.update(Submission)
            .where(Submission.insured_id == row.id)
            .values(
                input_version=Submission.input_version + 1,
                row_version=Submission.row_version + 1,
                status=sa.case(
                    (Submission.status.in_(["closed", "cancelled"]), Submission.status),
                    else_="in_review",
                ),
            )
        )
    row.row_version += 1
    audit(scope.session, scope.tenant_id, scope.membership_id, "insured.updated", "insured", row.id)
    scope.session.flush()
    return record(row)


@router.get("/submissions")
def list_submissions(
    scope: Scoped, limit: int = Query(default=50, ge=1, le=100), after: UUID | None = None
) -> dict[str, Any]:
    scope.require("submissions.read")
    query = sa.select(Submission).order_by(Submission.id).limit(limit + 1)
    if after:
        query = query.where(Submission.id > after)
    rows = list(scope.session.scalars(query))
    return {
        "items": [record(row) for row in rows[:limit]],
        "next": str(rows[limit - 1].id) if len(rows) > limit else None,
    }


@router.post("/submissions", status_code=201, response_model=SubmissionResponse)
def create_submission(
    payload: SubmissionInput, scope: Scoped, idempotency_key: str | None = Header(default=None)
) -> dict[str, Any]:
    scope.require("submissions.write")
    insured = get(scope.session, InsuredAccount, payload.insured_id)
    if insured.status != "active":
        fail(409, "INSURED_ARCHIVED", "Reactivate this insured before creating a submission.")
    object_id, exists = idempotent(
        scope, "submission.create", idempotency_key, payload.model_dump(mode="json")
    )
    if exists:
        return record(get(scope.session, Submission, object_id))
    values = payload.model_dump()
    values["assigned_membership_id"] = payload.assigned_membership_id or scope.membership_id
    member = get(scope.session, Membership, values["assigned_membership_id"])
    if member.status != "active":
        fail(422, "OWNER_INACTIVE", "Assign an active agency member.")
    if payload.prior_submission_id:
        prior = get(scope.session, Submission, payload.prior_submission_id)
        if prior.insured_id != insured.id or payload.kind != "renewal":
            fail(422, "INVALID_RENEWAL", "Renewals must reference the same insured.")
        revision = scope.session.scalar(
            sa.select(CanonicalRevision)
            .where(CanonicalRevision.submission_id == prior.id)
            .order_by(CanonicalRevision.number.desc())
            .limit(1)
        )
        if revision:
            if digest(canonical_json(revision.snapshot)) != revision.sha256:
                fail(409, "INTEGRITY_FAILURE", "Prior revision integrity verification failed.")
            values["renewal_candidates"] = [
                {
                    "key": "renewal:" + str(index),
                    "field": fact["field"],
                    "value": fact["value"],
                    "context": fact["context"],
                    "prior_revision_id": str(revision.id),
                    "prior_revision_hash": revision.sha256,
                    "prior_fact": fact,
                    "confirmed_for_current_period": False,
                }
                for index, fact in enumerate(revision.snapshot["facts"])
            ]
    row = Submission(
        id=object_id, tenant_id=scope.tenant_id, requirements={"loss_freshness_days": 60}, **values
    )
    scope.session.add(row)
    scope.session.flush()
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "submission.created",
        "submission",
        row.id,
        {"kind": payload.kind, "carry_forward_verified": False},
    )
    return record(row)


@router.patch("/submissions/{submission_id}", response_model=SubmissionResponse)
def edit_submission(submission_id: UUID, payload: SubmissionEdit, scope: Scoped) -> dict[str, Any]:
    scope.require("submissions.write")
    row = get(scope.session, Submission, submission_id, lock=True)
    ensure_open(row)
    version_check(row, payload.expected_version)
    if (
        payload.insured_id != row.insured_id
        or payload.prior_submission_id != row.prior_submission_id
    ):
        fail(
            422,
            "IDENTITY_IMMUTABLE",
            "Create a new submission to change the insured or renewal lineage.",
        )
    owner = get(scope.session, Membership, payload.assigned_membership_id or scope.membership_id)
    if owner.status != "active":
        fail(422, "OWNER_INACTIVE", "Assign an active agency member.")
    values = payload.model_dump(exclude={"expected_version", "loss_freshness_days"})
    values["assigned_membership_id"] = owner.id
    for key, value in values.items():
        setattr(row, key, value)
    row.requirements = {"loss_freshness_days": payload.loss_freshness_days}
    row.input_version += 1
    row.row_version += 1
    row.status = "in_review"
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "submission.updated",
        "submission",
        row.id,
    )
    return record(row)


@router.post("/submissions/{submission_id}/status", response_model=SubmissionResponse)
def transition_submission(
    submission_id: UUID, payload: TransitionInput, scope: Scoped
) -> dict[str, Any]:
    scope.require("submissions.write")
    row = get(scope.session, Submission, submission_id, lock=True)
    version_check(row, payload.expected_version)
    ensure_open(row)
    transitions = {
        "draft": {"collecting", "cancelled"},
        "collecting": {"draft", "in_review", "cancelled"},
        "in_review": {"collecting", "ready_for_package", "cancelled"},
        "ready_for_package": {"in_review", "collecting", "cancelled"},
        "awaiting_approval": {"in_review", "cancelled"},
        "approved": {"in_review", "closed", "cancelled"},
        "exported": {"in_review", "closed", "cancelled"},
    }
    if payload.status not in transitions.get(row.status, set()):
        fail(409, "INVALID_TRANSITION", "This status transition is not permitted.")
    if payload.status == "draft" and scope.session.scalar(
        sa.select(DocumentVersion.id).where(DocumentVersion.submission_id == row.id).limit(1)
    ):
        fail(409, "DOCUMENTS_EXIST", "A submission with documents cannot return to draft.")
    if payload.status == "ready_for_package" and not readiness(scope.session, row)["ready"]:
        fail(
            409, "REVIEW_INCOMPLETE", "Resolve readiness blockers before marking ready for package."
        )
    if payload.status in {"closed", "cancelled"} and scope.session.scalar(
        sa.select(ProcessingJob.id)
        .where(
            ProcessingJob.submission_id == row.id, ProcessingJob.status.in_(["running", "queued"])
        )
        .limit(1)
    ):
        fail(
            409,
            "PROCESSING_ACTIVE",
            "Finish processing before closing or cancelling this submission.",
        )
    previous = row.status
    row.status, row.row_version = payload.status, row.row_version + 1
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "submission.transitioned",
        "submission",
        row.id,
        {"from": previous, "to": row.status, "reason": payload.reason},
    )
    return record(row)


@router.get("/submissions/{submission_id}", response_model=WorkspaceResponse)
def workspace(submission_id: UUID, scope: Scoped) -> dict[str, Any]:
    scope.require("submissions.read")
    submission = get(scope.session, Submission, submission_id)
    state = readiness(scope.session, submission)
    documents = scope.session.scalars(
        sa.select(DocumentVersion)
        .where(DocumentVersion.submission_id == submission_id)
        .order_by(DocumentVersion.created_at)
    ).all()
    jobs = scope.session.scalars(
        sa.select(ProcessingJob)
        .where(ProcessingJob.submission_id == submission_id)
        .order_by(ProcessingJob.created_at.desc())
    ).all()
    packages = scope.session.scalars(
        sa.select(Package)
        .where(Package.submission_id == submission_id)
        .order_by(Package.created_at.desc())
    ).all()
    issues = scope.session.scalars(
        sa.select(Discrepancy).where(Discrepancy.submission_id == submission_id)
    ).all()
    tasks = scope.session.scalars(
        sa.select(WorkItem)
        .where(WorkItem.submission_id == submission_id)
        .order_by(WorkItem.created_at)
    ).all()
    cited_ids = {
        UUID(ref)
        for group in state["groups"]
        for observation in group["observations"]
        for ref in [observation["source_id"], *observation["supporting_source_ids"]]
    }
    sources = scope.session.scalars(
        sa.select(SourceBlock)
        .join(DocumentVersion, DocumentVersion.id == SourceBlock.document_id)
        .where(
            DocumentVersion.submission_id == submission_id,
            sa.or_(SourceBlock.id.in_(cited_ids), DocumentVersion.processing_status == "failed"),
        )
    ).all()
    return {
        "submission": record(submission),
        "insured": record(get(scope.session, InsuredAccount, submission.insured_id)),
        "documents": [document_record(doc) for doc in documents],
        "jobs": [record(job) for job in jobs],
        "readiness": state,
        "sources": [record(source) for source in sources],
        "packages": [
            {
                **record(package),
                "superseded": package.input_version != submission.input_version
                or get(scope.session, CanonicalRevision, package.revision_id).number
                != submission.revision_number,
                "approval": record(approval)
                if (
                    approval := scope.session.scalar(
                        sa.select(Approval).where(Approval.package_id == package.id)
                    )
                )
                else None,
            }
            for package in packages
        ],
        "issues": [record(issue) for issue in issues],
        "tasks": [record(task) for task in tasks],
        "members": [
            {"id": str(member.id), "user_id": str(member.user_id), "display_name": name}
            for member, name in scope.session.execute(
                sa.select(Membership, AppUser.display_name)
                .join(AppUser, AppUser.id == Membership.user_id)
                .where(Membership.status == "active")
            )
        ],
        "field_labels": FIELD_LABELS,
    }


def store_document(
    scope: Scoped,
    submission: Submission,
    filename: str,
    data: bytes,
    object_id: UUID,
    supersedes_id: UUID | None = None,
    trusted_fixture: bool = False,
) -> DocumentVersion:
    ensure_open(submission)
    try:
        media_type = validate_file(filename, data)
    except DocumentError as error:
        fail(
            422, error.code, "The file could not be accepted. Upload a safe, supported PDF or XLSX."
        )
    documents = active_documents(scope.session, submission.id)
    if (
        len(documents) >= 30
        or sum(doc.byte_size for doc in documents) + len(data) > 100 * 1024 * 1024
    ):
        fail(422, "PACKET_LIMIT", "A submission supports 30 files and 100 MB of active documents.")
    if supersedes_id:
        previous = get(scope.session, DocumentVersion, supersedes_id)
        if previous.submission_id != submission.id:
            fail(422, "INVALID_DOCUMENT_VERSION", "Replace a document from this submission.")
        previous.excluded_reason = "Superseded by a newer document version"
        previous.excluded_by, previous.excluded_at = scope.actor.user_id, now()
    key = f"tenants/{scope.tenant_id}/documents/{object_id}/original"
    version = storage(scope.configuration).put(key, data, media_type)
    row = DocumentVersion(
        id=object_id,
        tenant_id=scope.tenant_id,
        submission_id=submission.id,
        supersedes_id=supersedes_id,
        filename=filename,
        media_type=media_type,
        sha256=digest(data),
        byte_size=len(data),
        object_key=key,
        storage_version=version,
        uploaded_by=scope.actor.user_id,
        fixture_signature=fixture_signature(data, scope.configuration) if trusted_fixture else None,
    )
    scope.session.add(row)
    scope.session.flush()
    submission.input_version += 1
    submission.row_version += 1
    submission.status = "collecting"
    enqueue(scope, submission.id, document_id=row.id)
    refresh_issues(scope.session, submission)
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "document.uploaded",
        "document",
        row.id,
        {"byte_size": len(data), "sha256": row.sha256, "replacement": bool(supersedes_id)},
    )
    return row


@router.post(
    "/submissions/{submission_id}/documents", status_code=202, response_model=DocumentResponse
)
def upload(
    submission_id: UUID,
    scope: Scoped,
    file: Annotated[UploadFile, File()],
    supersedes_id: Annotated[UUID | None, Form()] = None,
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    scope.require("documents.write")
    submission = get(scope.session, Submission, submission_id, lock=True)
    data = file.file.read(MAX_FILE + 1)
    filename = file.filename or ""
    object_id, exists = idempotent(
        scope,
        "document.upload",
        idempotency_key,
        {
            "submission": submission_id,
            "filename": filename,
            "sha256": digest(data),
            "supersedes": supersedes_id,
        },
    )
    if exists:
        return document_record(get(scope.session, DocumentVersion, object_id))
    row = store_document(scope, submission, filename, data, object_id, supersedes_id)
    return document_record(row)


@router.post("/documents/{document_id}/exclude")
def exclude_document(document_id: UUID, payload: ReasonInput, scope: Scoped) -> dict[str, bool]:
    scope.require("documents.write")
    doc = get(scope.session, DocumentVersion, document_id)
    submission = get(scope.session, Submission, doc.submission_id, lock=True)
    ensure_open(submission)
    doc.excluded_reason = payload.reason
    doc.excluded_by, doc.excluded_at = scope.actor.user_id, now()
    submission.input_version += 1
    submission.row_version += 1
    submission.status = "in_review"
    scope.session.flush()
    refresh_issues(scope.session, submission)
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "document.excluded",
        "document",
        doc.id,
        {"reason": payload.reason},
    )
    return {"ok": True}


@router.get("/documents/{document_id}/content")
def document_content(document_id: UUID, scope: Scoped, inline: bool = False) -> Response:
    scope.require("documents.read")
    doc = get(scope.session, DocumentVersion, document_id)
    if doc.scan_status != "clean":
        fail(409, "QUARANTINED", "This document is not available until safety scanning completes.")
    data = storage(scope.configuration).get(doc.object_key, doc.storage_version)
    if not hmac.compare_digest(digest(data), doc.sha256):
        fail(409, "INTEGRITY_FAILURE", "Document integrity verification failed.")
    audit(
        scope.session, scope.tenant_id, scope.membership_id, "document.accessed", "document", doc.id
    )
    return Response(
        data,
        media_type=doc.media_type,
        headers={
            "Content-Disposition": (
                "inline" if inline and doc.media_type == "application/pdf" else "attachment"
            )
            + "; filename*=UTF-8''"
            + quote(doc.filename),
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.post("/documents/{document_id}/classification", response_model=DocumentResponse)
def classify_document(
    document_id: UUID, payload: ClassificationInput, scope: Scoped
) -> dict[str, Any]:
    scope.require("documents.write")
    doc = get(scope.session, DocumentVersion, document_id)
    submission = get(scope.session, Submission, doc.submission_id, lock=True)
    ensure_open(submission)
    if doc.processing_status != "completed":
        fail(409, "DOCUMENT_NOT_READY", "Wait for processing before overriding classification.")
    doc.document_type = payload.document_type
    doc.processing_inventory = {
        **doc.processing_inventory,
        "classification_override": {
            "type": payload.document_type,
            "reason": payload.reason,
            "actor": str(scope.actor.user_id),
            "at": now().isoformat(),
        },
    }
    submission.input_version += 1
    submission.row_version += 1
    submission.status = "in_review"
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "document.classification_changed",
        "document",
        doc.id,
        payload.model_dump(),
    )
    return document_record(doc)


@router.get("/sources/{source_id}")
def source(source_id: UUID, scope: Scoped) -> dict[str, Any]:
    scope.require("documents.read")
    row = get(scope.session, SourceBlock, source_id)
    get(scope.session, DocumentVersion, row.document_id)
    return record(row)


@router.post("/jobs/{job_id}/retry", status_code=202, response_model=JobResponse)
def retry_job(job_id: UUID, scope: Scoped) -> dict[str, Any]:
    scope.require("documents.write")
    candidate = get(scope.session, ProcessingJob, job_id)
    submission = get(scope.session, Submission, candidate.submission_id, lock=True)
    ensure_open(submission)
    job = get(scope.session, ProcessingJob, job_id, lock=True)
    if job.document_id:
        doc = get(scope.session, DocumentVersion, job.document_id)
        pending = scope.session.scalar(
            sa.select(ProcessingJob.id)
            .where(
                ProcessingJob.document_id == doc.id, ProcessingJob.status.in_(["queued", "running"])
            )
            .limit(1)
        )
        if doc.processing_status == "completed" or pending:
            fail(
                409,
                "DOCUMENT_ALREADY_ACTIVE",
                "This document is already complete or has an active processing job.",
            )
        submission.input_version += 1
        submission.row_version += 1
    if job.status != "failed":
        fail(409, "JOB_NOT_FAILED", "Only failed jobs can be retried.")
    if job.error_code in {"MALWARE_DETECTED", "ACTIVE_PDF_CONTENT", "UNSAFE_SPREADSHEET"}:
        fail(409, "UNSAFE_DOCUMENT", "Upload a safe replacement document.")
    replacement = enqueue(
        scope,
        job.submission_id,
        document_id=job.document_id,
        package_id=job.package_id,
        kind=job.kind,
    )
    if job.document_id:
        doc = get(scope.session, DocumentVersion, job.document_id)
        doc.processing_status = "queued"
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "processing.retried",
        "job",
        replacement.id,
    )
    return record(replacement)


@router.post("/submissions/{submission_id}/review-decisions")
def review_decisions(submission_id: UUID, payload: ReviewInput, scope: Scoped) -> dict[str, Any]:
    submission = get(scope.session, Submission, submission_id, lock=True)
    version_check(submission, payload.expected_version)
    revision = review(
        scope, submission, [item.model_dump(mode="json") for item in payload.decisions]
    )
    return {"revision": record(revision), "row_version": submission.row_version}


@router.post("/submissions/{submission_id}/exceptions")
def accept_exception(submission_id: UUID, payload: ExceptionInput, scope: Scoped) -> dict[str, Any]:
    scope.require("exceptions.accept")
    submission = get(scope.session, Submission, submission_id, lock=True)
    ensure_open(submission)
    version_check(submission, payload.expected_version)
    verify_approval_authority(scope)
    state = readiness(scope.session, submission)
    item = next((b for b in state["blockers"] if b["key"] == payload.key and b["waivable"]), None)
    if not item or item["fingerprint"] != payload.fingerprint:
        fail(409, "EXCEPTION_CHANGED", "Refresh the current missing-information report.")
    row = scope.session.scalar(
        sa.select(Discrepancy).where(
            Discrepancy.submission_id == submission.id,
            Discrepancy.key == payload.key,
            Discrepancy.kind == "missing",
        )
    )
    if row is None:
        row = Discrepancy(
            tenant_id=scope.tenant_id,
            submission_id=submission.id,
            key=payload.key,
            kind="missing",
            severity="material",
            message=item["message"],
            observation_ids=[],
            input_fingerprint=payload.fingerprint,
        )
        scope.session.add(row)
    row.status, row.resolution_reason, row.resolved_by = (
        "accepted",
        payload.reason,
        scope.actor.user_id,
    )
    row.input_fingerprint = payload.fingerprint
    scope.session.flush()
    submission.row_version += 1
    revision = save_revision(scope, submission)
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "exception.accepted",
        "submission",
        submission.id,
        {"requirement": payload.key},
    )
    return {"revision": record(revision), "row_version": submission.row_version}


@router.post(
    "/submissions/{submission_id}/packages", status_code=202, response_model=PackageResponse
)
def generate_package(
    submission_id: UUID,
    payload: VersionInput,
    scope: Scoped,
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    scope.require("packages.generate")
    submission = get(scope.session, Submission, submission_id, lock=True)
    ensure_open(submission)
    object_id, exists = idempotent(
        scope,
        "package.generate",
        idempotency_key,
        {"submission": submission.id, "version": payload.expected_version},
    )
    if exists:
        return record(get(scope.session, Package, object_id))
    version_check(submission, payload.expected_version)
    state = readiness(scope.session, submission)
    if not state["ready"]:
        fail(
            409, "REVIEW_INCOMPLETE", "Resolve all nonwaived blockers before generating a package."
        )
    revision = save_revision(scope, submission)
    document_manifest = [
        {"id": str(d.id), "sha256": d.sha256}
        for d in active_documents(scope.session, submission.id)
    ]
    package = Package(
        template_version=TEMPLATE_VERSION,
        manifest={"requested_documents": document_manifest},
        id=object_id,
        tenant_id=scope.tenant_id,
        submission_id=submission.id,
        revision_id=revision.id,
        input_version=submission.input_version,
    )
    scope.session.add(package)
    scope.session.flush()
    enqueue(scope, submission.id, package_id=package.id, kind="package")
    submission.status = "awaiting_approval"
    submission.row_version += 1
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "package.requested",
        "package",
        package.id,
    )
    return record(package)


@router.get("/packages/{package_id}")
def package_detail(package_id: UUID, scope: Scoped) -> dict[str, Any]:
    scope.require("packages.read")
    package = get(scope.session, Package, package_id)
    submission = get(scope.session, Submission, package.submission_id)
    revision = get(scope.session, CanonicalRevision, package.revision_id)
    artifacts = scope.session.scalars(
        sa.select(PackageArtifact).where(PackageArtifact.package_id == package.id)
    ).all()
    approval = scope.session.scalar(sa.select(Approval).where(Approval.package_id == package.id))
    return {
        "package": record(package),
        "revision": record(revision),
        "approval": record(approval) if approval else None,
        "superseded": package.input_version != submission.input_version
        or revision.number != submission.revision_number,
        "artifacts": [record(a, {"object_key", "storage_version"}) for a in artifacts],
    }


@router.post("/packages/{package_id}/approvals")
def approve_package(package_id: UUID, payload: ApprovalInput, scope: Scoped) -> dict[str, Any]:
    scope.require("packages.approve")
    package = get(scope.session, Package, package_id)
    submission = get(scope.session, Submission, package.submission_id, lock=True)
    ensure_open(submission)
    verify_approval_authority(scope)
    revision = get(scope.session, CanonicalRevision, package.revision_id)
    if (
        package.input_version != submission.input_version
        or revision.number != submission.revision_number
    ):
        fail(409, "PACKAGE_SUPERSEDED", "The workspace changed. Generate and review a new package.")
    if (
        package.status not in {"ready", "approved"}
        or package.manifest_hash != payload.manifest_hash
        or not readiness(scope.session, submission)["ready"]
    ):
        fail(409, "PACKAGE_NOT_READY", "Review the current package and resolve all blockers.")
    if (
        digest(canonical_json(package.manifest)) != package.manifest_hash
        or digest(canonical_json(revision.snapshot)) != revision.sha256
    ):
        fail(409, "INTEGRITY_FAILURE", "Package integrity verification failed.")
    artifacts = list(
        scope.session.scalars(
            sa.select(PackageArtifact).where(PackageArtifact.package_id == package.id)
        )
    )
    expected_artifacts = {item["format"]: item for item in package.manifest.get("artifacts", [])}
    if len(artifacts) != 3 or {a.format for a in artifacts} != {"pdf", "xlsx", "zip"}:
        fail(409, "INTEGRITY_FAILURE", "The candidate is missing required files.")
    for artifact in artifacts:
        expected = expected_artifacts.get(artifact.format)
        if (
            not expected
            or expected["sha256"] != artifact.sha256
            or expected["byte_size"] != artifact.byte_size
        ):
            fail(409, "INTEGRITY_FAILURE", "Candidate files do not match the sealed manifest.")
        if (
            digest(storage(scope.configuration).get(artifact.object_key, artifact.storage_version))
            != artifact.sha256
        ):
            fail(409, "INTEGRITY_FAILURE", "An artifact failed integrity verification.")
    existing = scope.session.scalar(sa.select(Approval).where(Approval.package_id == package.id))
    if existing:
        return record(existing)
    approval = Approval(
        tenant_id=scope.tenant_id,
        package_id=package.id,
        approver_id=scope.actor.user_id,
        manifest_hash=package.manifest_hash,
        revision_hash=revision.sha256,
        attestation=payload.attestation,
    )
    scope.session.add(approval)
    package.status, submission.status = "approved", "approved"
    submission.row_version += 1
    scope.session.flush()
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "package.approved",
        "package",
        package.id,
        {"manifest_hash": package.manifest_hash, "revision_hash": revision.sha256},
    )
    return record(approval)


@router.get("/packages/{package_id}/artifacts/{format}")
def artifact_content(
    package_id: UUID,
    format: Literal["pdf", "xlsx", "zip"],
    scope: Scoped,
    preview: bool = False,
    acknowledge_superseded: bool = False,
) -> Response:
    scope.require("packages.read" if preview else "packages.export")
    package = get(scope.session, Package, package_id)
    submission = get(scope.session, Submission, package.submission_id, lock=True)
    artifact = scope.session.scalar(
        sa.select(PackageArtifact).where(
            PackageArtifact.package_id == package.id, PackageArtifact.format == format
        )
    )
    if artifact is None:
        fail(409, "ARTIFACT_NOT_READY", "The package is still being generated.")
    assert artifact is not None
    approval = scope.session.scalar(sa.select(Approval).where(Approval.package_id == package.id))
    revision = get(scope.session, CanonicalRevision, package.revision_id)
    stale = (
        package.input_version != submission.input_version
        or revision.number != submission.revision_number
    )
    if not preview and (approval is None or stale and not acknowledge_superseded):
        fail(
            409,
            "APPROVAL_REQUIRED",
            "Approve the current package before exporting; acknowledge historical versions explicitly.",
        )
    data = storage(scope.configuration).get(artifact.object_key, artifact.storage_version)
    if digest(data) != artifact.sha256:
        fail(409, "INTEGRITY_FAILURE", "The file failed integrity verification.")
    if not preview and approval:
        scope.session.add(
            ExportEvent(
                tenant_id=scope.tenant_id,
                package_id=package.id,
                approval_id=approval.id,
                actor_id=scope.actor.user_id,
                format=format,
            )
        )
        if not stale and submission.status not in {"closed", "cancelled"}:
            submission.status = "exported"
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "package.previewed" if preview else "package.exported",
        "package",
        package.id,
    )
    filename = (
        "SUPERSEDED_" if stale else "UNAPPROVED_" if preview and not approval else ""
    ) + artifact.filename
    return Response(
        data,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": ("inline" if preview and format == "pdf" else "attachment")
            + "; filename*=UTF-8''"
            + quote(filename),
            "X-Package-Status": "superseded" if stale else "approved" if approval else "unapproved",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.post("/submissions/{submission_id}/tasks", status_code=201, response_model=TaskResponse)
def create_task(submission_id: UUID, payload: TaskInput, scope: Scoped) -> dict[str, Any]:
    scope.require("reviews.write")
    ensure_open(get(scope.session, Submission, submission_id, lock=True))
    owner = get(scope.session, Membership, payload.owner_membership_id or scope.membership_id)
    if owner.status != "active":
        fail(422, "OWNER_INACTIVE", "Assign an active agency member.")
    row = WorkItem(
        tenant_id=scope.tenant_id,
        submission_id=submission_id,
        owner_id=owner.user_id,
        **payload.model_dump(exclude={"owner_membership_id"}),
    )
    scope.session.add(row)
    scope.session.flush()
    audit(scope.session, scope.tenant_id, scope.membership_id, "task.created", "task", row.id)
    return record(row)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def edit_task(task_id: UUID, payload: TaskEdit, scope: Scoped) -> dict[str, Any]:
    scope.require("reviews.write")
    candidate = get(scope.session, WorkItem, task_id)
    ensure_open(get(scope.session, Submission, candidate.submission_id, lock=True))
    row = get(scope.session, WorkItem, task_id, lock=True)
    if row.row_version != payload.expected_version:
        fail(409, "STALE_TASK", "This task changed. Refresh before editing.")
    if payload.status == "completed" and len(payload.resolution_reason.strip()) < 5:
        fail(422, "RESOLUTION_REQUIRED", "Record how the task was resolved.")
    if "owner_membership_id" in payload.model_fields_set:
        owner = get(scope.session, Membership, payload.owner_membership_id or scope.membership_id)
        if owner.status != "active":
            fail(422, "OWNER_INACTIVE", "Assign an active agency member.")
        row.owner_id = owner.user_id
    if "due_date" in payload.model_fields_set:
        row.due_date = payload.due_date
    if payload.status:
        row.status = payload.status
        row.resolution_reason = payload.resolution_reason if payload.status == "completed" else None
    row.row_version += 1
    audit(scope.session, scope.tenant_id, scope.membership_id, "task.updated", "task", row.id)
    return record(row)


@router.get("/tasks")
def task_queue(scope: Scoped) -> dict[str, Any]:
    scope.require("reviews.read")
    return {
        "items": [
            record(t)
            for t in scope.session.scalars(
                sa.select(WorkItem).order_by(WorkItem.due_date.asc().nulls_last()).limit(100)
            )
        ]
    }


@router.get("/submissions/{submission_id}/audit")
def audit_history(submission_id: UUID, scope: Scoped) -> dict[str, Any]:
    scope.require("audit.read")
    get(scope.session, Submission, submission_id)
    submission = get(scope.session, Submission, submission_id)
    ids = [
        submission_id,
        submission.insured_id,
        *scope.session.scalars(
            sa.select(DocumentVersion.id).where(DocumentVersion.submission_id == submission_id)
        ),
        *scope.session.scalars(sa.select(Package.id).where(Package.submission_id == submission_id)),
    ]
    return {
        "items": [
            record(event)
            for event in scope.session.scalars(
                sa.select(AuditEvent)
                .where(AuditEvent.object_id.in_(ids))
                .order_by(AuditEvent.created_at.desc())
                .limit(200)
            )
        ]
    }


@router.get("/agency")
def agency_settings(scope: Scoped) -> dict[str, Any]:
    scope.require("members.read")
    members = (
        scope.session.execute(
            sa.text("""SELECT m.id,m.user_id,m.status,m.row_version,u.display_name
        FROM app.membership m JOIN app.app_user u ON u.id=m.user_id ORDER BY u.display_name""")
        )
        .mappings()
        .all()
    )
    output = []
    for member in members:
        roles = scope.session.scalars(
            sa.select(Role.key)
            .join(MembershipRole, MembershipRole.role_id == Role.id)
            .where(MembershipRole.membership_id == member["id"])
        ).all()
        output.append(
            {**{k: str(v) if isinstance(v, UUID) else v for k, v in member.items()}, "roles": roles}
        )
    invites = (
        scope.session.scalars(
            sa.select(Invitation).order_by(Invitation.created_at.desc()).limit(50)
        ).all()
        if "members.manage" in scope.actor.permissions
        else []
    )
    usage = (
        scope.session.execute(
            sa.select(UsageEvent.kind, sa.func.sum(UsageEvent.quantity)).group_by(UsageEvent.kind)
        ).all()
        if "usage.read" in scope.actor.permissions
        else []
    )
    return {
        "agency": record(get(scope.session, Tenant, scope.tenant_id)),
        "members": output,
        "invitations": [record(i, {"token_hash"}) for i in invites],
        "usage": [{"kind": k, "quantity": n} for k, n in usage],
        "synthetic_only": True,
    }


@router.post("/invitations", status_code=201)
def invite(payload: InvitationInput, request: Request, scope: Scoped) -> dict[str, Any]:
    scope.require("members.manage")
    email = payload.email.strip().lower()
    from app.database.models import AppUser

    with database(request).identity() as identity_session:
        existing_member = identity_session.scalar(
            sa.select(Membership.id)
            .join(AppUser, AppUser.id == Membership.user_id)
            .where(
                Membership.tenant_id == scope.tenant_id,
                AppUser.email == email,
                Membership.status == "active",
            )
        )
    if existing_member:
        fail(409, "ALREADY_A_MEMBER", "Manage this existing colleague through team access.")
    if "@" not in email or any(c.isspace() for c in email):
        fail(422, "INVALID_EMAIL", "Enter a valid email address.")
    scope.session.execute(
        sa.update(Invitation)
        .where(
            Invitation.email == email,
            Invitation.status == "pending",
            Invitation.expires_at <= now(),
        )
        .values(status="expired")
    )
    token = secrets.token_urlsafe(32)
    row = Invitation(
        tenant_id=scope.tenant_id,
        email=email,
        token_hash=digest(token),
        inviter_membership_id=scope.membership_id,
        expires_at=now() + timedelta(days=7),
        role_keys=list(dict.fromkeys(payload.roles)),
    )
    scope.session.add(row)
    scope.session.flush()
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "invitation.created",
        "invitation",
        row.id,
    )
    return {
        "invitation": record(row, {"token_hash"}),
        "token": token,
        "url": scope.configuration.public_origin + "/invite?token=" + quote(token),
    }


@router.post("/invitations/accept")
def accept_invite(
    payload: InvitationAcceptance, request: Request, actor: Authenticated
) -> dict[str, Any]:
    if not actor.mfa_verified:
        fail(403, "MFA_REQUIRED", "Complete multi-factor authentication first.")
    token = payload.token
    if not 20 <= len(token) <= 100:
        fail(400, "INVITATION_INVALID", "This invitation is invalid or expired.")
    with database(request).identity() as session:
        try:
            with session.begin_nested():
                tenant_id = session.scalar(
                    sa.text("SELECT app.accept_invitation(:token, :user)"),
                    {"token": digest(token), "user": actor.user_id},
                )
        except sa.exc.DBAPIError:
            fail(400, "INVITATION_INVALID", "This invitation is invalid, already used, or expired.")
        from app.database.models import AuthSession

        row = session.get(AuthSession, actor.session_id)
        assert row is not None
        row.active_tenant_id = tenant_id
    return {"tenant_id": str(tenant_id)}


@router.patch("/memberships/{membership_id}")
def edit_membership(membership_id: UUID, payload: MembershipEdit, scope: Scoped) -> dict[str, Any]:
    scope.require("members.manage")
    # Serialize agency membership changes, including last-admin and approval races.
    get(scope.session, Tenant, scope.tenant_id, lock=True)
    member = get(scope.session, Membership, membership_id, lock=True)
    if member.row_version != payload.expected_version:
        fail(409, "STALE_MEMBERSHIP", "Membership changed. Refresh before saving.")
    admins = scope.session.scalars(
        sa.select(Membership.id)
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(Role, Role.id == MembershipRole.role_id)
        .where(Membership.status == "active", Role.key == "tenant_admin")
    ).all()
    if (
        membership_id in admins
        and len(admins) == 1
        and (payload.status != "active" or "tenant_admin" not in payload.roles)
    ):
        fail(409, "LAST_ADMINISTRATOR", "Keep at least one active agency administrator.")
    # Delete privilege is limited to role assignments; evidence/history cannot be deleted.
    scope.session.execute(
        sa.delete(MembershipRole).where(MembershipRole.membership_id == member.id)
    )
    for role in scope.session.scalars(sa.select(Role).where(Role.key.in_(payload.roles))):
        scope.session.add(
            MembershipRole(
                tenant_id=scope.tenant_id,
                membership_id=member.id,
                role_id=role.id,
                granted_by_membership_id=scope.membership_id,
            )
        )
    member.status = payload.status
    member.row_version += 1
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "membership.updated",
        "membership",
        member.id,
        {"roles": payload.roles, "status": payload.status},
    )
    return record(member)


class MeasurementInput(Input):
    baseline_minutes: int = Field(ge=0, le=100000)
    actual_minutes: int = Field(ge=0, le=100000)
    note: str = Field(min_length=5, max_length=1000)


@router.post("/submissions/{submission_id}/measurements", status_code=201)
def measure(
    submission_id: UUID,
    payload: MeasurementInput,
    scope: Scoped,
    idempotency_key: str | None = Header(default=None),
) -> dict[str, Any]:
    scope.require("reviews.write")
    get(scope.session, Submission, submission_id, lock=True)
    object_id, existing = idempotent(
        scope,
        "measurement.record",
        idempotency_key,
        {"submission": submission_id, **payload.model_dump()},
    )
    if existing:
        return record(get(scope.session, UsageEvent, object_id))
    row = UsageEvent(
        id=object_id,
        tenant_id=scope.tenant_id,
        submission_id=submission_id,
        kind="broker_preparation_minutes",
        quantity=payload.actual_minutes,
        estimated_cost=None,
        metadata_json={
            "baseline_minutes": payload.baseline_minutes,
            "minutes_saved": payload.baseline_minutes - payload.actual_minutes,
            "method": "broker_reported",
            "note": payload.note,
            "reviewer_id": str(scope.actor.user_id),
        },
    )
    scope.session.add(row)
    scope.session.flush()
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "measurement.recorded",
        "submission",
        submission_id,
    )
    return record(row)

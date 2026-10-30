"""Public workspace response contracts, consumed by generated TypeScript types."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class PublicRecord(BaseModel):
    id: UUID
    tenant_id: UUID
    created_at: datetime


class InsuredResponse(PublicRecord):
    name: str
    dba: str | None
    entity_type: str | None
    mailing_address: str | None
    operations: str | None
    external_reference: str | None
    status: str
    row_version: int


class SubmissionResponse(PublicRecord):
    insured_id: UUID
    prior_submission_id: UUID | None
    title: str
    kind: str
    intended_start: date
    intended_end: date
    due_date: date | None
    loss_required_start: date | None
    loss_required_end: date | None
    assigned_membership_id: UUID
    status: str
    input_version: int
    row_version: int
    revision_number: int
    requirements: dict[str, JsonValue]
    renewal_candidates: list[dict[str, JsonValue]]
    insured_name: str | None = None


class DocumentResponse(PublicRecord):
    submission_id: UUID
    supersedes_id: UUID | None
    filename: str
    media_type: str
    sha256: str
    byte_size: int
    scan_status: str
    processing_status: str
    document_type: str
    page_count: int
    excluded_reason: str | None
    excluded_by: UUID | None
    excluded_at: datetime | None
    processing_inventory: dict[str, JsonValue]
    uploaded_by: UUID


class CellResponse(BaseModel):
    coordinate: str
    raw_value: str
    number_format: str
    data_type: str
    formula: str | None
    cached_value: str | None
    hidden_column: bool


class LocatorResponse(BaseModel):
    kind: str
    page: int | None = None
    line: int | None = None
    sheet: str | None = None
    range: str | None = None
    method: str | None = None
    warnings: list[str] = Field(default_factory=list)
    cells: list[CellResponse] | None = None
    hidden_row: bool = False
    hidden_sheet: bool = False
    page_status: str | None = None
    page_width: float | None = None
    page_height: float | None = None
    rotation: int | None = None
    truncated: bool = False
    original_length: int | None = None
    offset: int | None = None
    sheet_inventory: bool = False
    hidden_rows: list[int] = Field(default_factory=list)
    hidden_columns: list[int] = Field(default_factory=list)
    merged_ranges: list[str] = Field(default_factory=list)


class SourceResponse(PublicRecord):
    document_id: UUID
    text: str
    locator: LocatorResponse
    parser_version: str


class ObservationResponse(PublicRecord):
    field: str
    value: JsonValue
    context: dict[str, JsonValue]
    source_id: UUID
    supporting_source_ids: list[UUID]
    document_id: UUID
    method: str
    warnings: list[str]
    availability: str
    support_status: str
    group_key: str
    raw_value: str
    pipeline_version: str


class DecisionResponse(PublicRecord):
    action: str
    value: JsonValue
    context: dict[str, JsonValue]
    reason: str
    observation_id: UUID | None
    field: str
    reviewer_id: UUID
    support: dict[str, JsonValue]


class RenewalCandidateResponse(BaseModel):
    key: str
    field: str
    value: JsonValue
    context: dict[str, JsonValue]
    prior_revision_id: UUID
    prior_revision_hash: str
    prior_fact: dict[str, JsonValue]
    confirmed_for_current_period: bool


class GroupResponse(BaseModel):
    key: str
    field: str
    label: str
    context: dict[str, JsonValue]
    observations: list[ObservationResponse]
    fingerprint: str
    conflicting: bool
    verified: bool
    state: str
    decision: DecisionResponse | None
    candidate: RenewalCandidateResponse | None = None


class BlockerResponse(BaseModel):
    key: str
    message: str
    waivable: bool
    fingerprint: str | None = None


class ExceptionResponse(BaseModel):
    key: str
    message: str
    reason: str | None
    accepted_by: UUID | None = None
    input_fingerprint: str | None = None


class CanonicalFactResponse(BaseModel):
    group_key: str
    field: str
    value: JsonValue
    context: dict[str, JsonValue]
    source_ids: list[UUID]
    original_source_ids: list[UUID]
    decision_id: UUID
    reviewer_id: UUID
    reason: str
    evidence_type: str
    attestation_origin: str | None
    renewal_lineage: RenewalCandidateResponse | None = None


class ReviewStateResponse(BaseModel):
    ready: bool
    blockers: list[BlockerResponse]
    exceptions: list[ExceptionResponse]
    groups: list[GroupResponse]
    facts: list[CanonicalFactResponse]
    verified_count: int
    total_count: int
    input_version: int
    requirement_profile: str
    document_inventory: list[dict[str, JsonValue]]


class PackageResponse(PublicRecord):
    status: str
    revision_id: UUID
    input_version: int
    manifest_hash: str | None
    manifest: dict[str, JsonValue]
    template_version: str
    approval: dict[str, JsonValue] | None = None
    superseded: bool = False


class JobResponse(PublicRecord):
    status: str
    stage: str
    kind: str
    document_id: UUID | None
    error_code: str | None
    attempts: int


class TaskResponse(PublicRecord):
    submission_id: UUID
    title: str
    description: str
    status: str
    due_date: date | None
    owner_id: UUID | None
    row_version: int
    resolution_reason: str | None


class MemberSummary(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str


class WorkspaceResponse(BaseModel):
    submission: SubmissionResponse
    insured: InsuredResponse
    documents: list[DocumentResponse]
    jobs: list[JobResponse]
    readiness: ReviewStateResponse
    sources: list[SourceResponse]
    packages: list[PackageResponse]
    tasks: list[TaskResponse]
    members: list[MemberSummary]
    issues: list[dict[str, JsonValue]]
    field_labels: dict[str, str]

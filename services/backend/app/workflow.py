"""Deterministic review, readiness and immutable canonical revision services."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.common import audit, canonical_json, digest, fail, record
from app.database.models import Membership, MembershipRole, OutboxEvent, Permission, RolePermission
from app.domain.cgl import (
    FIELD_LABELS,
    fingerprint,
    group_key,
    reconcile,
    validate_fact,
)
from app.domain.models import (
    CanonicalRevision,
    Discrepancy,
    DocumentVersion,
    IdempotencyRecord,
    InsuredAccount,
    Observation,
    ProcessingJob,
    ReviewDecision,
    Submission,
)
from app.identity import Scope


def get[T](session: Session, model: type[T], object_id: UUID, *, lock: bool = False) -> T:
    statement = sa.select(model).where(getattr(model, "id") == object_id)  # noqa: B009
    if lock:
        statement = statement.with_for_update()
    result = session.scalar(statement)
    if result is None:
        fail(404, "NOT_FOUND", "Record not found in this agency.")
    assert result is not None
    return result


def version_check(submission: Submission, expected: int) -> None:
    if submission.row_version != expected:
        fail(
            409,
            "STALE_WORKSPACE",
            "This workspace changed. Refresh and review the latest information.",
        )


def idempotent(scope: Scope, operation: str, key: str | None, value: Any) -> tuple[UUID, bool]:
    if not key or not 8 <= len(key) <= 100:
        fail(400, "IDEMPOTENCY_REQUIRED", "Supply an Idempotency-Key of 8–100 characters.")
    assert key is not None
    request_hash = digest(canonical_json(value))
    existing = scope.session.scalar(
        sa.select(IdempotencyRecord).where(
            IdempotencyRecord.operation == operation, IdempotencyRecord.key == key
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            fail(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This request key was already used with different information.",
            )
        return existing.response_id, True
    response_id = uuid4()
    scope.session.add(
        IdempotencyRecord(
            tenant_id=scope.tenant_id,
            operation=operation,
            key=key,
            request_hash=request_hash,
            response_id=response_id,
        )
    )
    scope.session.flush()
    return response_id, False


def active_documents(session: Session, submission_id: UUID) -> list[DocumentVersion]:
    return list(
        session.scalars(
            sa.select(DocumentVersion)
            .where(
                DocumentVersion.submission_id == submission_id,
                DocumentVersion.excluded_reason.is_(None),
            )
            .order_by(DocumentVersion.created_at)
        )
    )


def observation_groups(session: Session, submission_id: UUID) -> list[dict[str, Any]]:
    rows = session.scalars(
        sa.select(Observation)
        .join(DocumentVersion, DocumentVersion.id == Observation.document_id)
        .where(
            Observation.submission_id == submission_id, DocumentVersion.excluded_reason.is_(None)
        )
        .order_by(Observation.created_at, Observation.id)
    ).all()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in rows:
        grouped[observation.group_key].append(record(observation))
    latest: dict[str, ReviewDecision] = {}
    for previous_decision in session.scalars(
        sa.select(ReviewDecision)
        .where(ReviewDecision.submission_id == submission_id)
        .order_by(ReviewDecision.sequence, ReviewDecision.id)
    ):
        latest[previous_decision.group_key] = previous_decision
    # Explicit manual attestations also need review history and a canonical slot.
    for key in latest:
        if key.startswith("manual:"):
            grouped.setdefault(key, [])
    result = []
    for key, observations in grouped.items():
        decision = latest.get(key)
        current = fingerprint(observations)
        valid = decision is not None and decision.input_fingerprint == current
        source = observations[0] if observations else (record(decision) if decision else {})
        result.append(
            {
                "key": key,
                "field": source.get("field"),
                "label": FIELD_LABELS.get(source.get("field", ""), "Fact"),
                "context": source.get("context", {}),
                "observations": observations,
                "fingerprint": current,
                "conflicting": len({canonical_json(o["value"]) for o in observations}) > 1,
                "decision": record(decision) if decision else None,
                "verified": valid,
                "state": decision.action if valid and decision else "unreviewed",
            }
        )
    return sorted(result, key=lambda g: (g["field"] or "", g["key"]))


def refresh_issues(session: Session, submission: Submission) -> None:
    groups = observation_groups(session, submission.id)
    findings = reconcile([o for group in groups for o in group["observations"]])
    existing = {
        row.key: row
        for row in session.scalars(
            sa.select(Discrepancy).where(Discrepancy.submission_id == submission.id)
        )
    }
    active: set[str] = set()
    for finding in findings:
        active.add(finding["key"])
        row = existing.get(finding["key"])
        group = next(g for g in groups if g["key"] == finding["key"])
        resolved = group["verified"]
        if row is None:
            row = Discrepancy(
                tenant_id=submission.tenant_id, submission_id=submission.id, **finding
            )
            session.add(row)
        else:
            if row.input_fingerprint != finding["input_fingerprint"]:
                row.resolution_reason = None
                row.resolved_by = None
            for key, value in finding.items():
                setattr(row, key, value)
        row.status = "resolved" if resolved else "open"
    for key, row in existing.items():
        if row.kind == "conflict" and key not in active:
            row.status = "resolved"
    session.flush()


def approved_facts(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for group in groups:
        decision = group["decision"]
        if group["verified"] and decision and decision["action"] in {"approve", "correct"}:
            source_ids: list[str] = []
            if decision["observation_id"]:
                selected = next(
                    (o for o in group["observations"] if o["id"] == decision["observation_id"]),
                    None,
                )
                if selected:
                    source_ids = [selected["source_id"], *selected["supporting_source_ids"]]
            facts.append(
                {
                    "group_key": group["key"],
                    "field": decision["field"],
                    "value": decision["value"],
                    "context": decision["context"],
                    "decision_id": decision["id"],
                    "reviewer_id": decision["reviewer_id"],
                    "reason": decision["reason"],
                    "source_ids": list(dict.fromkeys(source_ids)),
                    "evidence_type": "document" if source_ids else "broker_attestation",
                }
            )
    return facts


def readiness(session: Session, submission: Submission) -> dict[str, Any]:
    groups = observation_groups(session, submission.id)
    facts = approved_facts(groups)
    blockers: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    documents = active_documents(session, submission.id)
    document_missing: list[tuple[str, str]] = []
    extracted_documents = {o["document_id"] for group in groups for o in group["observations"]}
    for doc in documents:
        if doc.scan_status != "clean" or doc.processing_status != "completed":
            blockers.append(
                {
                    "key": "document:" + str(doc.id),
                    "message": f"{doc.filename}: processing is not complete.",
                    "waivable": False,
                }
            )
        elif str(doc.id) not in extracted_documents or doc.document_type == "unknown":
            document_missing.append(
                (
                    "missing:document_review:" + str(doc.id),
                    f"{doc.filename}: no supported facts were extracted or its document type is unknown. Review the original manually, record material facts, and document the result.",
                )
            )
    for group in groups:
        if not group["verified"]:
            blockers.append(
                {
                    "key": group["key"],
                    "message": f"Review {group['label'].lower()} and its supporting evidence.",
                    "waivable": False,
                }
            )
        elif group["state"] in {"omit", "reject"}:
            exceptions.append(
                {
                    "key": group["key"],
                    "message": "Omitted: " + group["label"],
                    "reason": group["decision"]["reason"],
                }
            )
    comparable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        comparable[group_key(fact["field"], fact["context"], fact["value"])].append(fact)
    for key, values in comparable.items():
        if len({canonical_json(f["value"]) for f in values}) > 1:
            blockers.append(
                {
                    "key": "canonical:" + key,
                    "message": "Reviewed facts conflict in the same context. Correct or omit the conflicting attestation.",
                    "waivable": False,
                }
            )
    if any(f["field"] == "loss.no_losses" for f in facts) and any(
        f["field"] == "loss.claim" for f in facts
    ):
        for no_loss in (f for f in facts if f["field"] == "loss.no_losses"):
            for claim in (f for f in facts if f["field"] == "loss.claim"):
                loss_date = claim["value"].get("loss_date")
                if not loss_date or no_loss["context"].get(
                    "period_start", ""
                ) <= loss_date <= no_loss["context"].get("period_end", "9999"):
                    blockers.append(
                        {
                            "key": "loss:contradiction",
                            "message": "A no-loss statement overlaps a claim or cannot be reconciled with its missing loss date.",
                            "waivable": False,
                        }
                    )
    fields = {f["field"] for f in facts}
    for field in ("insured.legal_name", "insured.operations", "exposure.amount"):
        if field not in fields:
            blockers.append(
                {
                    "key": "required:" + field,
                    "message": f"Verified {FIELD_LABELS[field].lower()} is required.",
                    "waivable": False,
                }
            )
    missing: list[tuple[str, str]] = list(document_missing)
    insured = get(session, InsuredAccount, submission.insured_id)

    def normalized_text(value: str) -> str:
        return " ".join(value.casefold().split())

    legal_names = [f["value"] for f in facts if f["field"] == "insured.legal_name"]
    if legal_names and not any(
        normalized_text(value) == normalized_text(insured.name) for value in legal_names
    ):
        missing.append(
            (
                "missing:account_name_mismatch",
                "The reviewed legal insured name differs from the account directory. Confirm the correct entity and document the relationship or correct the record.",
            )
        )
    if not any(field.startswith("coverage.") for field in fields):
        missing.append(
            (
                "missing:coverage",
                "No coverage information was verified. Requested or expiring coverage remains unspecified.",
            )
        )
    if "loss.period" not in fields:
        missing.append(
            (
                "missing:loss_runs",
                "Loss runs were not supplied or their report period was not verified. No no-loss conclusion is made.",
            )
        )
    if submission.loss_required_start and submission.loss_required_end:
        periods = [f["context"] for f in facts if f["field"] == "loss.period"]
        ranges = sorted((c.get("period_start", ""), c.get("period_end", "")) for c in periods)
        covered_until = submission.loss_required_start
        for beginning, end in ranges:
            if not beginning or not end:
                continue
            start, finish = date.fromisoformat(beginning), date.fromisoformat(end)
            if start > covered_until:
                break
            if finish >= covered_until:
                from datetime import timedelta

                covered_until = finish + timedelta(days=1)
        if covered_until <= submission.loss_required_end:
            missing.append(
                (
                    "missing:loss_period",
                    "Verified loss reports do not cover the broker-requested history period.",
                )
            )
        for context in periods:
            if context.get("as_of") and (
                date.today() - date.fromisoformat(context["as_of"])
            ).days > int(submission.requirements.get("loss_freshness_days", 60)):
                missing.append(
                    (
                        "missing:stale_losses",
                        "A loss report valuation exceeds the configured freshness requirement.",
                    )
                )
    else:
        missing.append(
            (
                "missing:loss_requirement",
                "The broker has not specified a loss-history period requirement.",
            )
        )
    policy_periods: dict[str, dict[str, str]] = defaultdict(dict)
    for fact in facts:
        if fact["field"] in {"policy.effective", "policy.expiration"}:
            policy_periods[canonical_json(fact["context"])][fact["field"]] = fact["value"]
    for context_key, dates in policy_periods.items():
        if (
            dates.get("policy.effective")
            and dates.get("policy.expiration")
            and dates["policy.expiration"] <= dates["policy.effective"]
        ):
            blockers.append(
                {
                    "key": "policy:invalid_period:" + digest(context_key),
                    "message": "A reviewed policy expiration does not follow its effective date. Correct or omit the unsupported policy date with a reason.",
                    "waivable": False,
                }
            )
    claims: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if fact["field"] == "loss.claim":
            claim = fact["value"]
            claims[(claim["carrier"], claim["policy"], claim["claim_id"])].append(fact)
    for identity, values in claims.items():
        if len(values) > 1:
            blockers.append(
                {
                    "key": "duplicate:" + digest(canonical_json(identity)),
                    "message": "Multiple approved valuations of one claim would double-count loss history. Omit all but the intended valuation.",
                    "waivable": False,
                }
            )
    for fact in facts:
        if fact["field"] == "operations.class_code" and not fact["context"].get(
            "classification_system"
        ):
            missing.append(
                (
                    "missing:class_namespace",
                    "A supplied classification code has no verified carrier or rating-system namespace. It must not be treated as interchangeable.",
                )
            )
        if fact["field"] == "exposure.amount" and fact["context"].get("role") == "unspecified":
            missing.append(
                (
                    "missing:exposure_status",
                    "An exposure amount is not identified as an actual, historical figure, or estimate.",
                )
            )
        if fact["field"] == "loss.claim":
            value = fact["value"]
            amounts = [value.get(key) for key in ("paid", "reserves", "incurred")]
            if all(amount is not None for amount in amounts) and Decimal(amounts[0]) + Decimal(
                amounts[1]
            ) != Decimal(amounts[2]):
                missing.append(
                    (
                        "missing:claim_components:" + fact["group_key"],
                        "Paid plus reserves differs from reported incurred. Confirm the report's expense, recovery, and valuation basis; no total is recalculated.",
                    )
                )
            if date.fromisoformat(value["valuation"]) > date.today():
                missing.append(
                    (
                        "missing:future_valuation",
                        "A claim valuation is future-dated. Confirm the report date.",
                    )
                )
    resolutions = {
        row.key: row
        for row in session.scalars(
            sa.select(Discrepancy).where(
                Discrepancy.submission_id == submission.id, Discrepancy.kind == "missing"
            )
        )
    }
    missing_hash = digest(
        canonical_json(
            {
                "input": submission.input_version,
                "requirements": submission.requirements,
                "facts": facts,
            }
        )
    )
    for key, message in dict(missing).items():
        resolution = resolutions.get(key)
        accepted = (
            resolution is not None
            and resolution.status == "accepted"
            and resolution.input_fingerprint == missing_hash
        )
        if accepted:
            exceptions.append(
                {
                    "key": key,
                    "message": message,
                    "reason": resolution.resolution_reason if resolution else None,
                }
            )
        else:
            blockers.append(
                {"key": key, "message": message, "waivable": True, "fingerprint": missing_hash}
            )
    return {
        "ready": not blockers,
        "blockers": blockers,
        "exceptions": exceptions,
        "facts": facts,
        "groups": groups,
        "input_version": submission.input_version,
        "verified_count": sum(g["verified"] for g in groups),
        "total_count": len(groups),
    }


def save_revision(scope: Scope, submission: Submission) -> CanonicalRevision:
    state = readiness(scope.session, submission)
    snapshot = {
        "schema_version": "cgl-1",
        "submission_id": str(submission.id),
        "intended_start": str(submission.intended_start),
        "intended_end": str(submission.intended_end),
        "facts": state["facts"],
        "exceptions": state["exceptions"],
        "omissions": [
            {"group_key": g["key"], "reason": g["decision"]["reason"]}
            for g in state["groups"]
            if g["verified"] and g["state"] in {"omit", "reject"}
        ],
    }
    submission.revision_number += 1
    row = CanonicalRevision(
        tenant_id=scope.tenant_id,
        submission_id=submission.id,
        number=submission.revision_number,
        input_version=submission.input_version,
        snapshot=snapshot,
        sha256=digest(canonical_json(snapshot)),
        created_by=scope.actor.user_id,
    )
    scope.session.add(row)
    scope.session.flush()
    return row


def enqueue(
    scope: Scope,
    submission_id: UUID,
    *,
    document_id: UUID | None = None,
    package_id: UUID | None = None,
    kind: str = "document",
) -> ProcessingJob:
    job = ProcessingJob(
        id=uuid4(),
        tenant_id=scope.tenant_id,
        submission_id=submission_id,
        actor_user_id=scope.actor.user_id,
        document_id=document_id,
        package_id=package_id,
        kind=kind,
    )
    scope.session.add(job)
    scope.session.flush()
    scope.session.add(
        OutboxEvent(
            tenant_id=scope.tenant_id,
            event_type="processing.requested",
            aggregate_type="job",
            aggregate_id=job.id,
            dedupe_key=str(job.id),
            payload={
                "job_id": str(job.id),
                "tenant_id": str(scope.tenant_id),
                "user_id": str(scope.actor.user_id),
            },
        )
    )
    return job


def verify_approval_authority(scope: Scope) -> None:
    membership = scope.session.scalar(
        sa.select(Membership).where(Membership.id == scope.membership_id).with_for_update(read=True)
    )
    permission = scope.session.scalar(
        sa.select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(MembershipRole, MembershipRole.role_id == RolePermission.role_id)
        .where(
            MembershipRole.membership_id == scope.membership_id,
            Permission.key == "packages.approve",
        )
        .limit(1)
    )
    if membership is None or membership.status != "active" or permission is None:
        fail(403, "APPROVER_REQUIRED", "Current approval authority is required.")


def review(
    scope: Scope, submission: Submission, decisions: list[dict[str, Any]]
) -> CanonicalRevision:
    scope.require("reviews.write")
    groups = {g["key"]: g for g in observation_groups(scope.session, submission.id)}
    keys = [item["group_key"] for item in decisions]
    if len(set(keys)) != len(keys):
        fail(422, "DUPLICATE_DECISION", "Review each group only once in a batch.")
    sequence = submission.revision_number + 1
    for item in decisions:
        key = item["group_key"]
        group = groups.get(key)
        if not group and not key.startswith("manual:"):
            fail(404, "GROUP_NOT_FOUND", "Review group not found.")
        if group and item.get("fingerprint") != group["fingerprint"]:
            fail(409, "EVIDENCE_CHANGED", "Refresh the current evidence before saving a decision.")
        selected = None
        if item.get("observation_id"):
            selected = next(
                (
                    o
                    for o in (group["observations"] if group else [])
                    if o["id"] == str(item["observation_id"])
                ),
                None,
            )
            if selected is None:
                fail(422, "INVALID_EVIDENCE", "Select evidence from this review group.")
        field = group["field"] if group else item.get("field")
        if item["action"] == "approve" and selected is None:
            fail(422, "EVIDENCE_REQUIRED", "Select the document observation you verified.")
        value = selected["value"] if selected and item["action"] == "approve" else item.get("value")
        context = (
            selected["context"]
            if selected and item["action"] == "approve"
            else item.get("context", group["context"] if group else {})
        )
        if not isinstance(field, str):
            fail(422, "FIELD_REQUIRED", "Choose a supported CGL field.")
        if item["action"] in {"approve", "correct"}:
            try:
                value, context = validate_fact(field, value, context)
            except (ValueError, TypeError) as error:
                fail(422, "INVALID_FACT", str(error))
        else:
            value = None
        row = ReviewDecision(
            tenant_id=scope.tenant_id,
            submission_id=submission.id,
            sequence=sequence,
            group_key=key,
            input_fingerprint=group["fingerprint"] if group else fingerprint([]),
            action=item["action"],
            field=field,
            value=value,
            context=context,
            observation_id=UUID(selected["id"]) if selected else None,
            reason=item["reason"],
            reviewer_id=scope.actor.user_id,
        )
        scope.session.add(row)
    scope.session.flush()
    submission.row_version += 1
    submission.status = "in_review"
    refresh_issues(scope.session, submission)
    revision = save_revision(scope, submission)
    audit(
        scope.session,
        scope.tenant_id,
        scope.membership_id,
        "review.saved",
        "submission",
        submission.id,
        {"decision_count": len(decisions), "revision": revision.number},
    )
    return revision

"""Deterministic review, readiness and immutable canonical revision services."""

import re
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
    REQUIREMENT_PROFILE,
    RULE_VERSION,
    SCHEMA_VERSION,
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
    SourceBlock,
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


def ensure_open(submission: Submission) -> None:
    if submission.status in {"closed", "cancelled"}:
        fail(
            409,
            "SUBMISSION_TERMINAL",
            "This submission is closed or cancelled. Create a renewal or a new submission to continue.",
        )


def idempotent(scope: Scope, operation: str, key: str | None, value: Any) -> tuple[UUID, bool]:
    if not key or not 8 <= len(key) <= 100:
        fail(400, "IDEMPOTENCY_REQUIRED", "Supply an Idempotency-Key of 8–100 characters.")
    assert key is not None
    # Serialize identical keys before checking/inserting, including concurrent creates.
    # A transaction-scoped lock releases automatically on commit or rollback.
    scope.session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{scope.tenant_id}:{operation}:{key}"},
    )
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


def submission_documents(session: Session, submission_id: UUID) -> list[DocumentVersion]:
    return list(
        session.scalars(
            sa.select(DocumentVersion)
            .where(
                DocumentVersion.submission_id == submission_id,
            )
            .order_by(DocumentVersion.created_at)
        )
    )


def active_documents(session: Session, submission_id: UUID) -> list[DocumentVersion]:
    return [d for d in submission_documents(session, submission_id) if not d.excluded_reason]


def document_inventory(session: Session, submission_id: UUID) -> list[dict[str, Any]]:
    documents = submission_documents(session, submission_id)
    observations = list(
        session.scalars(sa.select(Observation).where(Observation.submission_id == submission_id))
    )
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "sha256": d.sha256,
            "included_in_exhibits": not bool(d.excluded_reason),
            "excluded_reason": d.excluded_reason,
            "excluded_by": str(d.excluded_by) if d.excluded_by else None,
            "excluded_at": d.excluded_at.isoformat() if d.excluded_at else None,
            "supersedes_id": str(d.supersedes_id) if d.supersedes_id else None,
            "replaced_by": [str(r.id) for r in documents if r.supersedes_id == d.id],
            "scan_status": d.scan_status,
            "processing_status": d.processing_status,
            "affected_requirements": sorted(
                {o.field for o in observations if o.document_id == d.id}
            ),
            "processing_inventory": d.processing_inventory,
            "source_assertions": [
                {
                    "id": str(o.id),
                    "field": o.field,
                    "value": o.value,
                    "context": o.context,
                    "availability": o.availability,
                    "source_ids": [str(o.source_id), *o.supporting_source_ids],
                }
                for o in observations
                if o.document_id == d.id
            ],
        }
        for d in documents
    ]


def observation_groups(session: Session, submission_id: UUID) -> list[dict[str, Any]]:
    active_source_ids = {
        str(ref)
        for ref in session.scalars(
            sa.select(SourceBlock.id)
            .join(DocumentVersion, DocumentVersion.id == SourceBlock.document_id)
            .where(
                DocumentVersion.submission_id == submission_id,
                DocumentVersion.excluded_reason.is_(None),
            )
        )
    }
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
        if valid and decision and decision.action == "approve":
            selected = next(
                (o for o in observations if o["id"] == str(decision.observation_id)), None
            )
            if (
                not selected
                or selected.get("support_status", "supported") != "supported"
                or selected.get("availability", "available") != "available"
            ):
                valid = False
        # Legacy corrections must be re-reviewed with an explicit support choice.
        if valid and decision and decision.action == "correct" and not decision.support:
            valid = False
        if (
            valid
            and decision
            and decision.action == "correct"
            and decision.support.get("type") == "document"
        ):
            valid = bool(decision.support.get("source_ids")) and set(
                decision.support["source_ids"]
            ).issubset(active_source_ids)
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
    submission = get(session, Submission, submission_id)
    for candidate in submission.renewal_candidates:
        key = candidate["key"]
        decision = latest.get(key)
        current = digest(canonical_json(candidate))
        valid = bool(
            decision
            and decision.input_fingerprint == current
            and (decision.action == "omit" or decision.action == "correct" and decision.support)
        )
        if (
            valid
            and decision
            and decision.action == "correct"
            and decision.support.get("type") == "document"
        ):
            valid = bool(decision.support.get("source_ids")) and set(
                decision.support["source_ids"]
            ).issubset(active_source_ids)
        result.append(
            {
                "key": key,
                "field": candidate["field"],
                "label": FIELD_LABELS[candidate["field"]],
                "context": candidate["context"],
                "candidate": candidate,
                "observations": [],
                "fingerprint": current,
                "conflicting": False,
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
                tenant_id=submission.tenant_id,
                submission_id=submission.id,
                rule_version=RULE_VERSION,
                **finding,
            )
            session.add(row)
        else:
            if row.input_fingerprint != finding["input_fingerprint"]:
                row.resolution_reason = None
                row.resolved_by = None
            for key, value in finding.items():
                setattr(row, key, value)
        row.status = "resolved" if resolved else "open"
        row.rule_version = RULE_VERSION
    for key, row in existing.items():
        if row.kind == "conflict" and key not in active:
            row.status = "resolved"
    session.flush()


def approved_facts(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for group in groups:
        decision = group["decision"]
        if group["verified"] and decision and decision["action"] in {"approve", "correct"}:
            selected = next(
                (o for o in group["observations"] if o["id"] == decision["observation_id"]), None
            )
            original_sources = (
                [selected["source_id"], *selected["supporting_source_ids"]] if selected else []
            )
            if decision["action"] == "correct" and not selected:
                original_sources = [
                    ref
                    for o in group["observations"]
                    for ref in [o["source_id"], *o["supporting_source_ids"]]
                ]
            support = decision.get("support", {})
            source_ids = (
                original_sources
                if decision["action"] == "approve"
                else support.get("source_ids", [])
            )
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
                    "attestation_origin": support.get("origin"),
                    "original_source_ids": original_sources
                    if decision["action"] == "correct"
                    else [],
                    "renewal_lineage": group.get("candidate"),
                }
            )
    return facts


def readiness(session: Session, submission: Submission) -> dict[str, Any]:
    groups = observation_groups(session, submission.id)
    facts = approved_facts(groups)
    blockers: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    documents = submission_documents(session, submission.id)
    inventory = document_inventory(session, submission.id)
    document_missing: list[tuple[str, str]] = []
    extracted_documents = {o["document_id"] for group in groups for o in group["observations"]}
    for doc in documents:
        if doc.excluded_reason:
            entry = next(d for d in inventory if d["id"] == str(doc.id))
            document_missing.append(
                (
                    "missing:excluded:" + str(doc.id),
                    f"Excluded document {doc.filename} ({doc.processing_status}); reason: {doc.excluded_reason}. "
                    f"Affected fields: {', '.join(entry['affected_requirements']) or 'unknown; manual completeness review required'}. "
                    f"Replacement versions: {', '.join(entry['replaced_by']) or 'none'}. "
                    f"Lost source assertions: {len(entry['source_assertions'])}; see excluded source inventory. Confirm lost material information and replacement adequacy.",
                )
            )
            continue
        segments = doc.processing_inventory.get("segments", [])
        if doc.document_type == "policy":
            segments = [
                {"type": "policy", "source_ids": doc.processing_inventory.get("source_order", [])}
            ]
        for index, segment in enumerate(segments):
            if segment.get("type") != "policy":
                continue
            refs = set(segment.get("source_ids", []))
            present = {
                entry["field"]
                for entry in doc.processing_inventory.get("fields", [])
                if entry.get("support_status", "supported") == "supported"
                and entry.get("context", {}).get("role") in {"existing", "historical"}
                and entry.get("source_ids")
                and (not refs or entry["source_ids"][0] in refs)
            }
            required_policy_fields = {
                "policy.number",
                "policy.effective",
                "policy.expiration",
                "coverage.each_occurrence",
                "coverage.general_aggregate",
            }
            absent = sorted(required_policy_fields - present)
            if absent:
                document_missing.append(
                    (
                        f"missing:policy_inventory:{doc.id}:{index}",
                        f"{doc.filename}: policy content is incomplete; fields not supplied/extracted in this segment: "
                        + ", ".join(FIELD_LABELS[field] for field in absent)
                        + ". Review the original and disclose any permitted omission.",
                    )
                )
        for page in doc.processing_inventory.get("pages", []):
            if page.get("page_status") in {"unresolved", "unreadable", "partial"}:
                document_missing.append(
                    (
                        f"missing:page:{doc.id}:{page.get('page')}",
                        f"{doc.filename}: page {page.get('page')} remains {page['page_status']}. Confirm the missing content.",
                    )
                )
        for issue in doc.processing_inventory.get("issues", []):
            document_missing.append(
                (
                    f"missing:parse:{doc.id}:{issue['source_id']}",
                    f"{doc.filename}: {issue['message']} (source {issue['source_id']}).",
                )
            )
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
        elif group["state"] == "omit":
            document_missing.append(
                (
                    "missing:omission:" + group["key"],
                    "Proposed omission: "
                    + group["label"]
                    + "; context: "
                    + canonical_json(group["context"])
                    + "; source assertions: "
                    + canonical_json([o["raw_value"] for o in group["observations"]])
                    + "; preparer reason: "
                    + group["decision"]["reason"],
                )
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
    supplied_forms = [f for f in facts if f["field"] == "policy.endorsement_form"]
    for fact in facts:
        if fact["field"] == "policy.referenced_endorsement" or (
            fact["field"] == "coverage.endorsement"
            and re.search(r"\b(attached|refer to|see form)\b", str(fact["value"]), re.I)
        ):
            reference = str(fact["value"]).split(" - ")[0].strip()
            if not any(
                (
                    str(form["value"]) == reference
                    or str(form["value"]).startswith(reference + " - ")
                )
                and all(
                    form["context"].get(key) == fact["context"].get(key)
                    for key in ("policy_reference", "role", "scope")
                )
                for form in supplied_forms
            ):
                missing.append(
                    (
                        "missing:endorsement:" + fact["group_key"],
                        "Referenced endorsement wording has not been verified: "
                        + str(fact["value"]),
                    )
                )
    policy_fields: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        if fact["field"].startswith("policy.") and fact["context"].get("policy_reference"):
            policy_fields[str(fact["context"]["policy_reference"])].add(fact["field"])
    for reference, supplied in policy_fields.items():
        for required in ("policy.number", "policy.effective", "policy.expiration"):
            if required not in supplied:
                missing.append(
                    (
                        "missing:policy_field:" + digest(reference + required)[:20],
                        f"{FIELD_LABELS[required]} is not verified for policy {reference}.",
                    )
                )
    # This versioned local profile is provisional until domain sign-off. Each supplied
    # policy/request context needs its core limit pair; a single verified limit is insufficient.
    coverage_contexts: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        if fact["field"].startswith("coverage.") and fact["field"] not in {
            "coverage.trigger",
            "coverage.endorsement",
            "coverage.additional_insured",
        }:
            coverage_contexts[canonical_json(fact["context"])].add(fact["field"])
    for context_key, supplied in coverage_contexts.items():
        for required in ("coverage.each_occurrence", "coverage.general_aggregate"):
            if required not in supplied:
                missing.append(
                    (
                        "missing:coverage_field:" + digest(context_key + required)[:20],
                        f"{FIELD_LABELS[required]} is not verified for {context_key}.",
                    )
                )
    for group in groups:
        unavailable = [
            o for o in group["observations"] if o.get("availability", "available") != "available"
        ]
        if unavailable and not any(f["group_key"] == group["key"] for f in facts):
            missing.append(
                (
                    "missing:availability:" + group["key"],
                    group["label"]
                    + " source availability: "
                    + ", ".join(sorted({o["availability"] for o in unavailable}))
                    + "; source values: "
                    + canonical_json([o["raw_value"] for o in unavailable]),
                )
            )
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
    if not coverage_contexts:
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
        if fact["field"] == "exposure.amount" and (
            fact["context"].get("basis") == "unknown"
            or fact["context"].get("unit") in {None, "unknown"}
        ):
            missing.append(
                (
                    "missing:exposure_basis:" + fact["group_key"],
                    "Exposure basis or unit is unknown: " + canonical_json(fact["context"]),
                )
            )
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
            for component in ("paid", "reserves", "incurred"):
                if value.get(component) is None:
                    missing.append(
                        (
                            "missing:claim_amount:" + fact["group_key"] + ":" + component,
                            f"Claim {value['claim_id']} has no supplied {component} amount. Missing amounts are not zero.",
                        )
                    )
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
                "documents": inventory,
                "decisions": [g["decision"] for g in groups],
                "profile": REQUIREMENT_PROFILE,
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
                    "accepted_by": str(resolution.resolved_by) if resolution else None,
                    "input_fingerprint": missing_hash,
                }
            )
        else:
            blockers.append(
                {"key": key, "message": message, "waivable": True, "fingerprint": missing_hash}
            )
    return {
        "requirement_profile": REQUIREMENT_PROFILE,
        "document_inventory": inventory,
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
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "requirement_profile": REQUIREMENT_PROFILE,
        "document_inventory": state["document_inventory"],
        "submission_id": str(submission.id),
        "intended_start": str(submission.intended_start),
        "intended_end": str(submission.intended_end),
        "facts": state["facts"],
        "exceptions": state["exceptions"],
        "omissions": [
            {"group_key": g["key"], "reason": g["decision"]["reason"]}
            for g in state["groups"]
            if g["verified"] and g["state"] == "omit"
        ],
        "rejections": [
            {
                "group_key": g["key"],
                "reason": g["decision"]["reason"],
                "observation_ids": [o["id"] for o in g["observations"]],
            }
            for g in state["groups"]
            if g["verified"] and g["state"] == "reject"
        ],
    }
    submission.revision_number += 1
    row = CanonicalRevision(
        tenant_id=scope.tenant_id,
        submission_id=submission.id,
        number=submission.revision_number,
        input_version=submission.input_version,
        snapshot=snapshot,
        schema_version=SCHEMA_VERSION,
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
    ensure_open(submission)
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
        support: dict[str, Any] = {}
        if (
            item["action"] == "approve"
            and selected
            and (
                selected.get("availability", "available") != "available"
                or selected.get("support_status", "supported") != "supported"
            )
        ):
            fail(
                422,
                "UNSUPPORTED_EVIDENCE",
                "Supply supported evidence or an explicit broker attestation for this field.",
            )
        if item["action"] == "reject":
            if (
                not group
                or not group["observations"]
                or any(
                    o.get("support_status", "supported") != "unsupported"
                    for o in group["observations"]
                )
            ):
                fail(
                    422,
                    "OMISSION_REQUIRED",
                    "Supported or unavailable source information requires an omission disposition, not rejection as an incorrect extraction.",
                )
            support = {"rejection": "incorrect_extraction"}
        if item["action"] == "correct":
            support_type = item.get("support_type")
            if support_type == "broker_attestation":
                origin = item.get("attestation_origin", "").strip()
                if len(origin) < 5:
                    fail(
                        422,
                        "ATTESTATION_ORIGIN_REQUIRED",
                        "Record who supplied the corrected information and how/when it was confirmed.",
                    )
                support = {"type": support_type, "origin": origin, "source_ids": []}
            elif support_type == "document":
                from app.documents import Block, supports_fact

                refs = [UUID(str(ref)) for ref in item.get("support_source_ids", [])]
                blocks_by_doc: dict[UUID, list[Block]] = defaultdict(list)
                for source in scope.session.scalars(
                    sa.select(SourceBlock)
                    .join(DocumentVersion, DocumentVersion.id == SourceBlock.document_id)
                    .where(
                        DocumentVersion.submission_id == submission.id,
                        DocumentVersion.excluded_reason.is_(None),
                    )
                    .order_by(SourceBlock.created_at, SourceBlock.id)
                ):
                    blocks_by_doc[source.document_id].append(
                        Block(source.text, source.locator, source.id)
                    )
                # Creation timestamps can tie; locators restore stable source order.
                for blocks in blocks_by_doc.values():
                    blocks.sort(
                        key=lambda b: (
                            b.locator.get("sheet", ""),
                            b.locator.get("page", 0),
                            b.locator.get("line", 0),
                            int(re.findall(r"\d+", b.locator.get("range", "A0"))[0]),
                        )
                    )
                if any(
                    ref not in {b.id for blocks in blocks_by_doc.values() for b in blocks}
                    for ref in refs
                ):
                    fail(
                        422,
                        "INVALID_EVIDENCE",
                        "Supporting sources must belong to active documents in this submission.",
                    )
                if not refs or not any(
                    supports_fact(blocks, field, value, context, refs)
                    for blocks in blocks_by_doc.values()
                ):
                    fail(
                        422,
                        "UNSUPPORTED_CORRECTION",
                        "The cited evidence does not support this corrected value and context.",
                    )
                support = {"type": "document", "source_ids": [str(ref) for ref in refs]}
            else:
                fail(
                    422,
                    "CORRECTION_SUPPORT_REQUIRED",
                    "Choose supporting document evidence or an attributed broker attestation.",
                )
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
            support=support,
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

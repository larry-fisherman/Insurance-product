"""At-least-once processing with database leases and a durable transactional outbox."""

import logging
import time
from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from celery import Celery

from app.common import canonical_json, digest, now
from app.config import Settings
from app.database.models import AppUser, Membership, OutboxEvent, Tenant
from app.database.runtime import Database
from app.documents import (
    MAX_PAGES,
    PARSER_VERSION,
    Block,
    DocumentError,
    classify,
    local_extract,
    parse_pdf,
    parse_xlsx,
    processing_inventory,
    provider_extract,
    scan,
    validate_file,
)
from app.domain.cgl import PIPELINE_VERSION, group_key
from app.domain.models import (
    CanonicalRevision,
    DocumentVersion,
    Observation,
    Package,
    PackageArtifact,
    ProcessingJob,
    SourceBlock,
    Submission,
    UsageEvent,
)
from app.rendering import render
from app.storage import storage
from app.workflow import active_documents, get, refresh_issues


def celery_app() -> Celery:
    configuration = Settings()
    broker = (
        configuration.redis_url.get_secret_value()
        if configuration.redis_url
        else "redis://127.0.0.1:56379/0"
    )
    application = Celery("cgl", broker=broker)
    application.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=550,
        task_time_limit=600,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": 900},
        beat_schedule={"recover-and-dispatch": {"task": "cgl.dispatch", "schedule": 5.0}},
    )
    return application


celery = celery_app()


def agency_actors(db: Database) -> list[tuple[UUID, UUID]]:
    with db.identity() as session:
        pairs = session.execute(
            sa.select(Membership.tenant_id, Membership.user_id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(AppUser, AppUser.id == Membership.user_id)
            .where(
                Membership.status == "active", Tenant.status == "active", AppUser.status == "active"
            )
        )
        return list(dict(pairs.all()).items())


@celery.task(name="cgl.dispatch")  # type: ignore[untyped-decorator]
def dispatch() -> None:
    configuration = Settings()
    db = Database(configuration)
    try:
        for tenant_id, user_id in agency_actors(db):
            with db.tenant(tenant_id, user_id) as session:
                events = session.scalars(
                    sa.select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None), OutboxEvent.available_at <= now())
                    .with_for_update(skip_locked=True)
                    .limit(50)
                ).all()
                for event in events:
                    process_task.delay(str(tenant_id), str(user_id), str(event.aggregate_id))
                    event.published_at = now()
                    event.attempts += 1
                # Covers lost broker messages, crashed workers, and redelivery after a committed lease.
                jobs = session.scalars(
                    sa.select(ProcessingJob)
                    .where(
                        sa.or_(
                            sa.and_(
                                ProcessingJob.status == "queued",
                                ProcessingJob.created_at < now() - timedelta(seconds=30),
                                sa.or_(
                                    ProcessingJob.lease_until.is_(None),
                                    ProcessingJob.lease_until <= now(),
                                ),
                            ),
                            sa.and_(
                                ProcessingJob.status == "running", ProcessingJob.lease_until < now()
                            ),
                        )
                    )
                    .with_for_update(skip_locked=True)
                    .limit(30)
                ).all()
                for job in jobs:
                    if job.attempts >= 3:
                        job.status, job.error_code = "failed", "ATTEMPTS_EXHAUSTED"
                        if job.document_id:
                            get(
                                session, DocumentVersion, job.document_id
                            ).processing_status = "failed"
                        if job.package_id:
                            get(session, Package, job.package_id).status = "failed"
                    else:
                        job.status = "queued"
                        process_task.delay(str(tenant_id), str(user_id), str(job.id))
    finally:
        db.close()


@celery.task(name="cgl.process")  # type: ignore[untyped-decorator]
def process_task(tenant_id: str, user_id: str, job_id: str) -> None:
    process_job(Settings(), UUID(tenant_id), UUID(user_id), UUID(job_id))


def process_job(configuration: Settings, tenant_id: UUID, user_id: UUID, job_id: UUID) -> None:
    db = Database(configuration)
    store = storage(configuration)
    started_at = time.perf_counter()
    claimed_attempt = 0
    try:
        with db.tenant(tenant_id, user_id) as session:
            job = get(session, ProcessingJob, job_id, lock=True)
            if job.status == "completed" or job.status == "failed":
                return
            if job.status == "running" and job.lease_until and job.lease_until > now():
                return
            if job.status == "queued" and job.lease_until and job.lease_until > now():
                return
            job.status, job.stage = "running", "processing"
            job.attempts += 1
            claimed_attempt = job.attempts
            job.heartbeat_at, job.lease_until = now(), now() + timedelta(minutes=12)
            kind, submission_id, document_id, package_id = (
                job.kind,
                job.submission_id,
                job.document_id,
                job.package_id,
            )
        if kind == "document" and document_id:
            with db.tenant(tenant_id, user_id) as session:
                document = get(session, DocumentVersion, document_id)
                key, version, sha, signature, filename = (
                    document.object_key,
                    document.storage_version,
                    document.sha256,
                    document.fixture_signature,
                    document.filename,
                )
            data = store.get(key, version)
            if digest(data) != sha:
                raise DocumentError("INTEGRITY_FAILURE")
            media_type = validate_file(filename, data)
            scanner = scan(data, signature, configuration)
            # Persist parsed evidence before extraction/provider calls. Retries reuse this
            # checkpoint and its immutable source IDs instead of duplicating evidence.
            with db.tenant(tenant_id, user_id) as session:
                existing = list(
                    session.scalars(
                        sa.select(SourceBlock)
                        .where(SourceBlock.document_id == document_id)
                        .order_by(SourceBlock.created_at, SourceBlock.id)
                    )
                )
                document = get(session, DocumentVersion, document_id)
                checkpoint = document.processing_inventory.get("parse_complete", False)
                page_count = document.page_count
            if checkpoint:
                blocks = [Block(row.text, row.locator, row.id) for row in existing]
                order = {
                    ref: i
                    for i, ref in enumerate(document.processing_inventory.get("source_order", []))
                }
                blocks.sort(key=lambda b: order.get(str(b.id), len(order)))
            else:
                blocks, page_count = (
                    parse_pdf(data) if media_type == "application/pdf" else parse_xlsx(data)
                )
            document_type = classify(blocks)
            local_candidates = local_extract(blocks, document_type)
            segment_types = {
                segment["type"]
                for segment in processing_inventory(blocks, local_candidates)["segments"]
                if segment["type"] != "unknown"
            }
            if len(segment_types) > 1:
                document_type = "mixed"
            with db.tenant(tenant_id, user_id) as session:
                submission = get(session, Submission, submission_id, lock=True)
                job = get(session, ProcessingJob, job_id, lock=True)
                if job.status != "running" or job.attempts != claimed_attempt:
                    return
                document = get(session, DocumentVersion, document_id)
                other_pages = (
                    session.scalar(
                        sa.select(
                            sa.func.coalesce(sa.func.sum(DocumentVersion.page_count), 0)
                        ).where(
                            DocumentVersion.submission_id == submission_id,
                            DocumentVersion.id != document_id,
                            DocumentVersion.media_type == "application/pdf",
                            DocumentVersion.excluded_reason.is_(None),
                        )
                    )
                    or 0
                )
                if media_type == "application/pdf" and other_pages + page_count > MAX_PAGES:
                    raise DocumentError("SUBMISSION_PAGE_LIMIT")
                if not checkpoint:
                    for block in blocks:
                        session.add(
                            SourceBlock(
                                id=block.id,
                                tenant_id=tenant_id,
                                document_id=document_id,
                                locator=block.locator,
                                text=block.text,
                                parser_version=PARSER_VERSION,
                            )
                        )
                    document.processing_inventory = {
                        **processing_inventory(blocks, local_candidates),
                        "parse_complete": True,
                        "source_order": [str(b.id) for b in blocks],
                    }
                document.scan_status = "clean"
                document.document_type, document.page_count = document_type, page_count
                job.stage, job.heartbeat_at = "extract", now()
                run_metadata = {
                    "job_id": str(job.id),
                    "attempt": claimed_attempt,
                    "provider": configuration.extraction_provider,
                    "model": configuration.extraction_model or None,
                    "prompt_version": "cgl-evidence-2",
                    "pipeline_version": PIPELINE_VERSION,
                    "parser_version": PARSER_VERSION,
                    "input_sha256": sha,
                    "input_characters": sum(len(b.text) for b in blocks),
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_status": "unpriced"
                    if configuration.extraction_provider != "local"
                    else "no_external_provider",
                }
                session.add(
                    UsageEvent(
                        tenant_id=tenant_id,
                        submission_id=submission_id,
                        kind="extraction_attempt",
                        quantity=1,
                        estimated_cost=None,
                        metadata_json={**run_metadata, "status": "started"},
                    )
                )
                document.processing_inventory = {
                    **document.processing_inventory,
                    "extraction_run": run_metadata,
                }
                submission.row_version += 1
            extracted = (
                provider_extract(blocks, configuration)
                if configuration.extraction_provider != "local"
                else local_candidates
            )
            if configuration.extraction_provider != "local":
                # Provider omissions must not discard recognized local fields or abstentions.
                identities = {
                    canonical_json([f.field, f.value, f.context, f.availability, f.support_status])
                    for f in extracted
                }
                extracted += [
                    f
                    for f in local_candidates
                    if canonical_json(
                        [f.field, f.value, f.context, f.availability, f.support_status]
                    )
                    not in identities
                ]
            with db.tenant(tenant_id, user_id) as session:
                submission = get(session, Submission, submission_id, lock=True)
                job = get(session, ProcessingJob, job_id, lock=True)
                if job.status != "running" or job.attempts != claimed_attempt:
                    return
                document = get(session, DocumentVersion, document_id)
                if document.processing_status != "completed":
                    document.processing_inventory = {
                        **document.processing_inventory,
                        **processing_inventory(blocks, extracted),
                    }
                    for fact in extracted:
                        session.add(
                            Observation(
                                tenant_id=tenant_id,
                                submission_id=submission_id,
                                document_id=document_id,
                                source_id=fact.source_ids[0],
                                supporting_source_ids=[str(ref) for ref in fact.source_ids[1:]],
                                field=fact.field,
                                group_key=group_key(fact.field, fact.context, fact.value),
                                value=fact.value,
                                context=fact.context,
                                raw_value=fact.raw_value,
                                method=fact.method,
                                pipeline_version=PIPELINE_VERSION,
                                warnings=fact.warnings,
                                availability=fact.availability,
                                support_status=fact.support_status,
                            )
                        )
                    document.scan_status, document.processing_status = "clean", "completed"
                    document.document_type, document.page_count = document_type, page_count
                    submission.row_version += 1
                    session.add(
                        UsageEvent(
                            tenant_id=tenant_id,
                            submission_id=submission_id,
                            kind="document_pages",
                            quantity=page_count,
                            estimated_cost=None,
                            metadata_json={
                                **run_metadata,
                                "status": "succeeded",
                                "scanner": scanner,
                                "method": "provider"
                                if configuration.extraction_provider != "local"
                                else "local-label-parser",
                                "pipeline_version": PIPELINE_VERSION,
                                "facts": len(extracted),
                                "processing_seconds": round(time.perf_counter() - started_at, 3),
                            },
                        )
                    )
                    session.flush()
                    refresh_issues(session, submission)
                job.status, job.stage, job.finished_at = "completed", "human_review", now()
        elif kind == "package" and package_id:
            with db.tenant(tenant_id, user_id) as session:
                package = get(session, Package, package_id)
                revision = get(session, CanonicalRevision, package.revision_id)
                if digest(canonical_json(revision.snapshot)) != revision.sha256:
                    raise DocumentError("INTEGRITY_FAILURE")
                snapshot, revision_hash = revision.snapshot, revision.sha256
                docs = active_documents(session, submission_id)
                if any(
                    d.scan_status != "clean" or d.processing_status != "completed" for d in docs
                ):
                    raise DocumentError("DOCUMENTS_NOT_READY")
                requested = package.manifest.get("requested_documents", [])
                actual = [{"id": str(d.id), "sha256": d.sha256} for d in docs]
                if sorted(requested, key=lambda d: d["id"]) != sorted(
                    actual, key=lambda d: d["id"]
                ):
                    raise DocumentError("PACKAGE_INPUT_CHANGED")
                documents = snapshot.get("document_inventory") or [
                    {"id": str(d.id), "filename": d.filename, "sha256": d.sha256} for d in docs
                ]
                supporting = []
                for doc in docs:
                    data = store.get(doc.object_key, doc.storage_version)
                    if digest(data) != doc.sha256:
                        raise DocumentError("INTEGRITY_FAILURE")
                    supporting.append(("supporting/" + str(doc.id) + "_" + doc.filename, data))
                source_ids = {
                    UUID(ref)
                    for fact in snapshot["facts"]
                    for ref in [*fact["source_ids"], *fact.get("original_source_ids", [])]
                }
                evidence = [
                    {
                        "id": str(source.id),
                        "document_id": str(source.document_id),
                        "locator": source.locator,
                        "text": source.text,
                    }
                    for source in session.scalars(
                        sa.select(SourceBlock).where(SourceBlock.id.in_(source_ids))
                    )
                ]
                if len(evidence) != len(source_ids):
                    raise DocumentError("EVIDENCE_NOT_FOUND")
            outputs, manifest = render(snapshot, revision_hash, documents, supporting, evidence)
            artifacts = []
            for format, (data, filename, media_type) in outputs.items():
                key = f"tenants/{tenant_id}/artifacts/{uuid4()}/{filename}"
                version = store.put(key, data, media_type)
                artifacts.append(
                    PackageArtifact(
                        tenant_id=tenant_id,
                        package_id=package_id,
                        format=format,
                        filename=filename,
                        media_type=media_type,
                        object_key=key,
                        storage_version=version,
                        sha256=digest(data),
                        byte_size=len(data),
                    )
                )
            with db.tenant(tenant_id, user_id) as session:
                get(session, Submission, submission_id, lock=True)
                job = get(session, ProcessingJob, job_id, lock=True)
                if job.status != "running" or job.attempts != claimed_attempt:
                    return
                package = get(session, Package, package_id, lock=True)
                if package.status != "ready" and package.status != "approved":
                    session.add_all(artifacts)
                    package.manifest, package.manifest_hash = (
                        manifest,
                        digest(canonical_json(manifest)),
                    )
                    package.status = "ready"
                    session.add(
                        UsageEvent(
                            tenant_id=tenant_id,
                            submission_id=submission_id,
                            kind="packages_rendered",
                            quantity=1,
                            estimated_cost=None,
                            metadata_json={"template": package.template_version},
                        )
                    )
                job.status, job.stage, job.finished_at = "completed", "approval", now()
        else:
            raise DocumentError("UNKNOWN_JOB")
    except Exception as error:
        code = error.code if isinstance(error, DocumentError) else "PROCESSING_FAILED"
        logging.getLogger("cgl.processing").warning(
            "job_failed job_id=%s code=%s exception_type=%s attempt=%s",
            job_id,
            code,
            type(error).__name__,
            claimed_attempt,
        )
        retryable = isinstance(error, DocumentError) and error.retryable
        if claimed_attempt:
            with db.tenant(tenant_id, user_id) as session:
                submission = get(session, Submission, submission_id, lock=True)
                job = get(session, ProcessingJob, job_id, lock=True)
                if job.attempts == claimed_attempt and job.status == "running":
                    job.error_code = code
                    job.status = "queued" if retryable and claimed_attempt < 3 else "failed"
                    job.lease_until = (
                        now() + timedelta(seconds=30 * 2 ** (claimed_attempt - 1))
                        if job.status == "queued"
                        else None
                    )
                    job.finished_at = now() if job.status == "failed" else None
                    session.add(
                        UsageEvent(
                            tenant_id=tenant_id,
                            submission_id=job.submission_id,
                            kind="processing_attempt_failed",
                            quantity=1,
                            estimated_cost=None,
                            metadata_json={
                                "job_id": str(job.id),
                                "attempt": claimed_attempt,
                                "stage": job.stage,
                                "provider": configuration.extraction_provider,
                                "model": configuration.extraction_model or None,
                                "pipeline_version": PIPELINE_VERSION,
                                "prompt_version": "cgl-evidence-2",
                                "error_code": code,
                                "input_tokens": None,
                                "output_tokens": None,
                                "cost_status": "unknown",
                            },
                        )
                    )
                    if job.document_id:
                        document = get(session, DocumentVersion, job.document_id)
                        document.processing_status = (
                            "queued" if job.status == "queued" else "failed"
                        )
                        submission.row_version += 1
                        if code == "MALWARE_DETECTED":
                            document.scan_status = "rejected"
                    if job.package_id:
                        get(session, Package, job.package_id).status = "failed"
        # Intentionally do not emit document text, provider bodies, or credentials to task logs.
    finally:
        db.close()

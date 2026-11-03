# ADR 0007: explicit cleanup of expired local upload parts

Date: November 2, 2026. Status: accepted for local synthetic A01 remediation.

## Context

The retained-storage limits count abandoned transfer parts and writes left behind by rolled-back transactions. Recovering that capacity must not race finalization, delete a retained source or package, or lose the deletion record if the filesystem operation succeeds but PostgreSQL rolls back. Distributed S3 reservations remain a separate requirement.

## Decision

Add an explicit, two-request maintenance API for an authenticated agency member with `retention.manage` and recent authentication. Its agency comes from the session and transaction-local forced RLS. It accepts an upload identifier, never an object key or filesystem path. No scheduler, public route or ordinary processing task invokes cleanup automatically.

The first request locks the upload session, verifies eligibility, inventories at most 100 files under that exact tenant/upload directory, and commits a cleanup intent and audit event without deleting anything. The intent records the requesting member, reason, policy version, relative names, byte counts and SHA-256 hashes. Creation retries use the existing request-hash-bound idempotency mechanism. The API returns counts and the plan identifier, not private object paths or file contents.

The second request locks the upload and existing intent, rechecks current authority, upload hold, expiry/grace, original integrity and retained document/artifact references, then validates the entire batch before unlinking. Both operations use the same upload-row lock as part writes/finalization and the filesystem adapter's process-shared lock. Lock order is database upload row, cleanup row when applicable, then storage. Hold mutations also require their expected version, so a stale release cannot clear a newer hold. Cleanup never discovers a different tenant by scanning the filesystem.

Policy `local-upload-cleanup-1` requires at least 24 hours after the upload's original expiry and file modification times at least 24 hours old. This applies to aborted, expired-open and finalized sessions alike. Finalized sessions additionally require a complete preserved original with matching submission, byte count and full-file hash. A per-upload `cleanup_hold` blocks planning and execution until an authorized, reasoned release; it is a narrow temporary-file preservation control, not a general legal-hold system.

Only regular, single-link files with the current generated part naming pattern and at most 1 MiB are admitted. Symlinks, non-directory ancestors, unfamiliar names, oversized/recent/changed files, unknown statuses and retained document/artifact references fail closed. Missing upload-session records confer no authority to delete. Empty exact upload directories can be removed; parent directories and other object categories remain untouched.

The committed intent survives a crash or rollback after deletion. Retrying the same execution tolerates already-absent planned objects, validates remaining bytes, and records completion separately. The summary distinguishes bytes removed in the successful attempt from previously absent objects; it does not invent when or why an absent file disappeared. A completed intent returns its saved result without another deletion/audit event. An immutable-intent database trigger prevents changing the manifest, attribution or reason, and completed records cannot be updated. Runtime gains no database `DELETE` privilege. Upload sessions, part metadata, original documents, evidence, packages, approvals and audit history are preserved.

## Limits and follow-up

This first increment is an authenticated maintenance API without a settings screen, candidate queue or scheduled collector. Operators need the upload ID and must retain the returned plan ID/idempotency key for recovery. Batches are bounded; large retry-orphan directories may need additional plans. Unexpected filesystem content requires investigation, not a broader recursive deletion command.

Orphans in document/artifact trees and directories without known upload sessions are excluded. Safe collection of those objects needs durable write intents and reference/lease reconciliation. Distributed quotas, S3 uncertain-write reconciliation, category retention, tenant-wide legal holds/offboarding, provider erasure, backup expiry and restored-backup enforcement remain open. No S3 write or customer-data gate changes.

## Verification

Regression tests exercise expiry/grace and holds, upload-row serialization with finalization, finalized-source integrity and retry, known-session rollback orphans, current permission and tenant scope, immutable manifests, absent-object recovery after deletion/transaction failure, bounded batches, unsafe paths and changed bytes. Tests delete only generated files in disposable test storage and use guarded disposable databases.

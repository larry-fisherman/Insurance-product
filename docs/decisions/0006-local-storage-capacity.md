# ADR 0006: local retained-storage admission before object writes

Date: November 2, 2026. Status: accepted for local synthetic A01 remediation.

## Context

Aborted and expired transfers free open-upload slots but retain their bytes. Files can also remain after a database rollback. Counting only open sessions or active document rows cannot bound storage consumption. Immediate deletion during a business transaction would introduce a mismatch because storage deletion cannot roll back with PostgreSQL.

## Decision

The filesystem adapter serializes capacity checking and each immutable write with a process-shared `flock` on the private local volume. It scans retained logical file bytes and filesystem entries, including objects without committed database rows, before admitting the full new object. The lock remains held until the write closes. A process crash releases the lock; any partial retained file consumes capacity on the next scan. Tenant and global limits both apply, and an entry limit bounds empty files/directories. Rejected writes do not create object directories. Existing objects cannot be overwritten.

All API/worker filesystem writes use this adapter and the same configured root/limits. The caller still owns authorization and server-generated tenant keys; storage quotas confer no access rights. API capacity failures return a safe 507 response. Worker capacity failures retain a safe error code without automatic retries. Reads do not require available capacity, and no purge is introduced.

This is an intentionally local solution using the existing shared POSIX volume. It avoids introducing cross-tenant definer functions or database privileges merely to repair the local quota bypass. The O(number of retained entries) scan and shared writer lock are not a scalable distributed storage design. Limits apply to logical bytes/entries under this root, not physical allocation, filesystem overhead or unrelated applications. Monitor physical capacity separately.

S3 writes fail closed until durable distributed reservations, conditional immutable writes, object versions and uncertain-write reconciliation can enforce equivalent limits. Historical version-addressed reads remain available. A local lock must never be presented as coordination between separate S3 clients.

## Remaining work

[ADR 0007](0007-temporary-upload-cleanup.md) adds explicit, narrowly scoped cleanup of expired known-session temporary parts and rollback-created part files, with committed intents, upload holds and finalization coordination. Broader unknown-session and document/artifact orphan reconciliation remains open. Preserve original source bytes, package artifacts, approval hashes and metadata. Do not delete the lock file while writers are active. Do not grant runtime blanket database deletion powers.

Hosted storage needs an independently accepted quota/reservation/reconciliation protocol, physical capacity monitoring, workload identities and retention/restore controls. No customer-data or public-deployment gate changes.

## Verification

Regression cases cover retained objects without database rows across store restart, independent tenant/global limits, simultaneous writes from independent processes, abrupt writer-process termination with retained partial bytes and kernel lock release, bounded empty-object directory creation, preserved pre-existing over-budget history, S3 rejection before provider access, and repeated authenticated upload/abort attempts through the API.

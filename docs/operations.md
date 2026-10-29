# Local synthetic operations

These procedures apply to the loopback-only Docker review stack. They do not establish production RPO/RTO, encryption, privacy compliance, or vendor agreements.

## Service operations

`make full-up` starts PostgreSQL, API, web, Redis, worker, and scheduler. `make scan-up` starts ClamAV; use it for ordinary uploads. `make full-logs` shows recent application/task logs. Do not print environment files, cookie jars, document text, or provider response bodies. The API returns request IDs and bounded error codes; the review UI displays processing stages and safe failure reasons.

Health/readiness: `/api/v1/health/live` checks the process; `/ready` checks application lifecycle and the identity database connection when configured. Readiness does **not** assert scanner readiness, Redis availability, storage health, or worker capacity. Test those separately before processing real workloads. Public exposure, TLS termination, alerts, dashboards, central log retention, and service SLOs remain launch work.

Redis persists to a named volume and uses a generated password. Database jobs/outbox are the source of truth. The broker contains identifiers only. The scheduler redispatches pending jobs and leases that expired after a crash. At most three automatic attempts are made; failed jobs require an authorized retry. Unsafe/malware documents must be replaced. A down scanner never releases quarantined uploads. Package generation failures appear in the processing activity panel.

Workers have a 550-second soft and 600-second hard limit, 2 GB container memory limit, bounded concurrency, and child recycling. A claim lease lasts twelve minutes. Allow the lease to expire after abruptly restarting a worker. Defaults are local review values, not a throughput guarantee. Inputs over document/page/cell/expansion/provider limits require splitting or manual handling. No unbounded model-agent loops are used.

## Backup and restore drill

The backup command **briefly stops the API, worker, and scheduler**, captures a PostgreSQL custom-format dump and a private-file archive, then restarts those services even on failure. Do not run while a browser acceptance test is active. It creates a new directory and never overwrites an existing backup.

```sh
make backup-local
make restore-drill
```

For subsequent backups choose a fresh directory:

```sh
services/backend/.venv/bin/python -m app.operations backup .artifacts/backup-2026-10-29-02
services/backend/.venv/bin/python -m app.operations drill .artifacts/backup-2026-10-29-02
```

The drill verifies archive hashes, restores to a newly generated `cgl_restore_<uuid>` database, verifies required schema, verifies every referenced document/artifact hash from the restored database against the file archive without extracting it, and removes only that disposable database. It never restores over `cgl_dev` or replaces live files. A drill proves schema/data restoration and referenced file integrity; a production exercise must also restore objects into an isolated store and verify versioned storage access, application login, and approved export. Keep encrypted copies of the private configuration and keys separately; the backup intentionally does not include secrets. The local backups are private files but are not application-encrypted. Do not use this backup destination for customer data.

For an actual local disaster, preserve existing volumes/configuration, create an isolated replacement stack, restore the dump/files there under operator supervision, verify tenant counts and object hashes, and only then switch ports/configuration. Do not reuse a restore drill as an automated destructive live restore.

## Retention, deletion, and orphan files

There is no automatic retention purge and no end-user delete endpoint for immutable evidence. Local volumes persist. Production needs a signed retention schedule, legal-hold handling, contractual deletion windows, and backup expiry before customer data is accepted.

An operator deleting a tenant must first suspend its tenant row through a reviewed migration/admin procedure and revoke its sessions. Enumerate its object keys and database dependents; preserve legal holds; obtain any customer-required authority; remove its objects/versions and records through an explicit maintenance migration that intentionally handles immutable triggers. Never grant tenant runtime credentials `BYPASSRLS`, table-owner privileges, truncate, or blanket delete to implement deletion. Record a minimal non-sensitive deletion completion record outside the removed dataset, and let encrypted backups expire under the agreed policy. This reviewed deletion migration is intentionally not exposed as an API in the local MVP.

A storage write can outlive a rolled-back database transaction or killed renderer. These orphan objects are private and cannot be downloaded through the API, but consume disk. Before cleanup, stop writers, inventory all document/artifact object keys, compare against the private filesystem/S3 inventory, quarantine unreferenced objects older than the maximum job lease, and delete only after review. An automated S3 orphan collector requires infrastructure-specific inventory/version and retention tests. No speculative garbage collection deletes files during ordinary processing.

## Incidents and access reviews

For suspected cross-tenant access, stop public/API access, preserve database/audit/object versions, capture only safe metadata, rotate session/application/DB/storage/provider credentials, and engage the designated security/legal contacts before customer notification decisions. Application-secret rotation invalidates signed fixtures and requires a controlled TOTP-key migration; do not replace it casually. Database/application/Redis credentials must be rotated consistently with the saved volume state.

Review active agency memberships, role grants, stale invitations, retained demo agencies, and operator/database access regularly. Synthetic example agencies have no shared credential, but their records persist; retire them through an operator-reviewed cleanup. Do not turn local demo access into a public production login.

## Provider and deployment configuration

External provider endpoints are operator configuration, not user-supplied URLs. Gateway endpoints must use HTTPS; OpenAI uses its fixed HTTPS Responses endpoint. Redirects are disabled, input/output are bounded, and material values/citations are validated before persistence. Upload text is untrusted data and cannot call tools or override extraction instructions.

The local filesystem is the default Compose storage, mounted only into API/worker/scheduler, with app-owned private directories/files. To deploy S3, first validate bucket privacy, versioning, encryption/KMS, least-privilege runtime IAM, retention/lifecycle, deletion, and restore using the actual provider. Do not infer S3 controls from the existence of an adapter. Public-cloud infrastructure is deliberately not provisioned or represented as ready by this repository.

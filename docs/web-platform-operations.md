# Web platform: local operation and implementation status

Updated November 1, 2026. This implements the approved architecture as a local, synthetic-only platform. It does not establish production or insurance-domain acceptance.

## Start and use

```sh
make full-up
make scan-up
```

- Marketing: **http://127.0.0.1:3001**. Product/workflow/security pages and demo, pricing and contact forms. Use fictional contact details. Forms persist a record; they do not send email.
- Operations: **http://localhost:3000**. Sign in or choose **Open a fictional example** for the existing four-document CGL workflow. Registered local users enroll TOTP, complete agency setup, and assign preparer/approver roles under **Settings & team**.
- Use these exact local hostnames. App cookies must not be shared with marketing by running both experiences on the same hostname with different ports.

The startup preserves existing secrets, records and named volumes. Migration `0007_platform` adds agency profiles, subscriptions, billing requests, operator activations, upload sessions/parts and identity session fields. Do not reset the database or volumes to upgrade.

Application routes: `/dashboard`, `/insureds`, `/insureds/:id`, `/submissions`, `/submissions/new`, `/submissions/:id/{overview,documents,review,tasks,packages,audit,settings}`, `/tasks`, `/onboarding`, `/settings/team`, `/settings/agency`, `/settings/billing`, `/settings/usage`, `/profile/security`, `/agency-select`, `/access-pending`, `/activate`, `/invite`, `/login`. Old `/workspace/...` links redirect. Reconciliation and discrepancy review share the submission review page; package generation and exact-file approval share the packages page.

A new agency owner has administration and billing access. Assign preparer and approver explicitly before document work. Approval requires current authority and the reviewed package's exact manifest and bytes. If an agency/session changes in another tab, reload before continuing; stale writes fail closed. Sensitive actions may direct a local user to **Profile & security** for password and a new authenticator code.

Uploads use resumable chunks. After interruption or reload, select the same file in the same submission/replacement context to resume. Finalization verifies bytes and queues normal scanning; it never grants a synthetic scan bypass. Expired upload sessions return a clear restart error. Aborted/expired temporary parts are retained; an automated purge is not yet implemented.

## Managed identity configuration

Local mode is the default. To exercise OIDC with an approved test provider, privately configure `CGL_AUTH_MODE=oidc`, `CGL_OIDC_ISSUER`, `CGL_OIDC_CLIENT_ID`, `CGL_OIDC_CLIENT_SECRET`, `CGL_OIDC_AUTHORIZATION_ENDPOINT`, `CGL_OIDC_TOKEN_ENDPOINT`, `CGL_OIDC_JWKS_URI`, and `CGL_OIDC_REQUIRED_AMR` (default `mfa`). Issuer/provider endpoints must use HTTPS; the callback is `${CGL_PUBLIC_ORIGIN}/api/v1/auth/oidc/callback`. Configure the exact callback at the provider. No automatic endpoint discovery or user-supplied provider URL is accepted. Local/test app origins may remain HTTP for localhost verification; hosted app cookies use the `__Host-` prefix and Secure.

The provider must issue verified email, nonce, subject, audience, expiry, issued-at, recent `auth_time` and the configured MFA `amr`. Server-side code validates signed RS256 ID tokens against the configured key set. Wrong/missing claims fail closed. OIDC mode disables local passwords, recovery and demo access; provider account recovery remains with the provider. Provider logout/revocation events, disabled-account propagation, enterprise federation and a live provider acceptance exercise remain unfinished.

Operator-issued activations enable controlled agency creation. From the repository root, use the project Python environment:

```sh
services/backend/.venv/bin/python -m app.platform_admin activation \
  --email owner@example.invalid --agency "Fictional Agency" \
  --reference "local-review-operator" --output /tmp/fictional-agency-activation.txt
```

This creates a new mode-0600 file containing the one-time activation code; it does not print the code or send a message. Sign in as that verified identity, open `/activate`, and enter the code. It expires after seven days. The CLI is restricted to the configured loopback `cgl_dev` database and never runs in an HTTP request. Do not commit or paste the token into logs. Use another tab for sign-in when an invitation/activation link is already open.

## Subscription administration

The billing role can view status/limits, change the billing contact, and record plan-change/cancellation/data-return requests. These requests need operator action; cancellation requests do not immediately cancel access. No money is charged and no invoice is generated.

```sh
services/backend/.venv/bin/python -m app.platform_admin subscription \
  --tenant AGENCY_UUID --status manual_pilot --seats 25 --submissions 100 \
  --reference "local-review-operator-change-123"
```

Statuses `restricted` and `cancelled` prevent new intake/generation and worker processing, while existing authorized reading/export and administrative review remain available. Seat limits are checked when accepting invitations or reactivating members. Submission capacity is an all-time synthetic limit. The CLI records before/after values and the supplied operator reference. Review evidence/history is retained.

## Service boundaries and maintenance

`marketing` and `inquiries` share only the internal `public-intake` network. Marketing also joins a separate `public-edge` network so Docker can publish its loopback browser port; neither public network joins operations. The inquiry receiver has its own `public-inquiries` volume, no operations environment file and no exposed service port. `INQUIRY_DATABASE_URL` supports SQLite or a dedicated PostgreSQL psycopg connection; schema initialization is explicit through `python -m app.inquiries migrate`. The local entrypoint initializes it. `python -m app.inquiries count` prints only a count, not contact contents.

`parser` has no operations environment, private-file volume, database connection or Redis connection. Its internal `parsing` network is shared only with `worker`. It accepts bounded PDF/XLSX bytes after worker-side scanning, starts a child with CPU/memory/file/descriptor limits, and returns bounded parsed blocks. One child runs at a time; one additional request can wait, matching the two local worker processes. Further requests receive a retryable busy response. Parser endpoint errors remain processing failures; normal documents never bypass scanning. Native test runs without `CGL_PARSER_ENDPOINT` use the local parser fallback.

Run `make verify-platform` to inspect runtime network/credential/mount/port separation without printing environment values. Use `docker compose --env-file services/backend/.env.app -f compose.full.yaml ps` for health and `make full-logs` for bounded processing diagnostics. Never print private environment files. The inquiry volume is separate from the existing operations backup; its backup, retention and restoration procedures remain a hosted-release prerequisite. Existing operations recovery guidance still applies.

## Remaining acceptance work

- Real OIDC provider configuration and lifecycle/recovery/revocation acceptance; hosted DNS/TLS/security controls and independent assessment.
- Payment provider, signed billing webhooks, checkout/customer portal and invoices; approved prices, billing reconciliation and dunning.
- Invitation/notification delivery, operator inquiry review tooling, public intake abuse protection and retention.
- Comprehensive resource/load/lease tests and hosted parser isolation validation.
- The existing open CGL semantic, representative accuracy, forms/templates and workflow findings in [review remediation](review-remediation.md) and [pilot readiness](pilot-readiness.md). This platform work does not waive them.

## Verification record — November 1, 2026

All checks used local/generated fictional data. No paid provider calls, external messages, customer data or public deployment were involved.

The table below records the implementation handoff. The subsequent [plan/code audit](WEB_PLATFORM_AUDIT.md) contains newer verification, reproduced open defects and a browser run that timed out after approval. Use the audit when assessing current completion or release eligibility; earlier passes remain dated historical evidence.

| Check                 | Evidence                                                                                                                                                                                                                                                    |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend               | `make check-api`: Ruff, mypy and 80 non-database tests passed, including bounded parser concurrency and credential rejection.                                                                                                                               |
| PostgreSQL            | 58 database tests passed in the full suite; the subsequently added invitation idempotency/revocation regression passed separately. `make db-check` reported no new upgrade operations.                                                                      |
| Frontend              | Formatting, lint, TypeScript and 23 unit tests passed. Both Next.js applications built successfully, including production Docker builds.                                                                                                                    |
| Browser workflow      | `CGL_SCAN_ACCEPTANCE=1 make check-e2e`: all four tests passed. Covered source review/conflict resolution, exact-file approval and PDF export, mobile login, ordinary chunked PDF upload with ClamAV and processing, and isolated public inquiry submission. |
| OCR and boundaries    | `make verify-ocr` passed for a generated scanned PDF with page/line references. `make verify-platform` passed for runtime networks, credentials, mounts, privileges and published/private ports.                                                            |
| Extraction regression | `make evaluate` passed the generated synthetic regression. This is not representative insurance accuracy or priced-provider evidence.                                                                                                                       |

`make full-up` also completed successfully with the final readiness checks: both websites, the API, parser, inquiry receiver and Redis became healthy. Worker and scheduler processes started; the browser workflow exercises their processing path. ClamAV was running for the ordinary-upload acceptance check.

After the final navigation update and rebuild, the focused broker workflow passed again, including confirmation that the submission heading reflects the approved status.

Live testing identified and corrected parser-slot contention, the marketing port's need for a separate edge network, and healthcheck Host-header handling. Temporary failures are not treated as successful runs; the browser suite and affected runtime checks were rerun after those corrections. Existing records, configuration and volumes were preserved.

See [ADR 0004](decisions/0004-web-platform.md) for decisions and local deviations. No production approval follows from passing synthetic tests.

For native Next.js runs, set `APPLICATION_ORIGIN` to match the operations host and `CGL_PUBLIC_ORIGIN`, and set `MARKETING_ORIGIN` to the separate public host. Marketing uses only these public origins and `INQUIRY_INTERNAL_URL`; never copy the backend environment into marketing. `npm run dev:marketing` starts the public app on port 3001. The full Compose stack supplies its isolated inquiry address automatically.

## Audit remediation deployment

The first [audit remediation batch](WEB_PLATFORM_AUDIT.md#remediation-batch-1--november-1-2026) requires migration `0008_versions` and rebuilt API/worker/parser/web/marketing images. Use the existing `make full-up` path after verification. Preserve existing environment files, database records and volumes. Old application images must not continue writing after defaults are removed; coordinate the local restart. No data backfill is required or authorized by this migration.

Set both `APPLICATION_ORIGIN` and `MARKETING_ORIGIN` for native frontend builds and runtime. They must have different hostnames: different ports or HTTP versus HTTPS on the same host do not isolate cookies. Defaults remain `http://localhost:3000` and `http://127.0.0.1:3001`. Compose passes both public values; neither belongs to a secret configuration bundle.

Configured extraction gateways must honor `Accept-Encoding: identity`. Compressed responses are rejected before decompression. Responses are limited to 2 MiB and JSON depth 32 (including enclosed output/value JSON); connect inactivity is bounded to 10 seconds, writes to 30 seconds and reads to 90 seconds, and a 90-second elapsed deadline is checked at response chunks/completion. A socket wait can run until its timeout before the elapsed check. Oversized, compressed and malformed output does not become extracted facts. These limits are not cost accounting or live provider acceptance.

OIDC recent-authentication checks now use signed provider authentication time, with allowed future skew clamped to callback time. Existing sessions retain their stored timestamps; revoke them or let them expire before relying on the correction. Real identity-provider acceptance remains required.

## Local storage capacity controls

Admission is the PostgreSQL reservation ledger ([ADR 0008](decisions/0008-storage-reservations-and-orphan-cleanup.md)). Limits live in the migration-managed `app.storage_limit` row: 2 GiB per tenant, 20 GiB globally and 100,000 entries. The former `CGL_STORAGE_*_LIMIT` settings are no longer read. Change a limit only through a reviewed migration, so API and worker processes cannot diverge. Existing usage above a new limit remains readable; additional writes fail with HTTP 507 / `STORAGE_CAPACITY_REACHED`. Package jobs record the same safe code and fail without automatic retries. A ledger that is not yet seeded returns HTTP 503 / `STORAGE_QUOTA_UNAVAILABLE`.

After applying migration `0010_storage`, run `make storage-reconcile` (part of `make full-up`) before relying on uploads. It runs `python -m app.storage_admin reconcile` in the API image against the private volume, as the runtime role. For each tenant with an active member it scans `tenants/{id}/` under the storage lock. The first run seeds the baseline and marks the tenant initialized; later runs raise counters when retained bytes exceed the ledger, never lower them, and record signed drift. Until every active tenant is seeded, every reservation fails closed, because the global total would be incomplete. The command reports tenants without an active member, which cannot be seeded, and exits non-zero if a tenant directory holds a symlink or special file. Investigate that rather than removing files. `make storage-report` prints per-tenant usage and the latest sweep counts (no keys or paths). Agency-admin read endpoints are future work.

These are logical-byte/entry limits, not physical filesystem reservations or a retention policy. Each reservation charges its exact bytes and one entry; the seed also counts directories, as ADR 0006 did. Aborted, expired, finalized, quarantined, failed, excluded and orphaned bytes stay charged until a verified deletion releases them. Do not manually delete original documents or artifacts to recover space. Monitor disk capacity and retain the existing synthetic-only boundary. Never delete the `.storage-write.lock` file while writers run: replacing it would split the shared lock.

Policy `storage-orphan-cleanup-1` uses versioned constants (in `app/upload_cleanup.py`), not settings: a 1-hour reservation TTL, a 5-minute writer safety margin and a 24-hour orphan grace after expiry. The scheduler's `storage-sweep` beat task is report-only. `CGL_STORAGE_SWEEP_INTERVAL_SECONDS` (default 3600, 300–86400) and `CGL_STORAGE_SWEEP_BATCH` (tenants per run, default 100) tune it, and `CGL_STORAGE_SWEEP_MODE` accepts only `report`. For each tenant it records orphan candidates, expired never-written reservations, legacy unreserved files and bytes, anomalies and ledger drift. It logs only a count of unknown tenant directories and never opens them.

S3 reads retain their version-addressed behavior. New S3 writes fail closed with HTTP 503 / `STORAGE_QUOTA_UNAVAILABLE` before any charge or provider call. The ledger is adapter-neutral, but the gate stays closed until conditional S3 writes have a local S3-compatible service and release evidence. Raising local limits does not bypass that gate.

## Temporary upload maintenance

Apply migration `0009_cleanup` through `make db-migrate-native` and rebuild/restart the backend through the usual `make full-up` workflow. This adds metadata and a false-by-default upload hold; it does not delete existing bytes or enable a scheduled collector. The authenticated API is the first implementation; a settings screen and candidate queue are not yet provided.

Use an active agency administrator with `retention.manage` and authentication within the last ten minutes. Synthetic demo sessions retain their existing documented authentication bypass. All requests require the ordinary agency-context header; mutations also require the existing Origin/CSRF controls. Tenant IDs, filesystem keys and paths are not accepted as cleanup authority. Avoid placing session material in commands, screenshots or logs.

For a known upload ID returned by resumable initialization:

1. Read the current hold and version with `GET /api/v1/uploads/{upload_id}/cleanup/hold`. Set or release it with `POST` to the same path and `{"held": true, "expected_version": 1, "reason": "Preserve pending investigation"}`, using the actual version returned (use `false` with a release reason to release). A stale version returns `UPLOAD_HOLD_CHANGED`; reload and review rather than automatically retrying a stale release. Hold changes and execution serialize on the upload row. A hold blocks subsequent deletion; it cannot restore bytes already removed.
2. After at least 24 hours beyond upload expiry, call `POST /api/v1/uploads/{upload_id}/cleanup/plan` with `{"reason": "Remove expired synthetic transfer bytes"}` and a retained `Idempotency-Key`. Files must also be at least 24 hours old. The committed plan returns its ID, policy, object count and byte count; this request deletes nothing. Reuse the same key and payload after an uncertain response.
3. Review those counts, then call `POST /api/v1/uploads/{upload_id}/cleanup/{plan_id}/execute`. Execution rechecks the hold, grace, original integrity and retained references and deletes at most that plan's 100 temporary objects. A finalized original must match the initialized file's hash/size. Original source files, package artifacts and all metadata/history remain preserved.
4. Inspect `GET /api/v1/uploads/{upload_id}/cleanup/{plan_id}`. If completion is uncertain, retry the same execute path. The durable intent remains pending after a crash/rollback; already-absent planned objects are counted separately. A completed plan returns its saved result. Create another plan only if more temporary files remain.

`UPLOAD_CLEANUP_HELD` requires an authorized hold decision. `UPLOAD_CLEANUP_NOT_READY` requires waiting, not modifying expiry. `UPLOAD_CLEANUP_INTEGRITY`, `UPLOAD_CLEANUP_REFERENCED` and `UPLOAD_CLEANUP_UNSAFE` require investigating the preserved originals/references/filesystem. Do not rewrite a plan or manually delete around a failed check. `UPLOAD_CLEANUP_EMPTY` means no temporary files were available to plan; it does not report whether broader orphan categories exist. S3 cleanup is unsupported and its writes remain gated.

The plan handles temporary rollback orphans only when their upload session is known. It does not scan unknown sessions, purge document/artifact trees, implement general legal holds/retention, or enforce cleanup after restoring a backup. Completing a plan releases each cleaned part's reservation, or the pre-ledger baseline bytes for parts without one, exactly once. See [ADR 0007](decisions/0007-temporary-upload-cleanup.md).

## Reservation-backed orphan maintenance

Apply migration `0010_storage` and seed the ledger first (see above). This explicit API reclaims objects whose reservation was committed but never linked: a rolled-back document store, a package render whose job lease was lost, a rolled-back part, or a reservation whose writer crashed before writing. It uses the same administrator, recent-authentication, agency-context, Origin/CSRF and idempotency requirements as upload maintenance. It accepts no tenant ID, key or path. Writers do not need to stop.

1. Call `POST /api/v1/storage/orphan-cleanup/plan` with `{"reason": "Remove expired synthetic orphan bytes"}` and a retained `Idempotency-Key`. This selects up to 100 of the agency's reservations still `reserved` at least 24 hours after expiry whose key no document, package artifact or upload part references. It marks them `cleanup_planned`, so a late business transaction fails, and records presence, size, hash and modification time. It returns the plan ID and counts; nothing is deleted.
2. Call `POST /api/v1/storage/orphan-cleanup/{plan_id}/execute`. Execution rechecks current authority, grace and references, validates the whole batch under the storage lock, unlinks present objects and empty object directories, then completes the intent and releases each reservation once. A never-written reservation completes as already absent with nothing deleted.
3. Inspect `GET /api/v1/storage/orphan-cleanup/{plan_id}`. After an uncertain or crashed execution, retry the same execute path: absent objects are tolerated and releases happen exactly once. A completed plan returns its saved result.

`ORPHAN_CLEANUP_EMPTY` means no eligible reservation exists; legacy pre-ledger files are never eligible and stay report-only. `ORPHAN_CLEANUP_UNSAFE` (changed bytes, symlink, extra link, late write) and `ORPHAN_CLEANUP_REFERENCED` require investigation, not manual deletion. `ORPHAN_CLEANUP_CHANGED` means the planned reservations changed state. S3 cleanup is unsupported.

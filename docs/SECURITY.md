# Security requirements and current posture

Updated: October 30, 2026. The repository is a local synthetic review build. Real customer documents, staging/production, and non-synthetic startup remain disabled. This document states engineering controls and open release requirements; it does not assert legal compliance, certification, or launch approval.

## Data and trust boundaries

Insurance documents may contain personal, financial, claims, employee, and confidential business information. Collect only necessary fields. Keep customer fixtures out of ordinary Git and avoid unnecessary identifiers such as FEINs or personal medical detail.

Treat browser input, uploaded bytes/text, extracted observations, spreadsheet formulas, and provider responses as untrusted. Only authenticated server-side authorization establishes tenant scope. Only explicit review establishes an approved fact. Only current package approval authorizes export of the bound artifacts.

| Threat                                  | Current control                                                                                                                                 | Remaining requirement                                                                                               |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Cross-tenant access / guessed IDs       | Session-derived agency; current membership/permissions; forced RLS with non-owner runtime; tenant relationships; scoped downloads/jobs          | Independent assessment across all routes, workers, pooled connections, support/admin paths and actual deployment    |
| Session theft / privilege abuse         | Argon2 passwords, TOTP enrollment/replay checks, one-use recovery, opaque expiring/revocable sessions, CSRF/Origin checks, live role resolution | Managed identity/verified ownership, approved onboarding/recovery and production identity assurance                 |
| Malicious files / decompression abuse   | Size/type/magic/expansion bounds, quarantine, ClamAV, bounded parser/OCR subprocesses and worker limits                                         | Separate parser permissions/egress boundary with minimal per-job access; hostile-file corpus and deployment testing |
| Prompt injection / fabricated facts     | No tools/autonomous actions in extraction; strict schemas; value/context/source-association checks; human review                                | Representative adversarial/provider evaluation; broader semantic support remains unvalidated                        |
| Stale review / altered exports          | Evidence fingerprints, input versions, immutable revisions, sealed manifests, exact artifact hashes and approval locks                          | Independent end-to-end race/integrity assessment and production recovery verification                               |
| Data leakage through files/logs/vendors | Private server-controlled storage, authorized streaming, bounded metadata/error responses, configured provider endpoints                        | Hosted IAM/KMS/network/log policy, approved vendor terms, retention and deletion guarantees                         |
| Lost work / duplicate processing        | Transactional outbox, scoped leases, bounded retries, idempotent creation and local restore tooling                                             | Crash/stall/load tests, stage checkpoints, production object restore and measured recovery targets                  |

## Identity and authorization

Agency membership supplies role bundles: viewer, preparer, approver, and tenant administrator. Check the required permission on every action and reload current membership at sensitive transitions, including approval and exception acceptance. Administrative membership alone must not be assumed to grant professional approval authority. Platform support/operator access needs its own auditable access design; do not add implicit document access through tenant roles.

The restricted authentication connection resolves identity/session metadata. Business transactions use the non-owner `cgl_runtime` path with transaction-local tenant/user settings and forced RLS. Runtime credentials must never have table ownership, superuser, or `BYPASSRLS`. Migration/admin credentials are for explicit provisioning, migrations, and controlled maintenance, never normal requests.

Tenant IDs supplied by a request or queue message cannot authorize access. Verify record ancestry, active agency/actor, and job purpose. Workers currently use an active agency actor; production service principals and member-deprovisioning semantics require explicit design. Test missing scope, connection reuse, disabled membership, forged identifiers, direct object download, and cross-tenant references.

Sessions require CSRF and trusted Origin checks for unsafe authenticated actions. Keep secrets, cookies, MFA seeds, recovery codes, and invitation tokens out of logs and source control. Invitation links belong only in the intended invitation/acceptance flow; redact tokens from request logs and referrers. Synthetic example sessions have an explicit MFA bypass for fictional review; they are not production authentication.

## Document and extraction safety

Normal uploads remain quarantined until scanning succeeds. Scanner failure/unavailability never releases a file. Only exact server-generated HMAC-signed fictional fixtures use the documented synthetic bypass; user labels or filenames cannot trigger it.

Apply independent upload, page, cell, expansion, CPU, memory, time, and output bounds. Do not execute workbook macros, formulas, embedded programs, or document links. Preserve formula/cached-value and hidden-content context. Spreadsheet exports must not turn untrusted text into executable formulas.

Parsing currently runs within the application worker boundary. Resource limits and subprocesses reduce exposure but do not provide the required independent filesystem/credential/network isolation. That remains an engineering blocker for customer-data use.

Uploaded instructions cannot alter prompts, system policy, provider endpoints, tools, permissions, or workflow state. Provider output must match closed schemas, known field/context rules, and actual source blocks. Existing semantic checks recognize a bounded explicit-label grammar; they are not proof that arbitrary prose was interpreted correctly. Unsupported proposals cannot be approved directly. A broker correction/attestation must carry its own attributable support.

External extraction is optional and operator-configured. The default local parser makes no paid model call. Optional OpenAI requests use strict structured output and `store: false`; this setting is not a contractual retention or no-training guarantee. Gateway endpoints require HTTPS with redirects disabled. Approve provider account/model, DPA, residency, subprocessors, retention, logging, training use, incident terms, and cost before transmitting permitted data. Never send documents to user-supplied destinations.

## Storage, encryption, secrets, and audit

Private filesystem storage is the local default. S3 access uses server-generated keys, private SDK access, stored object versions, and conditional writes; actual bucket privacy, versioning, KMS encryption, least-privilege IAM, lifecycle/holds, and restore remain unverified infrastructure gates. No public document URLs are generated. Authorized streaming checks tenant scope rather than exposing object keys as capabilities.

Generated `.env.app` and `.env.database` are private ignored files. Do not print or commit them. Use distinct auth/runtime/migration/admin credentials and approved secret management for deployment. Secret rotation must account for stored database users, session revocation, fixture signatures, and encrypted TOTP material; replacing one file is not a complete rotation.

Audit stores actor/action/object/request metadata with minimal summaries. Runtime cannot update/delete/truncate audit history and protected evidence/review records. Database owners can change schema controls; production needs operator access review and protected archival/retention policy. Do not log document text, provider bodies, private file contents, raw tokens, or credentials to make debugging convenient.

## Retention, deletion, and recovery

No automatic retention purge, legal-hold workflow, or end-user tenant deletion is implemented. Excluding a document is a workflow action and preserves history; it is not erasure. Storage adapter delete methods do not constitute compliant tenant deletion.

Before customer data, approve category-specific retention, customer return/deletion, hold precedence, derivative/provider erasure, orphan cleanup, backup expiry, and restored-backup deletion enforcement. Maintenance must preserve tenant boundaries and a minimal deletion completion record without granting runtime blanket delete or bypass privileges.

[operations.md](operations.md) documents consistent-stop local backup, disposable database restore checks, queue recovery, and incidents. Local backups are private but not application-encrypted. Production recovery must restore actual document/artifact versions into an isolated store, verify approval hashes and authorized access, and meet approved measured RPO/RTO. Do not test recovery destructively against the developer database.

## Release gates and incident handling

Before customer data, complete domain validation; customer upload authority; counsel/contract/template-rights review; approved production identity/cloud/TLS/encryption; vendor review; independent tenancy/parser/security assessment; and exercised retention, recovery, access review, support, and incident processes. [pilot-readiness.md](pilot-readiness.md) is the detailed checklist. Paid pilots and commercial launch add their own contractual, quality, billing/support, and operational gates.

Preserve synthetic-only fail-closed checks until those gates and explicit authorization are recorded. Documentation or passing synthetic tests cannot satisfy them. New lines inherit all shared controls and require separate domain, rights, vendor-data-scope, and evaluation approval.

For suspected exposure, restrict affected access, preserve relevant evidence safely, engage designated security/legal contacts, rotate compromised credentials with dependency-aware procedures, and follow approved notification decisions. Do not paste sensitive incident material into issues or ordinary logs. Production staffing, notification commitments, dependency/container scanning, release provenance, and independent assessment remain open work.

# Development execution record

Authorization: user requested continuous implementation and review at the end on October 29, 2026. This record supersedes the earlier P1-01/P1-02-only status. No external deployment, real customer data, paid provider calls, or commercial/legal approval has been performed.

## Implemented

- Repository/tooling foundation, Next.js/React/TypeScript, FastAPI, locked Python/Node dependencies, Docker and CI.
- PostgreSQL migrations, restricted auth/runtime roles, forced tenant RLS, composite tenant references, immutable evidence/audit/reviews/revisions/approvals, database-sealed package manifests.
- Password/authenticator MFA enrollment, replay control, one-use recovery, opaque sessions/CSRF/origin checks, agency onboarding, role permissions, invitation links, agency selection, current permission checks and last-administrator protection.
- Account, submission, renewal lineage, dates/ownership/requirements, documents, sources, jobs, observations, deterministic discrepancies, missing information, decisions, canonical revisions, packages, approvals, exports, follow-ups, usage and reported time measurements.
- Private filesystem/S3 adapters; local private shared volume; Celery/Redis outbox/leases/recovery; bounded safe upload and PDF/XLSX/OCR parsing; ClamAV quarantine; signed fictional fixtures; structured provider abstraction including optional OpenAI Responses adapter.
- Broker review UI with source locations and original files; correction/omission/rejection/reasons; required readiness rules; manual attestations; explicit accepted missing-information exceptions.
- Original PDF/XLSX/ZIP templates, deterministic reviewed-fact narrative, document index, manifest and snapshot, exact-file approval and freshness verification; manual export only.
- Complete responsive dashboard/account/submission/review/tasks/package/settings flows and public marketing/security pages. Sites skill used in local-only repository mode; no Site registration/publication.
- Synthetic regression evaluation; unit/component, real-runtime PostgreSQL, and browser acceptance suites; local backup/disposable-restore tooling; operator and release-gate documentation.

## Deliberate scope choices

See ADR 0003. Local credentials provide a self-contained review build; managed OIDC is not connected. The default extractor is a label parser, not AI. External provider adapters have simulated-response tests; real paid model performance and vendor handling remain unverified. Local Compose uses filesystem storage after MinIO registry access failed; S3 infrastructure controls remain to be verified against an actual account.

Canonical CGL facts are validated namespace/context records in immutable snapshots; dedicated normalized line-specific projections are deferred until actual pilot query requirements justify them. Complex documents require domain evaluation; carrier/ACORD/AMS/mail/billing/other-line integrations remain explicitly deferred in plan.md.

## Verification record

Final results are recorded in docs/verification.md: 35 backend unit, 39 real PostgreSQL, 15 frontend unit/component, and 3 browser checks passed, together with actual OCR/scanning, schema/OpenAPI consistency, synthetic regression evaluation, and consistent backup/disposable restore verification. Generated logs/screenshots/archives are local ignored artifacts, not customer data. Production and synthetic-mode-off startup remain blocked by configuration validation.

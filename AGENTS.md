# Repository instructions for coding agents

## Product context and required reading

This repository builds an insurance document intelligence and workflow platform. Commercial General Liability (CGL) is the first supported domain implementation. Shared platform code must remain reusable for other lines; insurance semantics belong to the selected line's schema, rules, requirements, and templates.

Preserve the initial product promise: **Upload the documents. Identify missing or conflicting underwriting information. Review the facts. Generate a submission-ready package.** CGL extraction/classification, exposure/policy reconciliation, missing-information/discrepancy detection, human review/approval, and standardized generation/export are the required first-release path. Prioritize completing and verifying that path over additional lines or speculative abstractions. “Submission-ready” requires broker review and exact-package approval, with limitations disclosed; it does not imply carrier acceptance.

Before substantive implementation, read:

1. [Product requirements](docs/PRODUCT_REQUIREMENTS.md) for scope, users, invariants, and acceptance.
2. [Architecture](docs/ARCHITECTURE.md) for current components and proposed domain boundaries.
3. [Data model](docs/DATA_MODEL.md) for actual tables, provenance, versioning, and canonical snapshots.
4. [Security](docs/SECURITY.md) for controls, trust boundaries, and release restrictions.
5. [Roadmap](docs/ROADMAP.md) and [review remediation](docs/review-remediation.md) for unfinished work.

Consult [README.md](README.md) for setup, [implementation](docs/implementation.md) for code/API mapping, [operations](docs/operations.md) for local recovery, and [pilot readiness](docs/pilot-readiness.md) for release gates. [plan.md](plan.md) is the original detailed CGL baseline; the platform docs clarify reuse without waiving its acceptance criteria. ADRs in `docs/decisions/` explain narrower implementation choices. Use code/migrations for current physical behavior and clearly label proposed designs or gaps. If these disagree materially, surface the conflict and update the relevant documentation as part of authorized work; do not silently declare a requirement complete.

## Shared versus domain code

- Shared: tenancy/authentication, accounts/workspaces, ingestion/quarantine, private storage, parsing/OCR mechanics, provider invocation, source citations, job orchestration, exception/task lifecycle, review/revisions, approvals/manifests, export transport, audit/usage.
- CGL-specific: GL coverage fields, exposure/classification semantics, receipts/payroll/subcontractor reconciliation, loss-history checks, submission profiles, forms/exhibits, underwriting summary content.
- Current modules still contain CGL coupling. Put new insurance logic behind an explicit domain boundary as relevant code is touched. The general line contract is not implemented merely because it is documented. Do not add speculative schemas, empty frameworks, or rename all `cgl` packages/settings/DB roles without a concrete migration need.
- Never silently route an unsupported line to CGL rules, default all exposures to sales, or apply CGL loss/coverage requirements to every submission.

## Non-negotiable invariants

- Extraction yields unverified candidates. Model confidence, successful parsing, schema validity, and existing source IDs do not establish semantic truth or approval.
- Preserve original bytes, source blocks, observations, review decisions, revisions, packages, approvals, and attributable history. Reprocessing appends; it never overwrites broker decisions.
- Every exported material assertion requires validated document evidence, attributed attestation, or reproducible calculation from approved inputs. Corrections need their own support; original citations remain history unless revalidated.
- Keep missing/unknown/unreadable/not-applicable distinct from zero, false, or explicit no-loss statements. Excluded/replaced/failed sources stay in completeness accounting.
- Compare compatible entity, location, policy, period, role, basis, unit, currency, and scope. Preserve existing/requested/historical coverage and distinct claim valuations. Never silently resolve material conflict or invent classification, currency, or absence.
- Use exact decimal strings/Decimal for money. Do not introduce floating-point calculations, silent FX conversion, annualization, incompatible sums, or assumed incurred amounts.
- Omission proposals need authorized exception acceptance and disclosure; nonwaivable blockers remain blockers. Renewal candidates inherit no approval.
- Approval binds current reviewed inputs, source versions, manifest and actual artifact bytes. Material changes require new package/approval eligibility; retain historical approvals.
- Enforce tenant/current permission scope on APIs, workers, files, exports, and support paths. Client tenant IDs, UUIDs, or queue payloads cannot authorize access. Keep non-owner forced-RLS runtime and transaction-local scope; never use migration/admin credentials for requests.
- Treat uploaded text and provider output as untrusted data. No document-driven tools, endpoints, arbitrary code, or instruction overrides. Normal uploads stay quarantined if scanning is unavailable.

## Repository conventions

- `apps/web`: Next.js/React/TypeScript UI; `services/backend/app`: FastAPI/domain/workers; `services/backend/migrations`: Alembic; `packages/contracts`: generated OpenAPI/TypeScript.
- Python/Pydantic is authoritative for validation and API schemas. Run `make contracts` for relevant API changes and include generated outputs; never hand-edit generated contracts.
- Use explicit migrations for persistence changes. Preserve historical snapshot compatibility; version schema, pipeline, prompt, rules, requirements, template, and renderer changes. Do not rewrite immutable history to satisfy a new schema.
- Keep creation retries idempotent with request-hash binding; preserve optimistic versions/evidence fingerprints for concurrent edits. Queue delivery can repeat; lease/attempt validation and transactional outbox must remain correct.
- Add/update ADRs for material architectural choices and the canonical docs when behavior, ownership, or requirements change. Document limitations and distinguish implementation evidence from external acceptance.

## Local work and verification

Use `make bootstrap` for dependencies when needed. `make full-up` is the full synthetic product; `make scan-up` adds ClamAV. `compose.yaml` alone runs the health-shell smoke setup. Preserve existing work, private configuration, and named volumes. Do not use `down --volumes`, reset the developer database, or regenerate existing secrets to make checks pass.

Run checks appropriate to the change:

| Change                  | Relevant commands                                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| Documentation           | Local Markdown link/path review and Prettier on changed Markdown                                              |
| Backend                 | `make check-api`                                                                                              |
| Frontend                | `make check-web`; `npm run build` for affected build behavior                                                 |
| API schema              | `make contracts` plus backend/frontend checks                                                                 |
| Database/RLS/migrations | `make check-db` against guarded disposable local test databases; `make db-check` where configured             |
| Workflow/integrity      | Relevant regression tests; `make evaluate` for extraction/rules; `make check-e2e` with the full stack running |
| Ordinary upload/OCR     | `CGL_SCAN_ACCEPTANCE=1 make check-e2e` with scanner ready; `make verify-ocr`                                  |

`make check` combines backend/frontend checks. Do not invent successful results or report old verification as freshly run. Report unavailable prerequisites and skipped checks. Generated synthetic evaluation is regression evidence, not representative insurance accuracy, priced provider usage, or pilot approval.

## Release and data boundaries

The build remains local/test and synthetic-only with open functional and release requirements. Do not remove fail-closed gates, enable customer data, deploy publicly, make paid provider calls, provision billable infrastructure, or send submissions/messages externally without applicable explicit authorization and release evidence. Documentation requests do not authorize those actions.

Never print/commit `.env.app`, `.env.database`, credentials, MFA material, cookies, real documents, or provider bodies. Use fictional fixtures and minimal logs. No production compliance, SOC 2, accuracy, savings, integration, form-license, or managed-identity claims without supporting evidence. Storage adapters, resource-limited parsers, and reserved OAuth fields do not prove hosted storage security, independent isolation, or connected OIDC.

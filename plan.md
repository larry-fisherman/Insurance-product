# CGL submission and renewal platform — approved implementation plan

Planning date: October 28, 2026. Approved for implementation: October 29, 2026.

This is the planning baseline approved in the conversation. Implementation proceeds in the milestone order below. The first coding milestone is P1-01; later work must preserve the domain, review, security, and approval invariants. External accounts, billable infrastructure, production deployment, and customer-data use require their applicable authorizations and gates.

Recommendations and proposed thresholds are hypotheses until validated. ACORD licensing, proprietary classification rights, and specific AMS integration entitlements remain unverified.

## 1. Executive product overview

Build a commercial insurance preparation workspace that converts uploaded documents into traceable, reconciled, broker-approved Commercial General Liability (CGL) submission packages.

The central object is a submission workspace containing an insured account, intended policy period, immutable source documents, extracted observations, source references, discrepancies, missing-information tasks, broker decisions, canonical revisions, and approved packages.

Measure the outcome: reduced active preparation time with evidence supporting every material statement. Begin with three to five independent agencies and one or two agreed operational segments. The first complete workflow uploads an expiring policy, application, exposure spreadsheet, and loss runs; reconciles facts; records broker decisions; and exports a package.

The platform does not make autonomous coverage decisions, determine claim coverage, assign authoritative rating classifications, bind/issue/cancel/change coverage, send submissions externally, represent software approval as underwriting approval, or replace an AMS.

### Product invariants

1. Extraction alone never creates an approved insurance fact.
2. Material conflicts cannot be silently resolved by recency, model confidence, or majority vote.
3. Missing information never becomes zero, “no,” or “no losses.”
4. Every material exported fact has document evidence, attributed broker attestation, or a reproducible calculation with approved inputs.
5. Approval binds specific data, source versions, package manifests, and artifact bytes.
6. Material changes require a new package and approval; historical approvals remain recorded.
7. Tenant authorization applies to API requests, jobs, documents, exports, and support tools.
8. Uploaded instructions cannot change system behavior or invoke tools.
9. Existing, historical, and requested coverage remain separate.
10. No real customer data before security, vendor, contractual, and authorization gates pass.

## 2. Customers, users, and use cases

Primary customers are independent commercial agencies and small/midsize commercial P&C brokerages. Account managers prepare work; producers and authorized reviewers approve; marketing teams consume consistent packages; owners measure savings. Wholesale workflows need separate validation.

Initial uses: new-business CGL preparation, renewals, cross-document comparison, original submission summaries, supporting packages, completeness checks, and attributable corrections.

Pilot assumptions: US commercial insureds; English documents; USD calculations; occurrence-first CGL; PDF and XLSX; agency-wide access within each tenant; no cross-tenant sharing or outbound communications. Recognize claims-made forms and foreign currency without applying occurrence assumptions or automatic conversion. The pilot must exercise more than one exposure basis.

Discovery targets: interview eight users across relevant roles and observe ten authorized workflows. Record supported segments, packet sizes, scan quality, templates and rights, loss requirements, approval practices, materiality, preparation time, underwriter return reasons, purchasing authority, and willingness to pay.

## 3. Workflow and state model

1. Broker creates/selects insured, submission type, target period, owner, and dates.
2. Upload validates and quarantines immutable document versions.
3. Processing scans, classifies, segments, parses/OCRs, extracts, validates, and preserves evidence.
4. Reconciliation compares compatible observations with versioned deterministic rules.
5. Broker approves/corrects/rejects, requests information, or resolves permitted exceptions.
6. Reviewed information becomes an immutable canonical revision with provenance.
7. Generation freezes data and supporting versions into a package candidate.
8. Authorized approver reviews the exact candidate and approves its hashes/manifest.
9. Export returns unchanged approved artifacts; external distribution stays manual.

Business states: `draft → collecting → in_review → ready_for_package → awaiting_approval → approved → exported → closed`; also `cancelled`.

Document/job states: `uploaded → quarantined → scanning → parsing → extracting → validating → completed`; alternatives `rejected`, `needs_input`, `failed`, `cancelled`. Business and processing states are independent. Partial successes remain reviewable; failed/excluded documents remain visible in completeness checks. Export does not imply carrier receipt.

New relevant evidence increments the input-set version and invalidates current approval eligibility. Historical approvals remain preserved. Renewals copy prior information as unverified candidates, never inherited approval.

### Materiality and exceptions

Identity, operations, classification, geography, exposure amount/unit/basis/period, limits, retentions, significant provisions, dates, trigger, loss values/status/valuation/completeness, and assertions of absence are material by default.

Resolve a material discrepancy by selecting supported evidence, recording a correction with evidence/attributed attestation, or omitting the assertion and disclosing an authorized exception. An exception never converts an unresolved value into a verified fact.

Nonwaivable blockers: missing authorization; malware/unsafe file state; invalid evidence for asserted document-derived facts; invalid integrity; stale approval; unresolved conflicting asserted values; missing mandatory identity/period. Other omissions require authorized documented acceptance and final disclosure.

## 4. MVP scope and release boundaries

Priorities: P0 is necessary for the applicable workflow/safety gate; P1 supports commercialization/efficiency; P2 is deferred.

| Gate | Required scope |
| --- | --- |
| Functional MVP | Complete synthetic upload-to-approved-export workflow |
| Limited real-data pilot | MVP plus security, contracts, vendor approval, evaluation, training, recovery, and support |
| Paid pilot | Limited pilot plus paid terms, metering, invoicing, support commitments, and cost tracking |
| General launch | Repeatable onboarding, billing, production operations, independent security assessment, and demonstrated supported-segment quality |
| Expansion | Separately approved integrations, lines, portals, and advanced workflows |

Simplify: invitation onboarding, fixed roles, PDF/XLSX only, one versioned CGL schema and bounded ruleset, one approved extraction/OCR implementation behind interfaces, one authorized template, PDF/XLSX/ZIP, polling, in-app tasks and copyable questions, manual invoices/distribution, no vector database or autonomous agents, no mandatory AMS connection.

## 5. Non-MVP and expansion boundaries

Defer ACORD reproduction/filling pending rights; coverage recommendations; rating; automatic classification; appetite recommendations; carrier transmission/binding; email ingestion/sending; client portals; e-signature; AMS sync; quote comparison; multi-line packages; office/account access hierarchies; custom workflow/rule builders; enterprise SSO/SCIM unless a committed customer requires them; public API; cross-agency sharing; automated endorsement-interaction interpretation.

Future lines share accounts, documents, evidence, review, jobs, and approvals, but have separate domain schemas:

| Expansion | New requirements |
| --- | --- |
| Property | Buildings, values, construction, occupancy, protection, catastrophe exposure |
| Workers’ compensation | State/class payroll, experience modification, jurisdiction rules |
| Auto | Vehicles, drivers, garaging, use, schedules |
| Professional liability/cyber | Claims-made details, controls, applications, retroactive dates |
| Quote comparison | Quote terms, comparability, proposal review |
| Portals | External identity, restricted access, attestations |
| Carrier workflows | Recipient authority, transmission receipts, carrier requirements |

Do not implement these schemas prematurely.

## 6. Feature specifications

All F01–F22 belong in MVP. F23 is P1 and outside MVP. Each acceptance condition requires server-side enforcement where applicable.

| ID / priority | Problem and functionality | Dependencies / technical considerations | Acceptance criteria | Security |
| --- | --- | --- | --- | --- |
| F01 P0 | Controlled agency onboarding, invitation login, MFA, recovery | Managed identity, sessions, expiring invites | Invite joins only intended agency; MFA required for production; disabled member loses subsequent access | Validate identity issuer/audience/state; secure cookies; CSRF; rate limits |
| F02 P0 | Separate agencies on shared infrastructure | Tenant repositories, RLS, scoped storage/workers | Cross-tenant API, FK, job, citation, download, export tests fail closed | Derive scope from membership |
| F03 P0 | Separate preparation/approval/admin authority | Fixed permission bundles | Preparers cannot approve; viewers cannot edit; admin does not imply approval | Audited privilege changes; no default support document access |
| F04 P0 | Stable insured account and history | Tenant accounts, search, archival | Create/search/edit/archive; flag similar names without merging | Scoped results and errors |
| F05 P0 | Assign work and manage renewal dates/status | Accounts, memberships, transition rules | Create new business/renewal; owner/dates; unverified carry-forward | Audit changes and transitions |
| F06 P0 | Organize trustworthy immutable documents | Private storage, quarantine, hashes, finalization | Interrupted upload recovery; new version on replacement; unsafe files never parsed | Limits, signatures, malware, no cross-tenant dedup |
| F07 P0 | Read PDF scans/tables and spreadsheets | PDF parser, OCR, XLSX, evidence locators | Mixed PDFs; preserved cells/pages; no formula execution | Isolated bounded parsers and egress |
| F08 P0 | Select relevant document extraction | Taxonomy, parsed text, overrides | Policy/application/loss/schedule classification; mixed segments; unknown when uncertain | Output cannot change permissions/tools |
| F09 P0 | Eliminate repetitive insurance entry | Field registry, provider adapter, strict schemas | Schema-valid observations; material evidence; explicit absence | Minimize vendor content; reject invented evidence IDs |
| F10 P0 | Explain extracted facts | Immutable versions, geometry/cells | Click opens correct page/cells with raw and normalized values | Reauthorize evidence access; no signed URLs in logs |
| F11 P0 | Detect compatible conflicting facts | Normalization, deterministic rules | Comparable conflicts surfaced; noncomparable contexts labeled; no silent selection | Controlled/audited rule versions |
| F12 P0 | Detect omissions, stale/unreadable data | Requirement profiles and document inventory | Differentiate missing/stale/unreadable/not applicable; configurable loss periods | Cannot bypass nonwaivable blockers |
| F13 P0 | Queue unresolved work | Findings, owners, dates | Assign/resolve with reason; accept permitted exceptions; blockers stop approval | Restricted material-exception authority |
| F14 P0 | Correct fallible extraction | Viewer, decisions, concurrency | Approve/correct/reject/request; preserve raw/history and reason | Reject stale edits; no canonical bypass |
| F15 P0 | Assemble consistent submission | Canonical revision, template, renderer | Overview/exposures/coverage/losses/exceptions/narrative/exhibits | Escape content; no scripts/remote loads |
| F16 P0 | Record exact authorized approval | Readiness, immutable files, hashes | Exact revision approved; concurrent changes cannot approve stale data | Transactional membership and permission checks |
| F17 P0 | Work with existing distribution | Approved PDF/XLSX/ZIP | Matches review; periods/units retained; manifest complete | Formula injection/archive paths/download access |
| F18 P0 | Accountable history | Append-only events and actor/version IDs | Upload/edit/decision/approval/export/role/support attribution | No runtime audit update/delete; minimize sensitive payload |
| F19 P0 | Understand and recover failures | Durable jobs, attempts, retry | Per-document stages; actionable errors; idempotent retries; stall detection | Safe error codes, no secrets/document data |
| F20 P0 | Agency administration | Roles, settings, audit, retention | Invite/disable, usage, defaults, deletion requests | Protect last-admin recovery; separate approval grant |
| F21 P0 | See renewal and blocked work | Dates, owners, findings | Due work, blockers, failures, owner filters; date-only fields stable | Tenant-scoped aggregates |
| F22 P0 pilot | Demonstrate savings and costs | Minimal activity and usage events | Compare submission effort/cost to documented baseline | No keystroke/screen/content analytics |
| F23 P1 | Scale purchasing/admin | Validated pricing, provider, metering | Reconciled subscriptions/usage; platform retries not double billed | Provider-hosted card data; signed idempotent webhooks |

## 7. CGL domain and data requirements

Insurer descriptions establish context, not uploaded policy wording. [Travelers CGL overview](https://www.travelers.com/business-insurance/general-liability) describes distinct exposures and conditions coverage on actual terms and circumstances. The proposed dictionary needs an experienced commercial insurance reviewer’s approval.

### Shared material-field representation

Stable key/schema version; raw text; typed value; units/currency; entity/operation/location/policy/claim context; period/as-of date; role (historical/expiring/requested/current estimate/audited actual/unspecified); evidence; extraction version; availability; review; decision; derivation inputs/version.

Keep availability (`present`, `missing`, `unreadable`, `not_supplied`, `explicitly_unknown`, `not_applicable`) separate from review (`unreviewed`, `verified`, `corrected`, `rejected`, `superseded`), evidence quality, and comparison (`consistent`, `conflicting`, `noncomparable`, `insufficient_evidence`). Explicit “none” differs from absence.

### Insured identity

Broadly useful: legal name, DBAs, entity type, mailing address, locations/address purposes, business description, established date, agency account reference. Distinguish display name from named insured. Conditional: entity schedules, ownership/parent/subsidiary relationships, acquisitions/divestitures, necessary contact and identifiers. Avoid unnecessary FEIN/personal data. Similar names never establish identical legal entities.

### Operations/classification

Capture activities, operation/location relationships, geographic exposure, supplied codes/descriptions and system/edition/period, subcontracted work, operational changes, customer-site work, manufacturing/import/distribution/installation. Conditional supplemental fields include residential/commercial mix, height/depth, excavation/demolition/hot work, public access/events, safety and risk-transfer practices. Preserve supplied facts without certifying adequacy. No universal code interchange or invented supplemental answers.

### Flexible exposures

Each exposure row: entity, operation, optional location, supplied class/system, basis, exact quantity, unit, currency where relevant, start/end, actual/audited/estimated/projected/unspecified status, gross/net definition, included/excluded components, evidence, and review.

Bases: sales, receipts, payroll, subcontract_cost, area, admissions, units, other, unknown. Preserve original label. Never default to revenue; convert between unrelated bases; annualize without approved visible calculation; or sum incompatible currencies/periods/units/overlapping components. Store supplied rating denominator separately without calculating premium. Prior actual versus current estimate is change analysis rather than automatically a conflict.

### Policy/coverage

Separate existing/expiring, requested, historical, and future quoted contexts. Capture carrier, policy number/line/type, dates, occurrence/claims-made/unknown trigger, occurrence/general aggregate/products-completed operations/personal-advertising injury/damage to rented premises/medical payments limits, deductibles/SIR and basis/applicability, aggregate scope, defense treatment, form numbers/editions, endorsements, exclusions/sublimits/conditions, additional insured/primary-noncontributory/waiver requirements where supplied, claims-made retroactive/reporting details, audit basis, and optional contextual premium.

Declarations/certificates do not establish all terms; missing endorsements do not prove absence; titles do not establish effect; contractual requirements do not establish policy satisfaction. Complex summaries require source wording and broker review.

### Loss history

Separate claim identity from valuations. Capture carrier/policy/source claim ID, entity/location, occurrence/report date, description/category, status, paid indemnity/expense, outstanding reserves by compatible component, reported incurred, recoveries/adjustments, gross/net/deductible treatment, currency, valuation date, report period/run date, policy period, duplicate matching, and evidence.

Never add successive valuations; treat claim IDs as globally unique; merge ambiguous matches; infer zero losses; or assume incurred equals paid plus reserves without compatible definitions. Keep reported/calculated amounts distinct. Explicit no-loss statements must identify period/evidence. Show gaps and mixed valuation dates. Do not calculate loss ratio without comparable approved premium. Trends are descriptive, not predictive risk scores.

### Submission/renewal

Line/type, insured/prior link, expiring policies, intended period, renewal/internal/market-ready dates, preparer/approver, requirement version, input-set/current revision, inventory, tasks, history, approval/package/export, closure/archive. Required loss periods/freshness come from approved profiles/instructions, never a universal three/five-year assumption.

## 8. Technical architecture

Modular monolith in a monorepo: Next.js/React/TypeScript web, Python/FastAPI API, Celery workers, managed PostgreSQL, Redis delivery queue, private object storage, managed OIDC identity, and narrow approved OCR/extraction adapters. SQLAlchemy/Alembic for persistence, Pydantic for validation, Docker and CI/CD. Pin compatible supported versions.

Modules: identity/tenancy, accounts, submissions, documents/evidence, processing, CGL, reconciliation, review, packages/approval, audit/admin, usage/cost. Keep domain logic out of routes/tasks.

One browser origin routes `/api` to FastAPI. FastAPI owns sessions/authorization; OIDC code flow with state/nonce/PKCE; tokens server-side; secure HttpOnly cookies; CSRF; rechecked membership; explicit authorized tenant switch.

Shared schema with tenant ownership, repository scope, RLS, composite tenant FKs, scoped object/jobs/cache. Runtime roles cannot own tables or have superuser/BYPASSRLS. Use transaction-local tenant context and test pooled connection reuse. [PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

Use Celery for long-running work rather than request-process tasks. [FastAPI guidance](https://fastapi.tiangolo.com/tutorial/background-tasks/). PostgreSQL owns job state; Redis delivers. Tasks are idempotent; late acknowledgement is not exactly-once. [Celery tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html).

Transactional outbox, stage uniqueness, leases/heartbeats, bounded backoff/timeouts, checkpoints, recovery sweep, and provider cost tracking. Queue loss cannot erase state. Unknown provider timeout outcomes may create duplicate vendor cost; account for it and use vendor idempotency where available.

Deployment: one approved US region, separate local/staging/production, managed containers/DB/Redis/storage/secrets, private DB/queue networking, separate parser/extraction/API permissions. No Kubernetes, multi-region, or separate microservice repositories initially. Synthetic local/CI data only.

Costs: native extraction first, selective OCR, document segments/field groups, tenant-local content/version caching, page/token/retry/concurrency limits, submission/agency budgets, vendor/infrastructure metering. No cross-tenant caching.

## 9. Repository structure

```text
plan.md
apps/web/
services/backend/app/{api,identity,tenancy,accounts,submissions,documents,processing,domain/cgl,reconciliation,review,packages,audit,usage}/
services/backend/{migrations,tests}/
packages/{api-client,contracts}/
config/{field-registry,requirement-profiles,reconciliation-rules,prompts,templates}/
evals/{synthetic,annotations,suites,reports}/
infra/{local,environments}/
docs/{architecture,decisions,security,operations,product}/
.github/workflows/
```

Create directories when needed. Python schemas are authoritative; generate TypeScript from OpenAPI. Backend owns domain validation. Version prompts/rules/schemas/templates. Customer fixtures stay in controlled storage, outside ordinary Git. Separate public marketing and authenticated routes.

## 10. Database design

Conventions: UUID PKs; tenant-owned rows have non-null tenant_id and unique (tenant_id,id); tenant-aware FKs; UTC timestamptz; date-only policy/exposure dates; exact decimals with currency/unit; string external IDs; row_version for mutable operations; immutable records protected from runtime mutation. Structural fields required; absent insurance values represented explicitly.

### Identity/tenancy

| Table | Fields/relationships |
| --- | --- |
| tenant | id PK, name, status, timezone, created_at; approved setting-policy versions |
| app_user | id PK, issuer/subject unique, display_name, status; global identity only |
| membership | id PK, tenant_id, user_id, status; unique tenant/user |
| role / permission | fixed global stable keys |
| role_permission | role_id/permission_id composite PK |
| membership_role | tenant_id, membership_id, role_id |
| invitation | tenant/email/token_hash/expiry/inviter/status; single-use |
| session | user_id, secret_hash, expiry, revoked_at; separate membership checks |

Roles: viewer, preparer, approver, tenant administrator; platform operator has operational metadata without ordinary document access. Multiple role bundles allowed.

### Workflow

| Table | Fields/relationships |
| --- | --- |
| insured_account | tenant, display_name, status, created_by, row_version, optional external reference |
| policy | insured_account_id, line_code; stable identity, details in snapshots |
| submission | insured, line, type, status, owner, intended dates, input_set_version, current_revision_id, row_version, optional prior_submission/expiring_policy |
| submission_requirement | submission, requirement_key, profile_version, applicability, status |
| work_item | submission, kind, status, owner, due_date |
| submission_revision | submission, revision_number unique, schema_version, input_set_version, snapshot_json/hash, creator/time; immutable |

Renewal is a submission type with links, not a duplicate state machine. Add recurring renewal-program entities only when needed.

### Documents/provenance

| Table | Fields/relationships |
| --- | --- |
| document | insured, display_name, creator; logical container |
| document_version | document, version unique, object key/version, sha256, bytes, detected MIME, scan state, uploader; immutable |
| submission_document | submission, exact version, role, inclusion status |
| document_segment | version, kind, validated range, classification state |
| parse_artifact | version, parser version, private artifact key/hash |
| source_reference | version, parse artifact, locator type/validated JSON, excerpt |
| extraction_run | version/segment/job, pipeline/provider/model/prompt/schema versions, status, cost |
| extracted_observation | run, field path, type, raw/normalized values, availability, context; immutable |
| observation_source | tenant/observation/source; many-to-many |

PDF locators retain physical page index, printed number separately, geometry, and text offsets. XLSX locators retain exact version, sheet, cells/ranges, and header context.

### Canonical records

Immutable validated submission_revision.snapshot_json is authoritative. Transactionally generate query projections; never expose independent projection mutations.

| Projection | Principal fields |
| --- | --- |
| insured_snapshot | revision, legal name/state, entity type, mailing address, DBAs, established date |
| entity_relationship_snapshot | revision, source/target entity, relationship, supplied ownership |
| location_snapshot | revision, stable row key, address/purpose, operation links |
| operation_snapshot | revision, row key, description, entity/location, geography, namespaced classes |
| policy_snapshot | revision, row key, policy identity/context, carrier/number/dates/trigger |
| cgl_exposure | revision, row key, entity/operation/location/class, basis/quantity/unit/currency/period/status |
| coverage_term | revision, row key, policy/request context, key, typed value, applicability |
| policy_provision | revision, row key, form/edition/category, reviewed summary/applicability |
| claim_snapshot | revision, row key, claim identity, dates/description/status, selected valuation |
| canonical_field_provenance | revision, validated field path, decision or derivation |

Validate every provenance path, write snapshots/projections atomically, and test consistency. Future lines add domain sections/projections rather than forcing all insurance into one EAV table.

### Claims/review/packages

| Table | Principal fields |
| --- | --- |
| claim_identity | insured, internal reference; source number not globally unique |
| claim_observation | optional identity, extraction run, identifiers/descriptors |
| claim_valuation | observation, valuation availability/date, components/currency/basis |
| claim_match_decision | observation links, outcome/reviewer/reason |
| reconciliation_run | submission/input-set/ruleset/status |
| discrepancy | run/rule/path/kind/severity/status |
| discrepancy_observation | finding/input links |
| review_decision | submission, action/path, selected/corrected value, reviewer/reason/time/input context; append-only |
| review_decision_source | decision/evidence links |
| derivation | revision, calculation key/version, inputs/result |
| exception_resolution | typed FK target, resolution/reviewer/reason; no unconstrained polymorphic IDs |
| package | submission/revision/input set/template, manifest hash/status; frozen after rendering |
| package_artifact | package/format/key/version/hash/size |
| package_document | package/exact version/order |
| approval | package/approver/revision hash/manifest/time; append-only |
| approval_event | approval/type/reason/actor/time; supersession/revocation |
| export_event | package/approval/actor/format/time |

### Operations

| Table | Principal fields |
| --- | --- |
| processing_job | submission/optional document/stage/status/idempotency key/lease/heartbeat |
| job_attempt | job/attempt unique/status/times/safe error/provider request ID |
| outbox_event | type/aggregate/safe payload/published_at |
| audit_event | tenant/actor/action/object/request/time/safe summary; append-only |
| usage_event | submission/job/type/quantity/unit/estimated cost/rate version |
| retention_policy | tenant/version/category/settings/approval |
| deletion_request | scope/status/authority/hold/completion |
| support_access_grant | operator/scope/reason/expiry/authorizer |

Indexes: tenant/status/due date; tenant/insured/created; document/version unique; tenant/hash; tenant/submission/field; finding/status/severity; job/attempt unique; partial runnable/stalled jobs; audit tenant/time/object; package approvals/exports. Add targeted JSON indexes only for real queries.

Version source bytes, observations, decisions, and sealed snapshots. Each saved review batch creates a revision; small full copies are acceptable initially. Mutable metadata uses optimistic locking/audit. Erasure covers object versions, derivatives, packages, caches, vendors, and backup expiry; legal holds require review. Soft deletion is insufficient by itself.

## 11. API design

REST `/api/v1`, JSON, OpenAPI-generated TS, cursor pagination, safe errors/request IDs, HTTP 202 for jobs, idempotency for retriable creates/process/generation/approval, If-Match/revision checks, 409 stale/transition conflicts, 422 invalid input, tenant-scoped 404 where existence is sensitive. Authorize every referenced child.

| Area | Endpoints |
| --- | --- |
| Identity/agency | GET /me; login/logout/session; GET/PATCH /agency |
| Membership | GET /memberships; POST /invitations; PATCH /memberships/{id} |
| Accounts | GET/POST /insureds; GET/PATCH /insureds/{id} |
| Submissions | GET/POST /submissions; GET/PATCH /submissions/{id}; POST /submissions/{id}/renewals |
| Uploads | POST /submissions/{id}/uploads; POST /uploads/{id}/complete |
| Documents | GET /submissions/{id}/documents; POST /documents/{id}/versions |
| Processing | POST /document-versions/{id}/process; GET /jobs/{id}; POST /jobs/{id}/retry |
| Evidence | GET /source-references/{id}; POST /document-versions/{id}/access |
| Review | GET /submissions/{id}/observations; POST /submissions/{id}/reconciliations; GET /submissions/{id}/exceptions; POST /exceptions/{id}/resolutions; POST /submissions/{id}/review-decisions |
| Canonical | GET /submissions/{id}/revisions/{revision}; GET /submissions/{id}/readiness |
| Packages | POST /submissions/{id}/packages; GET /packages/{id}; POST /packages/{id}/approvals; POST /packages/{id}/exports |
| Audit/admin | Submission audit; scoped usage/retention/deletion |

Approval transaction rechecks membership/permission, serializes against mutations, verifies expected input-set/revision/readiness/exception dispositions and hashes, inserts approval/audit, marks current package approved. Never regenerate approved bytes. Candidate status is UI metadata; any future watermarked drafts are separate artifacts. No sending/binding endpoints in MVP.

## 12. AI processing design

Upload → validate/quarantine/scan → segment/classify → parse/OCR → extract → validate schema/evidence → normalize → reconcile → identify missing information → human review → canonical revision → generate → approve/export.

LLMs classify, map varied wording, extract from provided content, suggest questions from known gaps, and optionally draft from approved facts. Deterministic Python owns arithmetic, normalization, comparability, missing requirements, claim aggregation, permissions, state, approval, integrity, and exports. No model coverage determinations, autonomous value selection, web lookup, or tools.

Scans: detect text/quality per page, OCR selectively, retain originals/page geometry/rotation/table cells, flag unreadable/handwritten content without silent discard. Textract supports bounding boxes/polygons, but suitability needs corpus evaluation. [Textract geometry](https://docs.aws.amazon.com/textract/latest/dg/text-location.html).

XLSX: preserve values/formulas/formats/merged ranges/hidden metadata/sheet/cell lineage; never execute formulas/macros/links/objects. Cached results may be stale; distinguish rows/subtotals/grand totals; do not double count hidden duplicates. [Openpyxl cached-value behavior](https://openpyxl.readthedocs.io/en/stable/tutorial.html).

Evidence-constrained extraction receives server-issued block IDs. Require field/raw/typed/context/evidence/uncertainty. Validate existing authorized blocks, semantic support, context, and reproducible normalization. Matching a number alone is insufficient. Prefer deterministic numeric reading from cited blocks/cells.

Uncertainty signals: OCR problems, ambiguous mapping, missing period, conflict, unknown namespace/unit/currency, stale formula, incomplete policy. Review prioritization is not proof of correctness; do not display uncalibrated confidence percentages.

Rules specify ID/version/line/applicability/inputs/comparability/calculation/severity/explanation/resolutions/tests/domain approval. Initial rules: identity; compatible exposures; year-over-year changes; compatible totals; locations; same-policy dates; like-context coverage; loss gaps/staleness; duplicate candidates; compatible claim arithmetic; missing referenced endorsements; operational changes; unknown units/bases. No universal percentage threshold without approved configuration.

Narratives receive only frozen approved facts. Map material statements to field paths; prohibit invented safety strengths/loss explanations/adequacy claims. Label existing/requested, disclose exceptions, audit broker edits, fall back to deterministic prose on validation failure.

Treat uploaded content as untrusted data. Separate instructions/data, remove tool/network abilities, keep credentials out, validate outputs, escape rendering, scope inputs before calls. Test visible/OCR/hidden-cell/comment injection. Prompts alone are no security boundary. [OWASP prompt injection](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

Record pipeline/parser/OCR/provider/model/prompt/hash/schema/field registry/normalization/rules/requirements/template/renderer versions. Retry transient failures with limits; preserve partial results; stop persistent invalid output; ask for better documents. Reprocessing creates observations and can reopen review, never overwrite broker decisions.

## 13. Security and compliance checklist

Before real data: data inventory/threat model; API/DB/job/storage isolation tests; MFA; server permissions; TLS; encrypted private storage/DB; least privilege; quarantine/malware/bounded parsing; managed secrets; protected audit; safe logs/analytics; tested backups/restore; retention/deletion; incident owner/runbook; vendor terms/subprocessors; agency document authority; claimant-data minimization; no model training absent separate authorization.

Layer upload controls; extension/MIME alone is insufficient. [OWASP uploads](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

Document access: authorize first; scope exact object/version/action; proposed five-minute URLs; no URL logging/referrer leakage; safe viewer headers; reauthorize renewal. Presigned links are bearer credentials and may survive membership revocation until expiry. [S3 guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).

Before paid pilots: agreements/data terms; supported-use boundaries; customer retention; support/escalation; metering; deletion exercise including derivatives; independent security review; incident tabletop; evaluation; broker training; liability/E&O review; regions/subprocessors; complaints/corrections.

Before broader launch: independent penetration/isolation assessment; remediate/retest critical/high issues; alert/on-call ownership; access reviews; repeated restore; migration/rollback; capacity; vulnerability process; onboarding/offboarding; legal/procurement materials; billing/disputes; change/vendor reviews; SOC 2 readiness if needed. Do not claim “SOC 2 certified.”

Professional review: producer boundaries by state; insurance privacy and contractual roles; agency sharing authority; ACORD forms/standards separately; classification/manual rights; vendor training/retention/human access/regions/deletion/incidents; liability/E&O; state requirements; legal holds; privacy exemptions; notification duties; SOC 2 scope. Controls do not themselves establish legal compliance.

[NAIC cybersecurity](https://content.naic.org/cipr-topics/cybersecurity) describes the model law; enacted state law determines applicability. [NY DFS resources](https://www.dfs.ny.gov/industry_guidance/cybersecurity) address covered entities/exemptions; SaaS status/customer contracts need separate analysis. ACORD terms were inaccessible and remain unverified; use original/authorized templates.

## 14. UI page map and public website

Desktop-first, responsive, accessible keyboard navigation, clear type/tables, explicit extracted/verified/missing/conflicting labels, no color-only states, unsaved-change protection, evidence beside fields, honest progress. Avoid unexplained “AI recommended value.”

| Screen | Purpose/components/actions | Data/permission | Loading/empty/error |
| --- | --- | --- | --- |
| Auth | Login/MFA/recovery/invite | Identity/session | Expired/recovery guidance, no enumeration |
| Dashboard | Due/blocked/failing work, owner filters | Scoped submissions/tasks | Skeleton/first-create/retry |
| Directory | Search/create/archive | Account summaries; preparer | No results, duplicate warning |
| Insured | Profile/policy/submission history | Account scope | Empty history/stale edits |
| Workspace | Dates/owner/status/readiness/navigation | Submission | Setup checklist/partial processing |
| Documents | Upload/versions/role/include/exclude | Sources/scan; preparer | Progress/unsupported/quarantine |
| Processing | Stage/time/partial/retry | Safe jobs/errors | Queued/working/needs input |
| Structured data | Identity/operations/exposure/coverage/loss | Observations/canonical | Explicit missing/stale conflict |
| Comparison | Candidates and sources, review actions | Evidence/findings/reviewer | Source loading/unreadable path |
| Tasks | Owner/date/reason/questions | Requirements/work items | No tasks does not imply universal completeness |
| Builder | Sections/facts/exhibits/narrative | Revision/template | Blockers/generation retry |
| Approval/export | Full preview/exceptions/version/approve/download | Exact package/approver | Integrity/stale disables approval |
| Settings/users | Invites/roles/branding/retention/usage | Admin | Validation/last-admin protection |

Exposure review shows raw candidates, basis, period, actual/estimate, scope/location, source, comparison reason, select/correct/reject/request/omit actions.

Public website: home, workflow, CGL uses, security/data handling, demo/pilot, validated pricing, privacy/terms/contact. No perfect-accuracy/compliance/coverage/unproven savings claims. Contact forms collect necessary contact/context, never insurance files.

## 15. Testing and evaluation

Unit: money/date/context/claims/readiness. Schema: extraction/canonical/locators. DB: tenant FKs/RLS/roles/transactions/migrations. API: authorization/idempotency/concurrency. Workers: redelivery/crash/outbox/stalls. Storage: quarantine/finalization/version access/expiry. End-to-end: complete submission/renewal. Security: malicious uploads/injection/access. Rendering: facts/layout/hashes. Recovery: restore/queue/provider/deletion.

Proposed corpus: at least 100 fictional/synthetic/authorized packets; 60 development/40 held-out; split template/source families and insureds; native/scanned/mixed PDFs/XLSX; multiple bases/locations/periods/valuations; incomplete/ambiguous/adversarial content; 200 labeled discrepancies; critical regression suite. Experienced domain annotation, representative double-labeling/adjudication. Synthetic success does not prove customer performance.

| Metric | Definition | Proposed pilot target |
| --- | --- | --- |
| Material extraction | Correct contextual normalized values / produced material values | ≥98% |
| Money/exposure | Exact amount/unit/currency/basis/period among produced | ≥99% |
| Coverage of readable supplied fields | Correct supported fields / supported readable supplied | ≥90%; abstentions separate |
| Discrepancy recall | Detected / labeled material findings | ≥95% |
| Precision | Correct / raised findings | ≥85% |
| Reference validity | Correct version/location resolution | 100% |
| Semantic support | Actually supported material observations | ≥99%; all exported material facts reviewed |
| Unsupported package facts | Unsupported material assertions | Zero observed in release corpus |
| Correction | Corrected / reviewed material extractions | Hypothesis ≤10%, segmented |
| Processing | Upload complete to review-ready | P95 ≤10 min for agreed 100-page packet |
| Variable cost | OCR/AI/retries/rendering/allocated variable infra | Hypothesis ≤$3 reference packet |
| Labor savings | Matched baseline vs assisted active time | Hypothesis ≥40% median |

Report denominators/failures/document mix/uncertainty. Do not omit failed packets. Zero observed errors is not a universal guarantee. Track false alerts by rule without sacrificing recall.

Critical cases: same amount/different limit; factor-ten OCR; sales/payroll mismatch; actual/estimate; unit mismatch; requested/current; similar entity names; duplicate claims/multiple valuations; missing/partial no-loss evidence; stale formula; hidden instructions; absent endorsements; upload during approval; worker partial commit; cross-tenant job/document; disabled-member link; delete then backup restore.

[NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework) informs voluntary governance, not certification.

## 16. Integration roadmap

Import/export first. Stable IDs, optional external refs, versioned CSV/XLSX/PDF/ZIP, mapping records, and audit. No assumed proprietary access.

| Integration | Sequence/access |
| --- | --- |
| Applied Epic | Manual exports; later account/document sync; API/partner/license/customer rights unverified |
| AMS360 | Manual exports; portal exists but specific access/terms require confirmation |
| EZLynx | Manual workflow; rights/capabilities unverified |
| Microsoft 365/Outlook | Later user-selected import; explicit approval for sending; customer consent/permissions |
| ACORD | Rights review; standards and form rights separate |
| Carriers/MGAs | Export then approved transmission/receipts; individual contracts/credentials |
| E-signature | Later attestation; provider agreement/authority/consent |

[Vertafore portal](https://developer.vertafore.com/) is not entitlement. Prefer narrow delegated Graph access where adequate. [Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-overview).

Connectors require connection/revocation, mapping, idempotency, checkpoints, conflicts, authority rules, permission-aware retries, and receipts; never automatically overwrite reviewed facts.

## 17. Phased implementation and tickets

Tickets should normally fit one to three engineering days; split larger work. Shared done: reviewed change, relevant tests, migration impact, security review, documentation, demonstrated acceptance.

### Phase 0 — discovery and approval

Objective/deliverables: approved scope, rules, rights, architecture, success metrics.

- P0-01 interview/observe workflows.
- P0-02 select segments/exclusions.
- P0-03 approve fields/materiality/requirements/exceptions.
- P0-04 document/template rights.
- P0-05 threat model/vendors/data/retention.
- P0-06 decisions/API/evaluation/backlog.
- P0-07 baseline time/protocol.

Dependencies: customer/domain participation. Acceptance: named owners approve, unresolved items have blockers/defaults. Tests: define fixtures/expected behaviors, no app yet. Security: no unauthorized customer experiments. Done: approved plan and coding authorization.

### Phase 1 — foundation

- P1-01 monorepo, web shell, API health/readiness, lockfiles, local startup, CI, README.
- P1-02 tenant/identity/membership/roles/audit/outbox migrations.
- P1-03 transaction scope/RLS/composite FKs/isolation harness.
- P1-04 managed identity/session/MFA/recovery boundaries.
- P1-05 storage/job interfaces/local services/worker skeleton.
- P1-06 logs/secrets/dependency security/environment validation.
- P1-07 staging definitions/deployment/backups/rollback.

Deliverable: safe synthetic foundation. Dependencies: approval/vendor choices; external provisioning separately authorized. Acceptance: scope fails closed, reproducible startup, CI. Tests: health/config/migrations/auth/roles/tenant CRUD/connection reuse. Security: no bypass role or production development auth. Done: demonstrated synthetic foundation.

### Phase 2 — accounts and workspaces

- P2-01 invitations/memberships/roles.
- P2-02 insured CRUD/search/archive/duplicate warnings.
- P2-03 submissions/owners/dates/status/dashboard.
- P2-04 renewal links/unverified carry-forward.
- P2-05 audit/concurrent metadata edits.

Dependencies: Phase 1. Acceptance: preparer creates insured/renewal; disabled member blocked; viewer read-only. Tests: roles/transitions/stale edit/dates/approval reset. Security: single-use scoped invites/search. Done: audited account-to-submission workflow.

### Phase 3 — safe document intake

- P3-01 upload/finalize/hash/immutable version.
- P3-02 quarantine/scanning/allowlists/limits.
- P3-03 native PDF text/layout/page inventory.
- P3-04 OCR/mixed pages.
- P3-05 XLSX cells/formulas/hidden metadata.
- P3-06 segmentation/classification/override.
- P3-07 outbox/checkpoints/retries/stalls.
- P3-08 document/version/status UI.

Dependencies: Phase 2/storage/OCR approval. Acceptance: inspectable sources; unsafe files isolated; recovery works. Tests: malformed/encrypted/bomb/duplicate/page mapping/crash/queue loss. Security: bounded parser permissions/egress/temp cleanup. Done: file-safety corpus passes.

### Phase 4 — extraction and evidence

- P4-01 CGL schemas/field registry.
- P4-02 provider and deterministic test adapter.
- P4-03 segmented schema/evidence-constrained extraction.
- P4-04 normalization.
- P4-05 observations/source/version persistence.
- P4-06 structured data/evidence viewer.
- P4-07 usage and evaluation.

Dependencies: Phase 3/vendor. Acceptance: material evidence, explicit absence, no automatic canonical data. Tests: bad JSON/fabricated citations/units/injection/timeouts/normalization. Security: minimized tenant inputs/untrusted output. Done: held-out report/error analysis.

### Phase 5 — deterministic reconciliation

- P5-01 versioned rule engine/applicability.
- P5-02 identity/dates/coverage/location.
- P5-03 exposure/context/totals/changes.
- P5-04 claims/valuations/duplicates.
- P5-05 loss periods/freshness.
- P5-06 requirements/tasks.
- P5-07 queue and rule evaluation.

Dependencies: Phase 4/domain approval. Acceptance: valid comparisons, explanations, no silent selection. Tests: positive/negative/missing/boundary/nonapplicable per rule. Security: no executable user rules. Done: targets met or supported scope explicitly narrowed.

### Phase 6 — review and canonical records

- P6-01 attributed decisions/reasons.
- P6-02 immutable snapshots/projections.
- P6-03 evidence/attestation lineage.
- P6-04 exception authority/blockers.
- P6-05 readiness/concurrency.
- P6-06 approval contract/stale-input invalidation.
- P6-07 comparison/review/history UI.

Dependencies: Phase 5. Acceptance: provenance for every material fact, authorized resolutions, no overwrite. Tests: transitions/lineage/projections/privileges/stale/concurrent upload-approval. Security: no bypass endpoint. Done: synthetic submission ready for package.

### Phase 7 — package and export

- P7-01 original template/assembly.
- P7-02 contextual exposure/coverage/loss tables.
- P7-03 fact-mapped narrative/fallback.
- P7-04 PDF/XLSX/ZIP manifests.
- P7-05 preview/exact approval.
- P7-06 authorized exports/historical versions.
- P7-07 broker content/layout validation.

Dependencies: Phase 6/template rights. Acceptance: reviewed bytes match export, no unsupported material facts, exceptions visible. Tests: money/missing/injection/archive/hash/stale/narrative. Security: no arbitrary renderer scripts/URLs. Done: end-to-end workflow passes.

### Phase 8 — pilot gate

- P8-01 frozen held-out report.
- P8-02 tenant/upload security review.
- P8-03 restore/queue/provider/stall recovery.
- P8-04 retention/deletion/restored-backup handling.
- P8-05 packet/concurrency load tests.
- P8-06 incident/support/training.
- P8-07 contracts/vendors/sign-off.

Dependencies: Phase 7/all real-data prerequisites. Acceptance: no critical/high defects; thresholds/recovery met; owners sign off. Tests: regression/adversarial/config/recovery. Security: independent boundary review. Done: explicit customer-data pilot authorization.

### Phase 9 — pilot and commercialization

- P9-01 onboard/measure matched effort.
- P9-02 categorize corrections/false alerts.
- P9-03 highest-impact improvements/regression.
- P9-04 price/onboarding/support economics.
- P9-05 validated billing/admin.
- P9-06 external security/launch operations.
- P9-07 choose next segment/integration.

Dependencies: Phase 8. Acceptance: repeated use/savings/acceptable burden/unit economics. Tests: real failure classes/billing/migrations. Security: access/vendor/vulnerability reviews. Done: documented commercial go/no-go.

Critical path: domain → tenancy/auth → safe ingestion → evidence → extraction → rules → human review → frozen packages → quality/security → pilot. Marketing/nonessential polish must not delay it.

## 18. Backlog, execution order, and business measurement

P0 order: scope/rights → tenancy/auth → safe documents/jobs → extraction/evidence → rules/requirements → decisions/revisions → packages/approval → real-data gate. P1: commercial onboarding/billing/admin, then validated connector. P2: additional lines/portals/quotes under separate plans.

Execution: approve → Phase 1 demonstration → accounts → safe evidence → evaluated extraction → rules → canonical review → exact packages → pilot gates → measured pilot → commercialization decision.

Measure active preparation time separately from waiting; accepted entries avoided; confirmed discrepancies versus false alerts; corrections/reviewed fields; total variable processing costs; matched time savings; gross margin including correctly classified support/delivery; onboarding hours/cost; active agencies/users and started/reviewed/approved/exported submissions; usage/revenue retention. No reward for raw alert volume.

Pricing hypothesis: agency subscription with included submissions, transparent overage and bounded processing. Subscription predicts revenue but heavy usage risks margins; per-submission aligns usage but discourages adoption/disputes; hybrid balances both. Define one billable submission as insured+CGL placement/period+agreed revision window, validate with buyers, never double-charge platform retries. Manual pilot invoices. Price against ROI/value/support, not AI cost alone.

## 19. Major risks

Poor scans → abstention/quality flags (AI); broad scope → narrow segments (product); exposure context → typed comparability (domain); duplicate loss valuations → separate identity/valuation (backend); unsupported narrative → approved facts/validation (AI); irrelevant citations → semantic evaluation (AI); tenant leaks → layered tests (security); stale approval → transactional frozen manifests (backend); queue/provider loss → outbox/checkpoints (operations); excess review burden → observed active time (product); licensing → rights/original templates (legal); premature connectors → export first (CTO); costs → quotas/metering; per-agency fragmentation → bounded profiles; deletion mismatch → category policy/exercises; insufficient domain expertise → named reviewer.

Proposed limited-pilot recovery targets: RPO ≤24h, RTO ≤8h, business-hours named support. Reassess and demonstrate tighter capabilities before selling broader SLAs.

## 20. Assumptions and open decisions

Validate supported segments/forms; agency-wide versus restricted access; preparer/approver overlap; materiality; loss periods/freshness; permitted omissions; packet limits; identity/OCR/AI vendors; region; retention/legal holds; ACORD/class rights; pricing/billable definition; support; accessibility; claimant minimization; exhibit distribution; broker phone/email attestations; revoked source access.

Proposed limits: 30 files, 50 MB/file, 500 PDF pages/submission, separate bounded worksheet limits. Validate by load/cost tests. Managed OIDC; Textract first candidate; one extraction provider selected by accuracy/support/terms/cost; single approved US region; no universal retention; no ACORD reproduction; preserve supplied class codes only; no FX/language promises. Decide before affected implementation rather than hiding assumptions in UI.

## 21. Pilot readiness

Product: bounded scope/types; complete integration-free flow; coverage contexts; explicit conflicts; usable review/exceptions; exact approved exports.

Quality: held-out metrics/denominators/limitations; critical cases pass; no unsupported material release-corpus claims; domain sign-off and onboarding limitations.

Security/legal: real-data controls/contracts/vendors/rights; isolation review; no critical/high findings; restore/deletion; incident owners.

Operations: named pilot/escalation owners; monitoring; limits/costs; recovery; offboarding; training/feedback.

Measurement: baseline, active-time method, correction/false-alert reasons, submission costs, agreed success.

Paid pilot adds pricing/invoices/support/liability/accepted boundaries. Product/engineering/security/domain owners record gate approval; tests alone are insufficient.

## 22. Setup after approval

Save approved plan; record decisions/blockers; establish repository ownership; initialize minimal monorepo; pin compatible runtimes/lockfiles; local containers; placeholder env; synthetic fixtures; migrations; nonprivileged runtime/separate migration roles; immediate isolation tests; formatting/lint/type/tests/security CI; generated API types; health/readiness/logs; reproducible startup/test/migration docs; approved infrastructure only; production cannot enable development auth; demonstrate foundation.

External accounts are not assumed: source control, hosting/region, identity, storage/encryption, DB/queue, OCR/AI, secrets, monitoring. Never invent credentials or silently provision billable resources.

## 23. First coding milestone and follow-up

P1-01 delivers monorepo, minimal Next shell, FastAPI, health/readiness, environment validation, Docker local execution, initial CI, README, relevant tests. Fresh checkout starts; web reaches API; missing config fails clearly; checks pass; no secrets/customer data. Report files/checks/limits. P1-02 is next, ahead of AI.

First ten tasks: P1-01, P1-02, P1-03, P1-04, P1-05, P1-06, P1-07, P2-01, P2-02, P2-03.

Recommended next prompt recorded in the approval conversation: implement P1-01 only, using the approved stack; inspect instructions; authorize files/Git/dependencies; pin versions; synthetic data only; no external accounts/cloud/public deploy/real uploads/paid AI; report verification and stop at the milestone.

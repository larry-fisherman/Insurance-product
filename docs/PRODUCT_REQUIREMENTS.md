# Product requirements

Updated: October 30, 2026. Product direction: insurance document intelligence and workflow platform, with Commercial General Liability (CGL) as the first supported line.

This document defines product scope and acceptance requirements. [Architecture](ARCHITECTURE.md), [data model](DATA_MODEL.md), [security](SECURITY.md), and [roadmap](ROADMAP.md) describe the corresponding design and delivery gates. [plan.md](../plan.md) remains the original detailed CGL planning baseline; these documents clarify the platform boundary without waiving its acceptance requirements. Implementation status and known gaps are recorded in [review remediation](review-remediation.md) and [pilot readiness](pilot-readiness.md).

## Initial product promise

**Upload the documents. Identify missing or conflicting underwriting information. Review the facts. Generate a submission-ready package.**

These five capabilities are the initial CGL MVP scope and take priority over expansion to additional lines:

| Capability                                     | Required user outcome                                                                                                                                                                                                                                |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CGL document extraction and classification     | Upload policies, applications, exposure schedules, and loss runs; see document classifications, processing outcomes, extracted candidates, and source citations. Correct a classification or identify unsupported content without losing the source. |
| Exposure and policy information reconciliation | Compare compatible exposure and policy facts across documents, with basis, period, currency, scope, and existing/requested context preserved. Show conflicting values and their evidence without silently choosing one.                              |
| Missing-information and discrepancy detection  | See actionable missing fields, incomplete/unreadable sources, material conflicts, and applicable submission requirements. Mandatory gaps block generation; permitted accepted exceptions remain disclosed.                                           |
| Human review and approval workflows            | Inspect source evidence, verify/correct facts with attributable support, resolve conflicts, and obtain authorized approval of the exact package. Extraction alone never approves a fact.                                                             |
| Standardized submission generation and export  | Generate consistent original PDF/XLSX summaries and a ZIP of supporting documents, reviewed snapshot, citations, manifest, and limitations. Preview, approve, and export the same bytes.                                                             |

For this product, “submission-ready” means ready for the broker's manual submission after required review, authorized exception handling, and final approval. It does not establish carrier acceptance, replace carrier-required applications, or remove disclosed limitations. Generation creates an unapproved candidate; export requires approval. The current build demonstrates this workflow on supported fictional documents; representative CGL acceptance and real-data release gates remain open.

The executable checks and remaining limitations for this promise are mapped in [review guide](review-guide.md#initial-product-promise-acceptance).

## Product purpose and users

Help commercial insurance teams turn heterogeneous documents into traceable, reconciled, human-reviewed information and submission packages. The reusable product is the document, evidence, exception, review, and approval workflow. CGL supplies the initial insurance vocabulary, reconciliation rules, completeness requirements, and output content.

Initial customers are independent agencies and commercial P&C brokerages. Preparers collect documents and resolve findings; authorized approvers accept permitted exceptions and approve packages; viewers consume permitted records; tenant administrators manage membership and roles. Software role authority does not establish professional licensing or customer authorization.

Success means less active preparation work with reliable evidence and visible uncertainty. Measure matched manual versus assisted preparation time, correction effort, unsupported package assertions, completeness, processing latency, and cost. Do not infer savings or insurance accuracy from generated regression cases.

## Shared platform and CGL boundary

| Shared platform component                                           | CGL-specific component                                                                     |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Document ingestion, quarantine, parsing, OCR, immutable versions    | Recognition of CGL policies, applications, exposure schedules, endorsements, and loss runs |
| AI extraction infrastructure, provider interfaces, typed validation | GL coverage fields and CGL extraction schemas/prompts                                      |
| Source citation system, provenance, evidence navigation             | GL exposure classifications and their supplied system/edition/context                      |
| Comparison and derivation execution infrastructure                  | Receipts/payroll/subcontractor reconciliation and CGL exposure compatibility rules         |
| User authentication, tenant authorization, audit                    | GL-specific loss history checks and claim/report-period semantics                          |
| Exception management and follow-up tasks                            | GL submission requirements, materiality, applicability, and waiver policy                  |
| Review, revision history, package approval                          | GL forms and exhibits, including licensed templates only when authorized                   |
| Submission export, manifests, immutable artifacts                   | GL underwriting summaries and CGL package layouts                                          |

Shared components must not embed CGL limit names, assume a sales exposure basis, require GL loss-history periods for every line, or render every package as a GL summary. Domain modules may reuse shared mechanics while retaining their own insurance semantics. Other lines are future scope, not supported merely because this boundary is documented.

## Initial scope and implementation status

The repository is a local, synthetic-only CGL review build with open functional requirements. It includes a Next.js workspace, FastAPI API, PostgreSQL, workers, PDF/XLSX intake, evidence-backed observations, review decisions, original PDF/XLSX/ZIP packages, approval, and manual export. CGL behavior is still coupled into workflow, extraction, and rendering; a general line-module interface is a target design.

Pilot assumptions remain US commercial insureds, English documents, agency-wide tenant access, and one line per submission. Preserve supplied currencies and distinct exposure bases without automatic conversion. Recognizing claims-made language does not establish full claims-made workflow support. Managed identity, production deployment, representative insurance evaluation, and customer-data authorization remain open.

The default extractor is a bounded explicit-label parser. Optional provider adapters do not establish general AI interpretation capability or validated model accuracy. Unrecognized content must remain visible as unsupported or incomplete.

## Required workflow

1. Select/create an insured account, submission line, intended period, owner, and requirements profile. A renewal links its predecessor and copies prior facts only as unverified candidates.
2. Upload immutable document versions. Validate type, size, archive expansion, and parser bounds; quarantine before trusted processing. Preserve replacement lineage and all intake outcomes.
3. Scan, classify/segment, parse/OCR, extract candidates, and validate values and citations. Partial failures preserve usable sources and expose unprocessed content.
4. Run versioned domain comparisons and completeness checks. Explain conflicts, noncomparability, missing information, and source limitations.
5. Let preparers inspect originals and exact source locations, select supported observations, correct with validated evidence or attributed attestation, reject unsupported extraction, or propose omission. Track questions as assigned tasks.
6. Require authorized, reasoned acceptance for permitted exceptions. Freeze reviewed facts, provenance, decisions, requirements, and disclosed limitations into an immutable revision.
7. Generate a candidate package from that revision and the frozen source inventory. Preview the actual output files and manifest.
8. Check current approval authority, source/input freshness, and integrity. Approve the exact manifest, revision, and artifact bytes.
9. Export those approved bytes and record the export. External distribution remains manual; export does not imply carrier receipt or underwriting acceptance.

Target business states remain `draft → collecting → in_review → ready_for_package → awaiting_approval → approved → exported → closed`, with cancellation supported. Processing states are separate from business states. A failed processing job must not imply that a submission is complete.

## Functional acceptance requirements

| ID    | Requirement and acceptance evidence                                                                                                                                                                                        |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PR-01 | Every business request, worker, source download, package, and export enforces tenant and current role scope; cross-tenant identifiers fail closed.                                                                         |
| PR-02 | Every source preserves immutable bytes/version/hash and a resolvable locator. Excluded, replaced, failed, and unreadable sources remain in completeness accounting.                                                        |
| PR-03 | Extraction produces candidates with typed context, availability, support status, provenance, and version metadata. A valid JSON response or high confidence alone cannot create a reviewed fact.                           |
| PR-04 | Comparisons require compatible entity, policy, period, role, currency, unit, basis, and scope. Material disagreements require an attributable decision. Noncomparable values must not be silently merged.                  |
| PR-05 | Missing/unknown/unreadable/not-applicable information remains distinct from zero, false, and explicit absence. Required omissions block readiness; permitted exceptions require authority, reason, and package disclosure. |
| PR-06 | Corrections distinguish fresh supporting evidence from broker attestation and retain original history. New evidence invalidates affected decisions and approval eligibility without rewriting history.                     |
| PR-07 | The package includes reviewed information, source references, exclusions, and accepted limitations. Approval binds exact artifact hashes; changed inputs require a fresh package and approval.                             |
| PR-08 | Long-running work exposes status and bounded retries; duplicate delivery or uncertain client retries cannot create duplicate logical results. Failed packets remain in quality/usage denominators.                         |
| PR-09 | A future line can supply its own schema, requirements, rules, and templates while using the same ingestion, citations, tenancy, review, and approval mechanics. No second line is required for the initial release.        |

## CGL domain acceptance

- Coverage: distinguish existing, requested, and historical coverage; preserve trigger, limits, aggregates, deductibles/SIR, policy dates, supplied forms, and endorsements. A certificate, declaration, title, or absent endorsement cannot establish full policy terms.
- Exposures: preserve receipts, sales, payroll, subcontract cost, and other supplied bases with exact quantities, units/currency, period, actual/estimate role, classification namespace, entity/location, and scope. Never default every exposure to revenue or aggregate overlapping/incompatible components.
- Classification: retain supplied code/description/system and context. Do not invent, translate between proprietary systems, or certify authoritative rating classifications.
- Loss history: distinguish claim identity from valuations, reported amounts from calculations, report coverage from claim absence, and gaps from explicit no-loss statements. Never sum successive valuations or assume a universal required lookback period.
- Requirements: use a versioned, domain-approved profile for the supported segment and period. Missing mandatory identity/period/exposure, unsafe sources, invalid evidence, stale approval, or conflicting asserted facts cannot be waived into verified facts.
- Outputs: provide original CGL summaries and schedules with cited facts and limitations. Generated underwriting prose is presently deterministic from reviewed facts; coverage interpretation and autonomous underwriting are outside scope.

The complete contextual model and domain-approved rules remain open work; see [roadmap](ROADMAP.md). These requirements must not be represented as fully delivered by the current field registry.

## Non-goals and release constraints

No autonomous coverage decisions, rating, binding/issuance/cancellation, carrier transmission, AMS replacement, or unlicensed form reproduction. Email ingestion/sending, client portals, quote comparison, multi-line packages, payment processing, and additional lines require separately scoped work. The approved [web platform plan](WEB_PLATFORM_PLAN.md) adds separate marketing/operations experiences, managed identity integration, agency onboarding, and manual subscription administration; commercial pricing and payment acceptance remain open.

Customer documents, paid pilots, and production use require the gates in [SECURITY.md](SECURITY.md) and [pilot readiness](pilot-readiness.md). This documentation does not authorize deployment, real data, external communications, paid provider calls, or removal of synthetic-only enforcement.

## Evaluation

Retain the proposed targets in [plan.md, section 15](../plan.md#15-testing-and-evaluation) as hypotheses requiring domain approval: material-field precision ≥98%, exact contextual money/exposure accuracy ≥99%, readable-field recall ≥90%, material-discrepancy recall ≥95% and precision ≥85%, reference validity 100%, semantic support ≥99%, and zero observed unsupported material package assertions in the release corpus. Every exported material fact still requires review.

Report denominators, abstentions, failed packets, document mix, uncertainty, and correction burden. The proposed 40% median labor saving, ten-minute P95 reference-packet processing, and $3 variable-cost targets are unvalidated hypotheses. Independent held-out documents and licensed reviewer adjudication are required before quality or commercial claims.

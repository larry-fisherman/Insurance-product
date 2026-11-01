# Roadmap

Updated: November 1, 2026. Deliver an insurance document intelligence and workflow platform through a CGL-first implementation. Reuse shared ingestion/evidence/review infrastructure; validate each line's insurance semantics separately.

This is a delivery sequence with acceptance gates, not a claim of completed phases or a commitment to dates. [plan.md](../plan.md) retains the original detailed tickets and quality hypotheses. [review remediation](review-remediation.md) records current fixes and open requirements; [roadmap status](roadmap-status.md) maps earlier phases to the review build. Existing CGL acceptance requirements remain binding.

## Current baseline

Implemented for local synthetic review: tenant/accounts/submissions, local password/TOTP roles, private document intake and bounded OCR/parsing, observations and citations, deterministic CGL findings, human review and attestations, exceptions/tasks, immutable revisions, original packages, approval-bound export, outbox/jobs, and local verification/recovery tooling.

Not established: complete functional MVP acceptance, representative insurance accuracy, live managed-identity acceptance, independently assessed parser isolation, production security/operations, actual provider costs, customer authorization, paid pilot, payment processing, or support for another insurance line. The general domain-module contract is proposed, while CGL dependencies remain in shared code paths.

## Delivery sequence

| Stage                                  | Shared platform work                                                                                                                                                                                  | CGL-specific work                                                                                                          | Exit evidence                                                                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 1. Context and boundaries              | Adopt these docs and root agent instructions; identify shared services and version bindings                                                                                                           | Keep CGL requirements and limitations explicit                                                                             | Future work can identify its owner; current behavior and target architecture are distinguished                                 |
| 2. Complete review workflow            | Resumable upload/finalize/recovery; segment overrides; durable stage/page results; lease/heartbeat/load recovery; reliable PDF geometry; remaining typed routes/navigation; actual usage/cost budgets | Complete contextual entities/operations/exposures, aggregate/retention/forms and loss semantics; broker-accepted schedules | Open functional findings have implemented acceptance evidence and reviewed domain decisions                                    |
| 3. Establish module seam               | Explicit server-controlled line registry/contract; version binding across extraction/review/revision/package; domain-driven UI metadata; preserve tenant/approval behavior                            | Move CGL schemas, compatibility/requirements, extraction instructions and templates behind the contract as touched         | CGL acceptance remains intact; unknown lines fail closed; adding a future module does not require changing shared safety logic |
| 4. Validate security and quality       | Separate parser permissions/egress; managed identity/onboarding; retention/holds/deletion; dependency/release controls; independent assessment; production storage/recovery tests                     | Approved requirement profiles/materiality and independent held-out insurance corpus with adjudication                      | Recorded customer-document gates and explicit authorization; synthetic-only gate remains until then                            |
| 5. Limited real-data pilot             | Controlled onboarding, monitored processing, support/training, measured active work/cost/latency                                                                                                      | Supported segments, packet formats and template content signed off by brokers                                              | Authorized limited pilot demonstrates usable quality, correction burden and recovery                                           |
| 6. Paid pilot and commercial readiness | Approved pricing/terms, metering/invoicing, incident/support commitments, production identity and operations                                                                                          | Demonstrated CGL value and quality at agreed workload                                                                      | Paid-pilot gates, then separate general-launch gates, pass with recorded evidence                                              |
| 7. Add another line                    | Reuse tenancy/intake/evidence/review/export; extend contract only for demonstrated needs                                                                                                              | Implement and evaluate a separate line schema/rules/profile/templates                                                      | Independent line-domain acceptance and no regression in shared/CGL controls                                                    |

Stages overlap where independent work is possible. Parser isolation, identity, and domain validation need not wait for a full module refactor. Do not use platform abstraction as a reason to defer existing CGL functional requirements, or complete every future-line abstraction before validating CGL.

## Next engineering priorities

The first release must deliver the [initial product promise](PRODUCT_REQUIREMENTS.md#initial-product-promise): upload → detect missing/conflicting underwriting information → review facts → generate, approve, and export a submission-ready package. CGL extraction/classification, exposure/policy reconciliation, missing-information/discrepancy detection, human review/approval, and standardized output/export are required capabilities. Validate this complete path using the [acceptance checklist](review-guide.md#initial-product-promise-acceptance) before treating the functional MVP as complete. Additional lines and architectural expansion must not displace unfinished work on this path.

1. Resolve domain model decisions with experienced reviewers: entity/operation relationships, exposure gross/net/components/denominators, aggregate/retention applicability, form editions, claims-made limitations, claim component/recovery semantics, and comparable versus noncomparable outcomes.
2. Complete intake/evidence behavior: resumable transfer, broader classification/segmentation and broker overrides, durable partial results, reliable geometry and source fidelity on representative PDFs/workbooks.
3. Strengthen processing boundaries and operations: minimal-access parser execution, crash/stall/load tests, measured provider token/cost records and budgets, managed identity, and approved deletion/holds/restore workflows.
4. Extract the small line contract while preserving exact review, exception, provenance, version, and approval invariants. Test supported CGL behavior and unsupported-line rejection; do not build a second production line to prove the interface.
5. Freeze an independently labeled held-out evaluation and report failures, abstentions, semantic support, unsupported package assertions, numeric context, correction effort, latency, and real cost.

Use [review remediation](review-remediation.md#remaining-scope-and-release-blockers) for the detailed remaining scope. None of these items becomes complete solely by being listed here.

## Gate definitions

- Functional MVP: complete synthetic upload-to-approved-export acceptance, including partial failures, exclusions, supported corrections, current approval, and the outstanding domain/workflow requirements. Test counts alone do not establish it.
- Customer documents: independent security/domain evaluation, approved identity/infrastructure/vendors/rights, customer authority, retention/recovery/incidents, and explicit release authorization.
- Paid pilot: all customer-document gates plus signed scope/terms, measured quality/cost/capacity, metering/invoicing, onboarding/support and a consistent labor baseline.
- General launch: repeatable commercial onboarding, production identity/operations, independent assessment, measured recovery, approved contracts/claims and sustained supported-segment quality.

The detailed checklist remains [pilot-readiness.md](pilot-readiness.md). Do not convert external legal, vendor, customer, or infrastructure work into a completed engineering checkbox.

## Expansion policy

Potential future lines include commercial property, workers' compensation, commercial auto, umbrella/excess, professional liability, and cyber. They are examples, not a committed order or supported capabilities. Each needs its own typed facts, comparison semantics, requirements, loss/exposure concepts, templates/rights, and adjudicated corpus. Common document storage does not imply common coverage interpretation.

AMS/email/carrier integrations, client portals, e-signature, quote comparison, multi-line packages, automated distribution, and licensed forms require separately approved scope, access, commercial rights, and customer consent. Build core workflows without assuming those integrations exist. Avoid premature microservices, universal insurance schemas, generic rule builders, or autonomous underwriting.

## Evidence and documentation maintenance

For each material change, record affected requirements, shared/domain ownership, migrations and version changes, relevant verification, remaining limitations, and whether a release gate changed. Keep these five docs aligned with implementation and ADRs; keep historical verification reports dated. Existing generated regression success must not be relabeled as independent held-out accuracy or customer savings.

## Approved web-platform implementation

The local implementation now includes independent marketing/operations builds, isolated public intake, an OIDC adapter and controlled activation, onboarding/team management, billing-admin/manual entitlements, resumable intake, session context binding and a credential-free parser container. [Implementation status](web-platform-operations.md) distinguishes these implemented pieces from live provider/payment, retention, hosting and CGL acceptance still required. Existing delivery gates are unchanged.

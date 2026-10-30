# Release gates and evidence

The implemented milestone is a **local synthetic review build with outstanding functional and release requirements**. See [review remediation](review-remediation.md) for the defect fixes and the engineering work that remains open. The application enforces local/test environments and synthetic mode. Do not remove that gate simply to deploy.

## Before customer documents

- [ ] Licensed commercial-lines reviewers approve the CGL model, exposure contexts, loss semantics, materiality, requirements profiles, exceptions, and wording limitations.
- [ ] Customer discovery validates the target agency workflow and a measurable baseline; customer authority to upload/share all supplied documents is documented.
- [ ] Qualified counsel reviews producer-licensing boundaries, federal/state privacy/data-security requirements, insurance information sharing, agency contracts, E&O, indemnities, and any third-party template rights. No ACORD rights are assumed.
- [ ] Approved cloud region, production identity/MFA, TLS, KMS-backed storage/database encryption, private networking, secret rotation, and least-privilege IAM are provisioned and independently tested.
- [ ] Vendor/DPA/subprocessor review covers OCR/AI/storage/identity logs, residency, retention, training use, incidents, and deletion. Live model access and costs are verified with approved synthetic data first.
- [ ] Representative properly authorized or fictional documents include native/scanned/mixed PDFs, complex schedules, multiple locations/entities, loss-run overlap, duplicate valuations, formula caches, endorsements, and adverse/injection examples.
- [ ] Independent security review tests tenant isolation across every endpoint/object, identity/session lifecycle, file parsers, document access, and the operational environment.
- [ ] Retention, legal holds, tenant deletion, backup expiry, incident response, access reviews, and customer data-return procedures are approved and exercised.

Software evidence already present: forced RLS/non-owner pools; role checks; authenticator enrollment/recovery/replay controls; sessions/CSRF/origin checks; immutable evidence/revisions/approvals; bounded uploads; quarantine/ClamAV; OCR/parser bounds; source validation; approval hash/freshness checks; real PostgreSQL and browser tests. These are features, not legal conclusions or certification.

## Before paid pilots

All customer-document gates, plus:

- [ ] Signed pilot scope, support escalation, service boundaries, incident contacts, data-use agreement, and approved document/template rights.
- [ ] Approved quality thresholds and an independent held-out corpus; broker adjudication of critical numeric, source, conflict, and unsupported-narrative failures.
- [ ] Live provider validation if AI is marketed; default local-parser limitations are disclosed if no AI account is configured.
- [ ] Tenant onboarding/offboarding, invitations, MFA recovery assistance, monitoring, alerts, failure recovery, and end-to-end object restore are exercised.
- [ ] Expected workload, queue capacity, per-submission page/token limits, processing latency, and vendor/infrastructure cost are measured.
- [ ] Broker-reported preparation time is compared against a consistent manual baseline and measured active review effort; customer savings are not invented from page counts.
- [ ] Subscription/per-submission/hybrid pricing is validated with brokers. Pilot billing can remain manual; no payment processor is assumed.

## Before broader commercial launch

All pilot gates, plus:

- [ ] Hardened managed identity/enterprise MFA, verified email ownership, and documented identity assurance; demo routes unavailable in production.
- [ ] Production service-principal semantics, multi-office permissions, access recertification, audit export/search, customer retention/deletion, and operational administration are implemented to the agreed launch scope.
- [ ] Production restore drill verifies every document/artifact version and approval hash in an isolated environment; approved RPO/RTO/SLO and alert response are measured.
- [ ] Threat model, penetration test, dependency/container scans, SBOM, CI release provenance, deployment rollback, and security-change review pass.
- [ ] SOC 2 readiness work establishes organizational controls, evidence collection, and auditor scope. A software checkbox does not confer SOC 2 status.
- [ ] Contracts, privacy notices, service descriptions, claims about AI/accuracy/savings, and E&O coverage are professionally approved.

## Evaluation definitions

Use field precision/recall on independently labeled material facts; exact normalized numeric match with correct currency/basis/unit/period; material-discrepancy recall/precision with broker adjudication; valid document/version/location references and source-span support; unsupported statements per generated package; corrected approved fields / reviewed fields; p50/p95/p99 wall-clock latency by document type/page count; and total vendor + infrastructure cost per completed submission.

Do not count missing values as correct zeros, document references as proof of semantic support, or model confidence as correctness. Generated exact-label regression cases are useful smoke coverage and are not representative held-out insurance documents. Measure human correction, unsupported narrative rate, onboarding cost, monthly active agencies/submissions, retention, and gross margin from actual pilot activity. Treat all pricing and market assumptions in plan.md as unvalidated hypotheses.

## Deferred integrations and expansion

AMS (Applied Epic, AMS360, EZLynx), Outlook/Microsoft 365, carrier/MGA APIs, e-signature, ACORD-aligned standards, quote comparison, client portals, other insurance lines, and carrier distribution remain roadmap work. Confirm commercial access, licenses, customer permission, API terms, and partner agreements before implementation. Import/export works without any such access. No unavailable integration is presented as connected.

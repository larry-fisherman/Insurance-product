# Review against plan.md

Review date: October 29, 2026. Reviewed commit: `78dc7ea`.

**Assessment: partially aligned.** The repository implements a substantial local synthetic CGL review workflow, including human decisions, immutable revisions, tenant scoping, package generation, and approval of artifact hashes. It does **not yet satisfy the complete functional MVP or pilot acceptance criteria in `plan.md`**. There are correctness defects in evidence handling and extraction, as well as unfinished engineering requirements.

The narrower scope is acknowledged in `docs/decisions/0003-review-mvp.md` and `docs/implementation.md`. Those explanations are useful, but they do not make the omitted plan requirements complete. In particular, the description “complete local synthetic review MVP” in `docs/pilot-readiness.md` should be qualified until the functional gaps below are addressed.

This review changes only this file. Findings distinguish observed behavior, static code findings, and release work that requires external validation. “High” means address before treating the affected workflow as complete; “Medium” means a material functional or reliability gap. These severities are separate from the plan's P0/P1/P2 labels.

## Findings requiring changes

### R01 — High: excluding a document removes its problems from completeness and export disclosure

**Evidence:** `services/backend/app/api.py:540–552`; `services/backend/app/workflow.py:87–108,208–231`; `services/backend/app/api.py:715–718`; `services/backend/app/rendering.py:107–119`.

A user with `documents.write` can exclude a document with a reason. Both the observation query and readiness inventory then filter it out. Package generation also includes only active documents. The excluded document remains visible in the workspace, but its exclusion, failed processing, and lost observations are not automatically represented in the canonical exceptions or exported inventory.

For example, excluding an additional unreadable loss-run document or an adverse endorsement can remove its blocker or extracted facts while other documents still satisfy the broad field requirements. The package can omit this limitation entirely. Exclusion also does not refresh persisted conflict rows immediately, so dashboard findings can disagree with readiness.

**Plan:** §3 requires failed/excluded documents to remain visible in completeness checks; §3 materiality and F12/F13 require authorized acceptance and final disclosure of permitted omissions.

**Required change:** Separate inclusion in exhibits from inclusion in completeness accounting. Preserve exclusion disposition, actor, reason, affected requirements, and replacement lineage in the frozen revision. Require an authorized disposition where material information is lost and disclose applicable exclusions in every package format. Test exclusion of failed, conflicting, and adverse documents, including legitimate replacement.

### R02 — High: material omissions bypass the exception permission

**Evidence:** `services/backend/app/workflow.py:554–599,241–248`; compare `services/backend/app/api.py:643–652`.

`review()` checks only `reviews.write` for `omit` and `reject`. Readiness immediately treats these decisions as resolved groups and disclosed exceptions. It does not require the separate `exceptions.accept` permission or approval-authority check used by the exception endpoint.

An in-memory reproduction of omitting a material endorsement checked only `reviews.write`. Thus a preparer can settle a material omission before generation without the intended exception authorization. Final package approval is still required, but the explicit material-exception control has already been bypassed.

**Plan:** F03, F13, P6-04, and §3 materiality distinguish preparation from authority to accept material exceptions.

**Required change:** Distinguish rejecting a demonstrably incorrect extraction from omitting a supported material assertion. Enforce the configured exception authority for the latter, with a pending disposition until accepted. Keep mandatory identity/period and other nonwaivable blockers nonwaivable.

### R03 — High: local extraction assigns the last context in a document to earlier facts

**Evidence:** `services/backend/app/documents.py:371–446`; `services/backend/app/worker.py:179–183`.

The first pass collects one document-wide policy number, currency, exposure period/status, classification namespace, and loss context. Later occurrences overwrite earlier ones. The second pass applies that final context to all matching facts. Classification is also document-wide; there are no segment boundaries to prevent this.

**Reproduced:** A document containing policy `OLD-2026` with a 1,000,000 limit and policy `NEW-2027` with a 2,000,000 limit assigned both limits to `NEW-2027`. A 2026 actual payroll of 100,000 and a 2027 estimated payroll of 200,000 both became 2027 estimates. These produce false same-context conflicts and lose the original meaning before review.

**Plan:** invariants 2 and 9; F08/F09/F11; §7 contextual exposures/coverage; P3-06 and P4-03.

**Required change:** Extract within explicit page/section/table/row contexts, retaining the citations for each context. When context cannot be associated reliably, abstain with a visible issue. Add multi-policy, multi-period, multi-currency, and mixed-document regression cases.

### R04 — High: provider evidence validation accepts the right number under the wrong insurance meaning

**Evidence:** `services/backend/app/documents.py:595–639`.

Validation verifies citation IDs and text/numeric presence, plus four context fields. It does not validate that the cited text supports the field, role, policy reference, basis, unit, or scope. Claim amounts similarly need only appear somewhere in the combined cited text, so component associations are not established.

**Reproduced with a mocked provider, without a network request:** Source `General aggregate limit: USD 2000000` was accepted as `coverage.each_occurrence`, with role `requested` and policy reference `INVENTED-POLICY`.

Observations remain unreviewed, which is a useful safeguard, but this does not fulfill the specified server-side evidence validation. The review/export path has no later semantic-support check that repairs this automatically.

**Plan:** §12 explicitly says matching a number alone is insufficient; F09/F10 and P4-03 require contextual evidence support.

**Required change:** Validate field/context associations against cited spans or structured cells, including component labels for claims. Preserve ambiguity as an explicit unsupported/needs-review state rather than ordinary source-supported extraction. Add negative tests for swapped limit types, paid/reserve swaps, invented policy IDs, and existing/requested confusion.

### R05 — High: corrected values inherit document provenance even when the document supports the old value

**Evidence:** `services/backend/app/workflow.py:179–205,570–609`; `apps/web/src/components/workspace/review.tsx:185–219`.

The UI sends the selected observation ID when correcting a value. `approved_facts()` copies that observation's citations and labels the corrected fact `evidence_type="document"`, regardless of whether the corrected value or context is supported there. No explicit choice of new evidence versus broker attestation is required.

**Reproduced:** Correcting a source value of 1,000,000 to 2,000,000 while retaining the observation ID produces a 2,000,000 canonical fact labeled as document evidence. The reviewer and reason are retained, but the provenance misstates what supports the new assertion.

**Plan:** invariant 4; F14; P6-03; §10 canonical provenance.

**Required change:** Keep original citations as correction history, and separately record the support for the corrected fact. Require valid supporting evidence or an explicit attributed attestation with its origin and reason. Render that distinction in PDF/XLSX/ZIP and test corrected values and corrected contexts.

### R06 — High: extraction omissions are silently discarded and completeness is too coarse

**Evidence:** `services/backend/app/documents.py:415–417,474–479`; `services/backend/app/domain/models.py:143–167`; `services/backend/app/workflow.py:225–231,279–326`; `services/backend/app/domain/cgl.py:124–132`.

The local extractor drops `unknown`, `n/a`, `not supplied`, and invalid facts. Observations have no separate availability state. A known document gets a manual-document-review issue only when it yielded no facts at all. Once any coverage field is verified, the generic missing-coverage issue disappears; there is no requirement inventory for unreadable or omitted material fields and referenced endorsements. An exposure with basis `unknown` also passes validation without a specific readiness issue for that basis.

Consequently, successful extraction of a name or one limit can coexist with silently lost material content. The system cannot reliably distinguish “not applicable,” “explicitly unknown,” “unreadable,” and “not supplied” as the plan requires.

**Plan:** invariant 3; F09/F12; §7 shared material-field representation; P5-06.

**Required change:** Persist availability and parse/extraction issues independently of values. Apply versioned requirement profiles and retain a field/segment coverage inventory. Surface unknown units/bases, incomplete policy content, and referenced-but-missing endorsements even when other extraction succeeds. A permitted exception must disclose the actual omission.

### R07 — Medium: legitimate multiple DBAs and locations collapse into conflicting scalar groups

**Evidence:** `services/backend/app/domain/cgl.py:163–181,194–213`; `services/backend/app/documents.py:418–420`; `services/backend/app/workflow.py:249–259`.

`group_key()` initially adds the DBA value as an item key, then overwrites it in the general `insured.*` branch. Two DBAs in the same scope therefore have the same key. Local extraction also gives every `location.address` the default `all operations` context, so multiple addresses become competing values instead of distinct location rows.

**Reproduced:** `group_key("insured.dba", {}, "First DBA")` equals the key for `"Second DBA"`. Reconciliation treats distinct values as a material conflict, and the normal review path selects one value for the group.

**Plan:** §7 DBAs, locations, and operation relationships; F11; P4-01/P5-02.

**Required change:** Model stable row identities for repeated entities and locations, and fix DBA grouping. Distinguish two observations about the same entity from two legitimate entities. Test retaining multiple DBAs/locations in one approved package.

### R08 — Medium: foreign monetary exposures are labeled with contradictory units

**Evidence:** `services/backend/app/documents.py:347–355,422–429`; `services/backend/app/domain/cgl.py:124–132`.

Monetary exposure labels hardcode `unit="USD"`. An explicit foreign currency changes only `currency`, leaving the unit unchanged.

**Reproduced:** `Currency: CAD` plus a labeled payroll generated `unit="USD", currency="CAD"`. The validator accepts this combination and the renderer preserves it.

**Plan:** §2 requires foreign currency recognition without conversion; §7 requires exact amount/unit/currency and compatible comparison.

**Required change:** Represent monetary units consistently with the supplied currency, or use a neutral monetary unit with a separate currency field. Reject contradictory combinations and test CAD/EUR and ambiguous dollar symbols without introducing FX conversion.

### R09 — Medium: one blank or unreadable PDF page discards all usable pages

**Evidence:** `services/backend/app/documents.py:208–252`; `services/backend/app/worker.py:175–196,330–355`.

`parse_pdf()` raises `UNREADABLE_PAGE` whenever a page has no text after OCR. Source blocks are persisted only after the entire parse and extraction succeed. A blank separator or one damaged page therefore fails the whole document and loses all partial reviewable results.

**Reproduced:** A blank PDF page with OCR returning an empty string raises `UNREADABLE_PAGE`. The all-or-nothing persistence consequence follows from the worker code.

**Plan:** §3 requires partial successes to remain reviewable; F07/F19 and P3-03/P3-04/P3-07 require page inventory and recoverable processing.

**Required change:** Record page-level outcomes, distinguish blank from unreadable content, retain usable evidence, and surface unresolved pages as completeness issues. Persist bounded stage results so a later failure does not discard prior safe work.

### R10 — Medium: retrying a form after an uncertain network result creates a new idempotency key

**Evidence:** `apps/web/src/lib/workspace-api.ts:172–193`; `apps/web/src/components/workspace/submission.tsx:50–70`; `services/backend/app/workflow.py:51–84`.

The API client generates a new UUID each time a write is attempted, and callers do not retain it across retries. If the server commits an upload/account/submission/measurement but the response is lost, the displayed advice to try again sends a new logical request. Backend idempotency cannot deduplicate it. Duplicate uploads can start another processing job and invalidate review again.

**Plan:** F06 interrupted upload recovery; §11 idempotency for retriable requests; F19 retry safety.

**Required change:** Retain one key and payload for each logical operation until its outcome is known. Reuse it on uncertain retries and create a new key only for a new operation. Test a committed request with a dropped response, including duplicate concurrent requests.

## Remaining scope and release gaps

These are additional requirements that are missing or incomplete, rather than claims that all documented deferrals are unexpected bugs.

| Area / plan requirement | Current evidence and remaining change |
| --- | --- |
| Parser isolation — F07, §8, §12 | `worker.py:161–183` parses within the application worker. `compose.full.yaml` gives that worker the shared application environment, database/auth credentials, object volume, and network access. Container memory/CPU limits and dropped capabilities help, but are not the planned separate parser permissions and egress boundary. Isolate parsing with minimal input/output access, no application secrets, and restricted networking; independently test hostile files before real-data use. |
| Managed identity and controlled onboarding — F01, P1-04 | `identity.py:300–400` implements local self-registration/password authentication; OIDC configuration and an OAuth state table are not a working OIDC flow. Implement approved managed identity, verified identity ownership, state/nonce/PKCE validation, and production onboarding/recovery. `config.py:57–67` correctly blocks staging/production and disabling synthetic mode; preserve these gates until the work is complete. |
| Renewal carry-forward — F05, P2-04 | `api.py:328–345` validates and stores the prior submission link but copies no facts into unverified candidates. The audit field `carry_forward_verified=False` does not implement carry-forward. Add linked prior-revision candidates, separate current-period confirmation, and a complete renewal test without inherited approval. |
| Submission lifecycle — §3, F05, P2-03 | `SubmissionEdit` has no status/transition operation. Status is assigned opportunistically by upload/review/package/export endpoints; `draft`, `ready_for_package`, `closed`, and `cancelled` lack the specified workflow. Implement permitted transitions and terminal-state behavior with audit and tests. |
| Full contextual CGL schema — §7, P4-01 | `domain/cgl.py:17–102` provides a limited scalar field registry/context and a small claim schema. Missing structured concepts include entity/operation/location row links, exposure gross/net components and rating denominator, coverage aggregate/retention applicability, form editions, claims-made retroactive/reporting details, and claim component definitions/recoveries. Implement the approved supported subset or explicitly narrow the product scope with domain approval. Recognition of a claims-made trigger alone is not claims-made data support. |
| Deterministic reconciliation — F11/F12, Phase 5 | `cgl.reconcile()` detects unequal values with exactly matching group keys; `workflow.readiness()` adds useful special checks. There is no general applicability/noncomparability result, compatible totals/derivations, year-over-year analysis, location reconciliation, or missing-referenced-endorsement rule. Add versioned, domain-approved rules with positive, negative, boundary, and nonapplicable cases. |
| Evidence viewer and spreadsheet fidelity — F07/F10 | `review.tsx:36–61` shows excerpts and downloads the original; it does not open/highlight the cited PDF page or spreadsheet cells. PDF locators lack geometry; `parse_xlsx()` flattens rows and retains limited sheet/formula warnings, not the specified cell formats, formulas, merged ranges, and hidden-row/column context. Preserve the required parse metadata and provide source navigation. |
| Classification and upload lifecycle — F06/F08, P3-01/P3-06 | Upload is one multipart request; there is no separate finalize/recovery protocol. `documents.classify()` inspects the first 20 blocks and assigns one type; no mixed-document segments or broker classification override exist. Add the planned recovery/version lifecycle and segment-aware classification. |
| Tasks and dashboard — F13/F21 | `api.py:148–155,906–940` allows task creation and status changes but no reassignment, due-date edit, resolution reason, or task row-version check. Dashboard counts only stored open discrepancies and caps submissions at 100; dynamic readiness blockers are not an actionable owner-filtered queue. Add those controls and aggregate over all scoped records. |
| Durable processing, limits, and usage — F19/F22, P3-07/P4-07 | Jobs have leases and an outbox, but the worker records one general processing stage and one initial heartbeat. There are no persisted stage checkpoints or attempt records; retries restart the pipeline. The 500-page check is per document, not per submission. Usage is recorded on success with `estimated_cost=None`; failed provider attempts, tokens, model/prompt details, and budgets are not accounted for. Add bounded recovery/backoff, aggregate limits, reproducible extraction-run metadata, and attempt-level cost records. |
| Retention/deletion/admin — F20, P8-04 | The settings UI explicitly says there is no automatic purge. No tenant deletion-request, legal-hold, category-retention, derivative/vendor erasure, or restored-backup deletion workflow is implemented. Storage `delete()` methods and backup tooling are insufficient by themselves. Implement and exercise these before customer data. |
| Package usability — F15/F17, P7-02/P7-07 | The PDF renders scalar paragraphs with JSON context; the XLSX has generic fact rows rather than contextual exposure/coverage/loss schedules. Preserve the existing hashes and evidence links while adding broker-reviewed readable tables and layout tests for long text, multiple locations, and multiple claims. |
| API contracts and editing UX — §9/§11/§14 | `workspace-api.ts` hand-maintains business interfaces; business routes primarily return `dict[str, Any]`, so generated OpenAPI types do not describe those responses precisely. Introduce typed response schemas and consume the generated contracts. Add dirty-form navigation protection; current review controls can be unmounted by changing tabs/routes without an unsaved-change guard. |
| Evaluation and release evidence — §15, P4-07/P5-07/P8-01 | `evaluate.py:15–104` repeats generated explicit-label cases. The spreadsheet side is constructed `Block` objects, not parsed workbooks. It does not measure contextual semantic support, unsupported package assertions, representative OCR/table quality, failed packets, or independent held-out performance. It is honestly labeled as regression coverage, but cannot satisfy the release targets. Add the planned split corpus, domain adjudication, critical-case tests, and frozen reports. |
| Production and commercial gates — Phases 0, 1, 8, 9 | Discovery, domain/template rights approval, vendor terms, approved production infrastructure, security assessment, demonstrated production recovery/deletion, training/support, pilot authorization, and measured savings remain outstanding. Existing local backup/restore tools are useful evidence, not completion of these gates. Security scanning/release provenance is also absent from the current CI workflow. Keep these items explicitly blocked; automated billing, AMS integration, carrier sending, and additional lines remain correctly out of MVP scope. |

## Alignment already present

- The application separates extracted observations, attributed review decisions, canonical revisions, packages, approvals, and exports. Extraction does not itself approve a fact.
- Runtime tenant scoping uses transaction-local database context, forced RLS, and tenant-aware foreign keys. Immutable evidence/history and rendered package seals have database protections. Database integration tests exist, though they could not be executed successfully in this review environment.
- Package approval locks the submission, rechecks approver membership/permission, verifies revision/input freshness and manifest/artifact hashes, and exports stored bytes. Sequential stale-package and cross-tenant tests are present.
- Exact decimal strings, explicit exposure periods/bases, separate coverage roles, safe filenames, formula-safe generated cells, scan-before-parse, and unapproved-preview labeling are useful foundations.
- Synthetic-only/local-only startup restrictions and public disclosures are explicit. No carrier transmission, binding, automatic coverage recommendation, or ACORD form reproduction was found in the reviewed workflow.

## Verification performed and limitations

- Read the approved plan and traced backend schemas, migrations, identity, intake, extraction, reconciliation, review, jobs, rendering/export, frontend workflow, tests, CI, and release documentation.
- Backend non-database suite: **35 passed**, 39 database cases deselected. Command: `PYTHONDONTWRITEBYTECODE=1 services/backend/.venv/bin/python -m pytest -p no:cacheprovider -m 'not database' services/backend/tests`.
- Frontend suite: **15 passed across 2 files** using the repository's Vitest test script. These tests cover connection/environment behavior, not the entire workspace.
- Database suite attempted: **1 failure and 38 setup errors because local PostgreSQL connectivity was blocked**. A direct connection to `127.0.0.1:55432` returned `PermissionError: Operation not permitted` under the execution sandbox. This is an environment limitation, not evidence that all those product tests are defective. Database/RLS/concurrency acceptance remains unverified in this review.
- Ran temporary in-memory reproductions for R02–R05 and R07–R09 using existing functions. Provider responses and OCR were mocked; no external provider calls or new test files were used. Other findings are based on source-path inspection rather than a live end-to-end exploit.
- Did not run browser acceptance, ClamAV/OCR service acceptance, production deployment, external vendor evaluation, or recovery drills. Existing tests do not establish concurrent upload/approval correctness, crash recovery, semantic support, or held-out quality merely by being present.

## Recommended order of work

1. Close R01–R06: completeness, material exception authority, context association, semantic support, and corrected-fact provenance determine whether an approved package is trustworthy.
2. Address repeated entities, currency, partial document processing, and retry behavior; add regression tests that reproduce the findings.
3. Complete the functional MVP gaps, especially renewal carry-forward, contextual schemas/rules, source navigation, lifecycle/tasks, and reproducible processing/usage records. Update roadmap claims to the actual accepted scope.
4. Re-run database, concurrency, browser, and hostile-file checks in an authorized local environment, then obtain independent domain/security/evaluation evidence before opening the real-data gate.

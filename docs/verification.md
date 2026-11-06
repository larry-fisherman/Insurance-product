# Engineering verification — October 29, 2026

The October 29 results and environment description below are historical. See the October 30 review-remediation verification at the end for the current changes and test environment.

Scope: local synthetic CGL review MVP. These are local engineering results, not production, legal, insurance-domain, or SOC 2 approval. No paid AI-provider calls, real customer documents, remote CI runs, or public deployments were performed.

## Final results

| Check                               | Result                    | Evidence / reproduction                                                                                                                                                                                                                                                    |
| ----------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python lint / format / strict types | Pass                      | `make check-api`; 39 Python source files type-checked                                                                                                                                                                                                                      |
| Backend unit tests                  | 35 passed                 | `make check-api`; exact amounts, flexible bases/periods, missing loss runs, source references, file safety, formula caches, active PDF rejection, text budgets, export citations, and simulated provider responses                                                         |
| Real PostgreSQL integration         | 39 passed                 | `make check-db`; newly generated disposable databases, migration up/down/drift, constraints, non-owner runtime RLS, scope reset, cross-agency access, MFA/recovery/CSRF, role changes, quarantine, material edits, review, sealed manifests, approval/export and staleness |
| Frontend lint / types / formatting  | Pass                      | `make check-web`; Next.js route types and strict TypeScript                                                                                                                                                                                                                |
| Frontend unit/component tests       | 15 passed                 | `make check-web`; environment and connection-state contracts                                                                                                                                                                                                               |
| Browser workflow                    | 3 passed                  | `CGL_SCAN_ACCEPTANCE=1 make check-e2e`; real API/PostgreSQL/Redis/worker/filesystem/ClamAV, no mocked business API                                                                                                                                                         |
| Frontend container build            | Pass                      | Docker build compiles marketing, security, login, invitation, and workspace routes                                                                                                                                                                                         |
| Same-origin smoke                   | Pass                      | `npm run smoke`; web shell and API health/readiness through the web origin                                                                                                                                                                                                 |
| OpenAPI consistency                 | Pass                      | Regenerated JSON matched `packages/contracts/openapi.json`; TypeScript schema regenerated with `make contracts`                                                                                                                                                            |
| Schema drift                        | Pass                      | `make db-check`: no new upgrade operations                                                                                                                                                                                                                                 |
| Compose configuration               | Pass                      | `docker compose --env-file services/backend/.env.app -f compose.full.yaml config --quiet`                                                                                                                                                                                  |
| Actual OCR                          | Pass                      | `make verify-ocr`; raster-only fictional PDF recognized in the worker with page/line citations                                                                                                                                                                             |
| Actual malware scanner              | Pass                      | Safe synthetic bytes accepted; standard harmless EICAR test pattern rejected; normal browser-uploaded PDF scanned and processed                                                                                                                                            |
| Synthetic regression evaluation     | Pass on generated formats | `make evaluate`; 100 generated explicit-label packets, described below                                                                                                                                                                                                     |
| Consistent backup                   | Pass                      | `make backup-local`; API/worker/scheduler stopped for snapshot, then restarted                                                                                                                                                                                             |
| Disposable restore                  | Pass                      | `make restore-drill`; 30 tables restored, 5 fictional agencies in that snapshot, all 24 referenced document/artifact hashes verified; disposable DB removed                                                                                                                |
| Secret-file handling                | Pass                      | Generated `.env.database` / `.env.app` mode 0600, ignored by Git; local artifacts ignored                                                                                                                                                                                  |

The final browser run completed all three cases in 44.5 seconds. It confirmed that a material conflict had no preselected source value, reviewed all 19 example fact groups, generated PDF/XLSX/ZIP candidates, approved an exact manifest, exported PDF bytes, and observed the approval in history. It also checked a 390-pixel mobile public page and ordinary unsigned document upload through the real scanner. The normal-upload case is intentionally opt-in when ClamAV is running; default CI/browser runs skip it unless `CGL_SCAN_ACCEPTANCE=1` is set.

A browser attempt launched during a container restart failed before entering the workspace. The acceptance suite now explicitly waits for the readiness endpoint before starting; the uninterrupted final three-case run passed. No code failures remain hidden by test retries.

## Evaluation limits

The 100-packet regression corpus uses explicitly labeled generated PDFs and schedule-like source blocks. It varies exact amounts and sales/payroll/area bases, and includes comparable conflicts versus different-period negatives. It achieved 1.0 precision/recall, exact amount accuracy, discrepancy precision/recall, and source-reference coverage **on those generated cases only**. Last recorded local median was 0.0257 seconds per generated packet and p95 was 0.0819 seconds; these exclude queues, scanning, OCR, hosted storage, and AI calls and are not product latency claims.

This is not an independent held-out insurance-document corpus. It does not establish real-world extraction completeness, semantic evidence support, human correction rate, unsupported-narrative rate, provider accuracy/cost, or customer savings. Those require the domain/pilot evaluation defined in pilot-readiness.md. Mocked provider tests verify the wire contract, strict schema, refusal/incomplete handling, and rejection of fabricated values/citations; they do not validate a live model or account.

## Review environment left running

Open http://localhost:3000/login and choose **Open a fictional example**. API, web, Redis, worker, scheduler, ClamAV, and the separate PostgreSQL stack remain running. API/web/Redis/ClamAV reported healthy; worker/scheduler are running without pretending that process presence proves throughput. Same-origin readiness was confirmed after the backup restart. Historical fictional examples and their exports remain in local volumes.

The screenshot `.artifacts/approved-workspace.png`, evaluation JSON, backup directory, and browser traces/results are local ignored artifacts. They contain fictional data. The backup is a point-in-time test snapshot, not an automatic ongoing backup service.

## Outstanding gates

See [pilot-readiness.md](pilot-readiness.md) and [roadmap-status.md](roadmap-status.md). Managed identity/email verification, production cloud/TLS/KMS/IAM, live provider/vendor terms and performance, independent security and insurance-domain evaluation, approved retention/deletion, operational support/contracts/E&O, pricing and actual broker pilots remain uncompleted external/commercial work. Synthetic-only and local/test configuration gates remain enabled.

## Review-remediation verification — October 30, 2026

Scope: the R01–R10 corrections and additional workflow changes described in [review-remediation.md](review-remediation.md). These results do not establish complete functional MVP or pilot acceptance. That document identifies the remaining engineering requirements and external acceptance work.

| Check                                    | Result                       | Evidence / limits                                                                                                                                                                                                                                                         |
| ---------------------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend lint, formatting, strict types   | Pass                         | Ruff and mypy; 43 Python source files type-checked.                                                                                                                                                                                                                       |
| Backend tests, including real PostgreSQL | 114 passed                   | Full `services/backend/tests` suite; 61 non-database and 53 database cases. Includes RLS, concurrency, migration up/down/drift, seeded legacy-evidence migration, review authority, exclusions, corrections, renewal, tasks, lifecycle, and parse checkpoint recovery.    |
| Frontend lint and strict types           | Pass                         | `npm run lint` and `npm run typecheck`.                                                                                                                                                                                                                                   |
| Frontend unit/component tests            | 22 passed across 4 files     | `npm test`; includes uncertain-response/reload/concurrent retry recovery and unsaved-form protection.                                                                                                                                                                     |
| Optimized frontend build                 | Pass                         | `API_INTERNAL_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1 npm run build`; compilation only, no deployment.                                                                                                                                                        |
| Generated contracts                      | Regenerated                  | Backend OpenAPI export and `npm run contracts:generate`; frontend compiles against the resulting types.                                                                                                                                                                   |
| Synthetic regression evaluation          | 100 generated packets passed | All six measured accuracy/coverage ratios were 1.0; local median 0.0066 seconds and p95 0.0125 seconds. `.artifacts/review-final-evaluation.json`; the earlier evaluation limits still apply. No provider calls, semantic-package accuracy, or real-world quality claims. |

Browser acceptance: **2 passed, 1 skipped** in 39.9 seconds. The complete fictional review/approval/export workflow and mobile navigation passed. The workflow also opened cited PDF pages and spreadsheet cells. The normal unsigned upload/scanning case was skipped because ClamAV was unavailable. An earlier run under concurrent heavy checks timed out; the final isolated run passed without test retries.

Changed/new code and documentation pass Prettier, and `git diff --check` passes. Repository-wide formatting still reports the preexisting, unchanged `review.md`; it was preserved as the user's review input.

Docker was unavailable. Database checks used an isolated native PostgreSQL 16 cluster with disposable databases. The cluster identity was checked against its temporary data directory before applying browser-test migrations. The developer's persisted database, private configuration, and existing volumes were preserved. Browser checks used temporary loopback services, filesystem storage, local extraction, and a polling worker without Redis; they do not establish Redis, scanner, or production-deployment acceptance.

The temporary API, web, polling worker, and dedicated PostgreSQL cluster were stopped after verification. Their temporary database and test object directory were removed. This supersedes the historical environment-left-running description above; no remediation test services remain running. Test output, evaluation JSON, browser results, and the fictional screenshot remain in ignored `.artifacts` paths.

Production/synthetic-only startup gates remain unchanged. No customer documents, paid-provider calls, remote CI, public deployment, independent assessment, or new backup/restore drill was performed for this remediation.

## Initial product promise verification — October 30, 2026

Scope: the five-capability acceptance mapping in [review guide](review-guide.md#initial-product-promise-acceptance), explicit MVP priorities in the product requirements/roadmap/agent instructions, and expanded classification/package regression assertions. This verifies supported synthetic behavior; full functional MVP and representative insurance-document acceptance remain open.

- `make check-api`: Ruff lint/format and mypy passed; 62 non-database tests passed.
- Full backend suite: **115 passed**, including 53 database cases, against a temporary loopback-only native PostgreSQL 16.14 cluster with restricted auth/runtime roles and disposable test databases. A temporary runner supplied process-local configuration without changing private environment files or the developer database. The cluster was stopped and temporary data removed afterward.
- Expanded package acceptance verifies that all PDF/XLSX/ZIP formats reject export before approval, exported bytes match previews, XLSX schedules/source references contain rows, ZIP contents match standalone files, the reviewed snapshot has decisions and valid source references, and all supporting-file hashes match the manifest. Existing stale-package export rejection remains covered. Classification assertions verify each of the four fictional document types.
- The first combined run exposed a readiness lifecycle defect: probing after shutdown could reopen a disposed database pool and leave a connection unclosed. Readiness now skips dependency probes while the application lifecycle is inactive; a dedicated regression test covers before startup, inactive state, and after shutdown. The final combined suite passed without warning suppression.
- `make evaluate`: 100 generated explicit-label extraction/reconciliation packets passed. No external provider calls; this evaluation does not measure unsupported package statements, independent held-out accuracy, or human correction effort.
- Changed Markdown passed Prettier and local link/anchor checks; `git diff --check` passed.

Docker was unavailable. Browser, Redis delivery, ClamAV ordinary-upload, and actual OCR acceptance were not rerun for this change; prior dated results remain historical. No customer data, paid-provider usage, deployment, or release authorization was performed. Parser breadth, complete contextual CGL semantics, domain-approved requirements, independent quality/security assessment, and customer-data gates remain open.

## Invariant tests (audit Batch 1) — November 5, 2026

Scope: tests only, no application behaviour change. Added a catalog-wide RLS check (`services/backend/tests/database/test_schema_invariants.py`), a route-by-role matrix (`services/backend/tests/database/test_route_permissions.py`) and approval/export tamper and supersession cases in `services/backend/tests/database/test_workspace.py`.

| Check                                      | Result           | Evidence / limits                                                                                                                                                                                                              |
| ------------------------------------------ | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `make check-api`                           | Pass; 119 passed | Ruff, Ruff format, mypy strict over 76 source files, non-database pytest.                                                                                                                                                      |
| `make check-db`                            | Pass; 130 passed | Disposable `cgl_test_*` databases on the loopback development PostgreSQL. Includes the new RLS catalog, runtime-denied, DELETE-grant, 401/403 matrix, `INTEGRITY_FAILURE`, `PACKAGE_SUPERSEDED` and `APPROVAL_REQUIRED` cases. |
| Agent gate (`make check && make check-db`) | Green            | `make check` includes `check-web`.                                                                                                                                                                                             |

Notes:

- `app.agency_activation` is classified as "forced RLS, no runtime policy or grant" and is reached only through `app.activate_agency(text,uuid,text)`. It is not a global table.
- `GET /api/v1/auth/config` is listed as public: the sign-in page reads the identity mode before a session exists. `/api/v1/docs` and `/api/v1/openapi.json` are not FastAPI API routes and are outside the matrix; their exposure is tracked under P0-A2.
- The viewer case for review decisions sends the current `expected_version`, because the handler checks the optimistic version before the permission (`services/backend/app/api.py:863-864`, `services/backend/app/workflow.py:892`).
- Not run for this batch: `make check-e2e`, `make evaluate`, `make verify-ocr`, scanner acceptance; not required by the batch.

## Dead-code removal and prompt-version constant (audit Batch 2) — November 5, 2026

Scope: no behaviour change. Removed the duplicate `get(...)` call in `audit_history` and the in-function `AppUser` re-import in `services/backend/app/api.py`, moved `timedelta` to the module import in `services/backend/app/workflow.py`, and added `PROMPT_VERSION = "cgl-evidence-2"` in `services/backend/app/domain/cgl.py`, used by both run-metadata sites in `services/backend/app/worker.py`. The recorded prompt version value is unchanged.

| Check                                      | Result           | Evidence / limits                                                         |
| ------------------------------------------ | ---------------- | ------------------------------------------------------------------------- |
| `make check-api`                           | Pass; 119 passed | Ruff, Ruff format, mypy strict over 76 source files, non-database pytest. |
| Agent gate (`make check && make check-db`) | Green            | `make check` includes `check-web`; `make check-db` on disposable DBs.     |

Not run for this batch: `make check-e2e`, `make evaluate`, `make verify-ocr`, scanner acceptance; not required by the batch.

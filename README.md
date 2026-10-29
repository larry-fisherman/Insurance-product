# CGL Workspace

A local, synthetic-data implementation of the CGL submission workflow: document intake → extraction with sources → reconciliation → broker review → versioned submission package → approval → manual export.

**Use fictional documents only.** Staging and production startup, and disabling synthetic mode, fail closed. This is an engineering review build; real-data and paid-pilot approval gates remain in [docs/pilot-readiness.md](docs/pilot-readiness.md). No ACORD forms, carrier distribution, coverage recommendations, or binding actions are implemented.

## Start the complete workspace

Prerequisites: Git, Docker Desktop with Compose, Node 24, npm, Python 3, and network access to package/container registries. Allow approximately 6 GB for Docker when running the scanner and browser tests.

```sh
make bootstrap
make full-up
make scan-up
```

Open **http://localhost:3000**, choose **Sign in**, then **Open a fictional example**. Each example creates a separate agency with four server-generated fictional documents and a deliberate sales-estimate discrepancy. The example session is explicitly marked as an MFA bypass for fictional review. Registered users instead enroll authenticator MFA and receive single-use recovery codes.

The scanner may take several minutes to initialize its signature database. Its absence never releases normal uploads from quarantine. Built-in fictional fixtures are HMAC-signed by the server and can be processed while the scanner initializes.

`make full-up` creates private local configuration, starts PostgreSQL, provisions restricted database roles, applies migrations, and builds the web/API/workers. Repeating it preserves documents, records, and secrets. The generated files `services/backend/.env.database` and `.env.app` are ignored by Git and created with mode 0600. Never paste them into logs or commit them.

```sh
make full-down  # stop application services; preserve volumes
make db-down    # stop PostgreSQL; preserve its volume
```

Do not use `docker compose down --volumes` unless intentionally discarding the local dataset. `compose.yaml` is the dependency-free health-shell smoke setup; **compose.full.yaml** is the implemented product.

## What to review

1. Open a fictional example and wait for all four processing jobs to finish.
2. In **Data & discrepancies**, compare the application’s $1,200,000 sales estimate with the spreadsheet’s $1,350,000 estimate. Neither is preselected. Inspect the quoted source text, page/line or sheet/range, context, and original files.
3. Verify the source value you confirmed, or correct/omit/reject it with a reason. Review every group. A broker-attested fact records its origin separately from document evidence.
4. Check **Package & approval**. Required facts cannot be waived. An authorized approver can accept specified missing-information exceptions with a reason; these remain visible in the package.
5. Generate the original PDF, XLSX, and ZIP candidate. Inspect the files and manifest, record final approval, and export. The approved bytes are the previewed bytes.
6. Replace/exclude a document or change a reviewed fact. The historical package is retained, and a fresh package/approval is required.
7. Review account/renewal creation, follow-ups, team roles, audit history, usage, and the preparation-time measurement form.

See [docs/review-guide.md](docs/review-guide.md) for expected behavior and limitations.

## Architecture

- Next.js / React / TypeScript frontend, using a same-origin `/api/v1` proxy.
- FastAPI application; PostgreSQL stores identity, tenant records, immutable evidence, review revisions, and approvals.
- Separate non-owner `cgl_auth` and `cgl_runtime` connections; forced PostgreSQL RLS plus transaction-local tenant/user scope for business records.
- Celery/Redis processes long-running documents and packages. A transactional outbox and expiring leases recover lost messages and crashed workers.
- Private shared filesystem storage for the portable local stack. An S3 adapter supports private, version-addressed objects when configured; hosted S3 validation is an external gate.
- ClamAV quarantines normal uploads; pypdf/openpyxl parse bounded inputs; Tesseract/PDFium handles scanned pages in bounded workers.
- Strict CGL validation and deterministic reconciliation. The default extractor is an explicit-label parser, **not an LLM**. Optional OpenAI Responses and approved HTTPS gateway adapters are included; no paid provider calls are made automatically.
- ReportLab and openpyxl generate original submission templates. ZIP exports include supporting documents, an index/manifest, and the reviewed snapshot.

The public architecture and original roadmap are in [plan.md](plan.md). Implementation decisions and deviations are in [docs/decisions/0003-review-mvp.md](docs/decisions/0003-review-mvp.md).

## Verification

```sh
make check           # lint, formatting, Python/TS types, unit/component tests
make check-db        # real PostgreSQL tests; disposable databases only
make contracts       # regenerate OpenAPI and TypeScript schema
npm run build
make evaluate        # 100 generated synthetic regression packets
make browser-install
make check-e2e       # full stack must already be running
CGL_SCAN_ACCEPTANCE=1 make check-e2e  # include ordinary upload with ClamAV running
make verify-ocr      # actual scanned-PDF path in the worker
npm run smoke
```

Results generated by tests/evaluation are ignored under `.artifacts/` and `test-results/`. Source tests are under `services/backend/tests`, `apps/web/tests`, and `tests/e2e`.

For local process development, use `make dev-api` / `make dev-web` after setting storage/Redis/scanner URLs appropriate to the host. Docker uses internal service URLs; generated host URLs use ports 55432 (PostgreSQL), 56379 (Redis), 53310 (scanner), 8000 (API), and 3000 (web).

## Optional extraction provider

Configure secrets in the ignored `.env.app`, then rebuild/recreate the API and worker services. Read the vendor’s approved data terms before sending anything; this build is synthetic-only regardless of provider.

```dotenv
CGL_EXTRACTION_PROVIDER=openai
CGL_EXTRACTION_MODEL=<explicit approved model identifier>
CGL_EXTRACTION_KEY=<private API key>
```

The OpenAI adapter uses HTTPS Responses with strict JSON Schema, bounded input/output, and `store: false`. This option is not a guarantee about vendor retention or contractual data handling. Refusals, incomplete output, bad schemas, unsupported values, and nonexistent citations stop extraction. No tools or autonomous actions are enabled. See the [official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

For an approved custom adapter, set `CGL_EXTRACTION_PROVIDER=gateway` and `CGL_EXTRACTION_ENDPOINT=https://…`; its request/response contract is documented in [docs/implementation.md](docs/implementation.md).

## Operations and launch boundaries

[docs/operations.md](docs/operations.md) covers backup/restore drills, retention/deletion, private-file recovery, job recovery, and incident response. [docs/pilot-readiness.md](docs/pilot-readiness.md) separates review-build functionality from real-data, paid-pilot, and commercial-launch requirements. No infrastructure accounts, external integrations, public deployment, billing, legal approval, or licensed templates are assumed.

# Implementation map

`plan.md` is the original product plan. The current implementation is a local synthetic CGL review build. See [review remediation](review-remediation.md) for corrected behavior, migration notes, remaining engineering requirements, and external acceptance gates.

## Repository

- `apps/web/src/app`: marketing, security, login/MFA, invitation, and workspace routes.
- `apps/web/src/components/workspace`: account/dashboard, review/evidence, submission/documents/packages, tasks/settings, and shell components.
- `apps/web/src/lib/workspace-api.ts`: bounded same-origin API client consuming generated workspace contracts; generated OpenAPI is under `packages/contracts`.
- `services/backend/app/identity.py`: Argon2/TOTP/recovery, opaque sessions, CSRF/origin, agency scope, role resolution.
- `app/database`: foundational metadata, runtime/auth pools, guarded local provisioning settings.
- `app/domain`: tenant workflow entities and validated CGL schemas/rules.
- `app/documents.py`: magic/size/expansion checks, ClamAV, PDF/OCR/XLSX source blocks, local/gateway/OpenAI extraction.
- `app/workflow.py`: idempotency, review groups, canonical revisions, readiness, explicit exceptions, and job outbox.
- `app/worker.py`: Celery dispatch/recovery, leases, document processing and package jobs.
- `app/rendering.py`: original PDF/XLSX/ZIP renderer, exact-text amounts, formula-safe cells.
- `app/api.py`: authorized business endpoints, approval/export and time measurement.
- `app/storage.py`: private filesystem/S3 interfaces; server-only keys.
- `app/evaluate.py`: reproducible generated synthetic regression evaluation.
- `app/operations.py`: consistent-stop local backup and disposable restore drill.
- `migrations/versions`: foundation, workflow/RLS, idempotency, and package/document seals.
- `compose.full.yaml`: local review stack; `compose.database.yaml`: loopback PostgreSQL; `compose.yaml`: health-shell smoke stack.

## API outline

All business paths are under `/api/v1`; JSON writes use strict input models, and unsafe authenticated actions require Origin and `X-CSRF-Token`. Agency scope comes from the opaque session. A caller-supplied tenant ID/header does not authorize business access.

- `/auth/register`, `/login`, `/mfa/confirm`, `/recover`, `/logout`, `/agency`, `/demo`; `/me`.
- `/dashboard`; `/insureds` list/create and `/{id}` detail/edit.
- `/submissions` list/create and `/{id}` workspace/edit; create renewals with `kind=renewal` and `prior_submission_id`.
- `/submissions/{id}/documents` multipart upload; `/documents/{id}/content`, `/exclude`; `/sources/{id}`.
- `/jobs/{id}/retry`; processing activity is returned in the workspace.
- `/submissions/{id}/review-decisions`, `/exceptions`, `/tasks`, `/audit`, `/measurements`.
- `/submissions/{id}/packages`; `/packages/{id}` detail, `/approvals`, `/artifacts/{pdf|xlsx|zip}`.
- `/tasks`, `/tasks/{id}`; `/agency`, `/invitations`, `/invitations/accept`, `/memberships/{id}`.

List APIs enforce limits. Insured/submission list APIs return cursors. Writes that create insureds, submissions, uploads, packages, or measurements require `Idempotency-Key` and bind it to a request hash. Review/settings writes use expected row versions; review also checks an evidence fingerprint. Uncertain network responses retain the same logical key/payload across retries and reloads. Concurrent equal keys are serialized in the database.

Core material fields are validated using exact decimal strings and typed context. Raw observations are never canonical records. Every material observation group requires a broker decision before generation. Manual attestations require an origin/reason and cannot be used to bypass conflicting canonical facts. Schema-evolution work must version CGL validation, parser, rules, prompt, template, and snapshot formats; never mutate old evidence or snapshots.

## Gateway contract

The operator configures an approved HTTPS endpoint and optional bearer key. Request JSON has `model`, `schema` (strict closed JSON Schema), `instructions`, `allowed_fields`, and `blocks` (`id`, `text`). Response JSON is `{ "facts": [...] }`; each fact has `field`, `value_json` (a JSON-encoded string or claim object), `context`, and `source_ids` (existing UUIDs). Context keys follow the returned schema. All material amounts use quoted exact decimals; no model confidence is accepted. Input is limited to 180,000 characters and output to 500 facts. Endpoint redirects are disabled. Refusals/incomplete responses fail the OpenAI adapter; gateway adapters must return a schema-valid abstention (`facts: []`) or an error.

Values, dates, currencies, claims and citations are revalidated. Field/value/context associations must match the bounded local evidence grammar; ambiguous provider proposals are explicitly unsupported and cannot be approved directly. These conservative checks do not establish general semantic accuracy. All persisted provider proposals remain unverified until broker review. OpenAI requests use the Responses API with `store:false` and strict text format; vendor retention, account access, chosen model, cost, and real document quality remain unverified until explicitly tested under approved terms.

## Material limitations

Native text/PDF OCR citations use page and text line; spreadsheets preserve cells, formats, formulas/cached values, merged ranges, and hidden context. Cited PDF page navigation/search highlighting depends on the browser; independent geometry overlays remain open. Complex table inference is outside the local parser's scope. Original policy wording is retained but not interpreted. Templates are original, distribution manual, customer billing unimplemented, managed OIDC not connected, and no production cloud resources are provisioned.

Approval verifies the canonical snapshot, requested document set, manifest, and artifact hashes while locking the submission and current approver membership. It does not assert insurance completeness, licensing authority outside configured roles, customer authorization, or carrier acceptance. Those remain professional and contractual controls.

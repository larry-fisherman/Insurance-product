# ADR 0003: Complete local review MVP

Status: implemented for synthetic local/test use. Date: October 29, 2026.

The user authorized continuous implementation through a final review. The initial roadmap remains the product specification. This ADR records deliberate implementation choices; it does not turn external discovery, customer validation, legal review, or production operations into completed engineering tasks.

## Modular monolith and tenancy

Keep one Python package, one API, PostgreSQL, and two background processes (Celery worker and scheduler). Authentication metadata uses a restricted auth role. Business requests derive agency scope from an opaque session and active membership; transaction-local settings and forced RLS constrain a non-owner runtime role. Missing scope and connection reuse fail closed. Runtime roles cannot modify immutable evidence, decisions, revisions, approvals, export records, or audit history. The migration role has an explicit maintenance RLS policy and never serves requests.

Local password/TOTP authentication makes the review build self-contained. MFA enrollment, replay prevention, recovery, CSRF, session revocation, invitation acceptance, and live role lookup are implemented. Managed OIDC/enterprise provisioning remain commercial-launch work; unused issuer fields are reserved configuration, not a working adapter. Synthetic example sessions disclose their MFA bypass and cannot turn off synthetic mode.

## Canonical CGL records

Raw document bytes, source blocks, extracted observations, review decisions, and canonical snapshots are separate records. Canonical facts use validated CGL field namespaces and explicit context (role, period, basis, unit, currency, scope, classification system, policy reference). Claims have a typed schema. Immutable snapshots avoid introducing partially synchronized insurance child tables before the pilot establishes its actual query and carrier needs. A future line-specific projection can be rebuilt from approved snapshots without rewriting evidence or approval history.

This is a narrower first implementation than the eventual normalized policy/exposure/coverage/claim tables in the plan. Do not treat raw observations or prior-year approvals as authoritative. Future projections must reference their canonical revision and preserve currency, periods, classification namespaces, and evidence.

## Processing and storage

Celery/Redis is retained for bounded processing and deployment parity. The database outbox commits with each request. A scheduler republishes queued or expired-lease jobs, and terminal jobs need explicit authorized retry. A worker's completion transaction locks the submission and verifies its lease attempt. Duplicate delivery cannot append another completed extraction. Workers use a tenant-scoped active agency actor; production service-principal design and deprovisioning semantics require further review.

The local stack uses a private shared Docker volume after upstream MinIO images could not be pulled from either registry. This avoids a broken setup dependency. The S3 adapter remains implemented, uses server-generated keys, private SDK access, conditional writes, and stored object versions; hosted bucket policies, encryption, IAM, lifecycle rules, and recovery require real infrastructure validation. No public document URLs are generated. Authorized streaming downloads are simpler than signed URLs for this MVP.

Normal uploads require ClamAV. Only exact server-generated HMAC-signed fictional fixtures can bypass the scanner. Parsing uses resource-limited worker processes with PDF/XLSX/OCR bounds. Scanning is an optional startup profile so fictional review works without a large signature download; ordinary uploads fail closed while it is unavailable.

## Extraction and reconciliation

The explicit-label parser is the default and openly identified in the UI. It supports known labeled PDFs and simple two-column spreadsheets, not arbitrary insurance layouts. Unrecognized facts abstain. The optional OpenAI Responses adapter and generic HTTPS gateway share strict schema/evidence validation, versioned prompts, and bounded document input. External AI requests require explicit secrets/configuration and were not exercised against a paid account.

No model confidence is used as proof. Reconciliation compares equivalent contexts only. Material conflicts require human decisions. Readiness checks prevent omitted required name/operations/exposure, duplicate claim valuations, and contradictory no-loss assertions; waivable missing-information items require approval authority and a reason. Aggregate, currency, classification, requested/expiring coverage, and loss period semantics must be evaluated with insurance reviewers before pilot use.

## Packages and exports

Package generation snapshots the reviewed revision and requested document IDs/hashes. Rendering checks the document set and hashes. It emits original PDF/XLSX templates and a supporting ZIP. A database trigger seals the rendered manifest. Approval locks the submission and reviewer membership, checks current authority, required review, freshness, manifest/snapshot hashes, and artifact bytes. Historical files remain available only with explicit historical acknowledgment through the API; the normal UI asks for a current package. Export uses the same bytes that were reviewed.

Generated prose is a deterministic narrative from approved fields. LLM-written underwriting narratives, quote analysis, carrier appetite, coverage advice, carrier distribution, AMS access, client portals, and other lines remain roadmap work.

## Commercial boundaries

No real data or production configuration is enabled. Security controls are engineering evidence, not legal compliance. Customer discovery, licensed expert evaluation, vendor terms, independent security assessment, real infrastructure encryption/restore tests, privacy/retention agreements, support/incident staffing, and pricing validation remain explicit gates.

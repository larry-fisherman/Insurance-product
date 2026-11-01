# ADR 0004: Separate public and agency runtimes

Status: accepted for local synthetic implementation, November 1, 2026. Implements the user-approved [web platform plan](../WEB_PLATFORM_PLAN.md); hosted release remains gated.

## Decision

Keep one repository and two independently built Next.js apps. `apps/marketing` serves public content and forwards a bounded inquiry schema to an isolated receiver. It has no operations API proxy, database credentials, private-file volume, or operations-network membership. `apps/web` serves authenticated operations and the same-origin FastAPI proxy. Host allowlists reject unknown hosts. Local defaults use `127.0.0.1:3001` for marketing and `localhost:3000` for operations: cookies are host-scoped, not port-scoped. Hosted domains must be separate hosts with host-only cookies.

FastAPI remains the authorization authority. A page-level cookie check provides navigation only. Every business API call carries the tab's expected agency/session/context version; switching agencies rotates session and CSRF secrets and causes a full document navigation. A stale tab is rejected rather than silently acquiring the new agency. File links carry the same non-secret context in their query string. Permission and current membership checks remain server-side.

Implement OIDC authorization code + PKCE with server-held secrets, browser-bound single-use state, nonce, configured issuer/JWKS/audience, verified email and required MFA method. Identity matching uses issuer/subject, never automatic email linking. OIDC mode disables password registration/login/recovery and synthetic demo entry. A real provider is not configured or accepted by this change. Local password/TOTP remains available only in local mode. Sensitive role, approval, exception, and billing actions require recent authentication; the explicit fictional demo bypass is retained.

Agency activation is operator-issued, expiring, verified-email-bound, and consumed through a narrowly granted SQL function. New agency owners receive administration and billing roles, not implicit document preparation or approval. Existing demo provisioning intentionally retains its review roles. Invitations validate current inviter authority, agency status, matching identity and seat capacity; existing memberships are managed explicitly. Onboarding records versioned acknowledgments, contact details and timezone. Application actions do not activate real-data use.

Subscriptions are manually administered local entitlements. Billing administrators can view limits, edit the billing contact and record immutable plan/cancellation/data-return requests. The operator CLI changes status/limits with an attributable reference and audit history. No checkout, invoices, webhooks or payment processor is connected. Counts use all-time synthetic submissions, not an inferred monthly commercial plan.

Resumable intake stores immutable 1 MiB parts in private storage. The server binds initialization to the file hash, byte count, submission and replacement; finalization validates every part and the complete file before creating an ordinary quarantined document/job. Retry recovers the same finalized document. Browser storage retains only upload references/digests; users reselect the original file after reload. Expired/aborted parts are retained pending the documented maintenance/retention work.

The full Compose stack runs parsing/OCR in a credential-free service on a dedicated internal network. One resource-limited child handles each file with a minimal environment and temporary working directory. Only the worker joins both parsing and operations networks. Parsing follows scanning; results are still unverified source evidence. Native tests retain an explicit in-process fallback when no parser endpoint is configured. Container isolation is implementation evidence, not an independent sandbox/security certification.

## Consequences and local deviations

- Marketing inquiries use a separate local SQLite volume by default, with an independent PostgreSQL URL supported by the receiver. This avoids giving public intake any operations database credential. Hosted inquiry migrations, retention, rate/abuse controls and operational access still need acceptance.
- Marketing alone joins a public edge network to publish its loopback port; its connection to inquiry intake uses a separate internal network. Neither network overlaps operations. The parser serializes one active child and one waiting request to bound resources without forcing the two local workers to retry routine simultaneous requests.
- Marketing is deliberately non-indexable and labelled fictional-only in this environment. Real conversion measurement, approved claims/legal copy, production domains and pricing are outstanding.
- The two applications can deploy independently, but this change does not deploy them. TLS, hosted IAM/storage, provider lifecycle/revocation integration, payment integration, email delivery, representative CGL acceptance and release gates remain open.

# ADR 0001: foundation, contracts, and local configuration

Date: 2026-10-29. Status: accepted for P1-01.

Use an npm-workspace monorepo for Next.js and uv-managed Python for FastAPI. Avoid framework generators that add unrelated product features. The Python API contract generates frontend types; the frontend validates network responses at runtime.

Use Node 24 and Python 3.13 as the standard runtimes, with Python 3.14 permitted by backend metadata. Pin direct packages and preserve both transitive lockfiles. TypeScript 5.9 stays within the installed TypeScript ESLint support range; ESLint 10 is supported by the installed Next.js configuration. No dependency is selected solely because it is the latest release.

The browser uses relative `/api/v1` requests. Next.js proxies to a validated server-only API origin. This avoids two browser origins and leaves authentication/authorization in the API when implemented. Rewrites are fixed by the frontend build; deployment must supply the correct origin at build time.

The backend uses an application factory. Importing modules does not read environment files or start services. Startup requires an explicit environment; staging/production fail closed until the identity/tenancy foundation exists. No pretend authentication, in-memory customer records, or upload endpoints are introduced.

Readiness currently means the application lifespan started. Database/storage/queue checks do not exist yet and are not advertised. Future services add real checks; process liveness must stay separate.

Compose builds non-root web/API containers, binds loopback ports, drops capabilities, and uses read-only filesystems with temporary scratch space. PostgreSQL/Redis/object storage join the local stack when their implementation tickets require them.

Official setup references checked during implementation:

- [Next.js installation](https://nextjs.org/docs/app/getting-started/installation): Node requirements, App Router, separate lint command.
- [Next.js output configuration](https://nextjs.org/docs/app/api-reference/config/next-config-js/output): standalone artifacts and monorepo tracing.
- [FastAPI package metadata](https://pypi.org/project/fastapi/): Python compatibility and dependency release.

No cloud resources or production environment are created by this decision.

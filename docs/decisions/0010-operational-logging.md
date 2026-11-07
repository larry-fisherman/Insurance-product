# ADR 0010: Content-free operational logging and request correlation

Date: November 6, 2026. Status: accepted for the local synthetic build.

## Context

The API had no logging configuration and no application logger, and Uvicorn runs with `--no-access-log`. Every `SQLAlchemyError`, including privilege/RLS refusals and programming errors, became an unlogged 503 "please retry". Unhandled exceptions fell to Starlette's default handler. Audit rows took a fresh `uuid4()` as `request_id`, and the response carried a different random `X-Request-ID`, so neither could be correlated with a request. Incident response and a security assessment both need correlated, content-free logs.

## Decisions

**Standard library only.** `app/logging_setup.py` installs one stdout `StreamHandler` with a JSON-line formatter on the root logger and replaces Uvicorn's own handlers so its lines use the same format. No new dependency and no exporter.

**Allowlisted fields, no content.** A line carries `timestamp`, `level`, `logger`, `event` and only these structured fields when present: `request_id`, `route`, `method`, `status`, `duration_ms`, `tenant_id`, `code`, `exception_type`, `job_id`, `attempt`. `route` is the matched route template (for example `/api/v1/submissions/{submission_id}`), never the raw path or query string. Bodies, query strings, headers, cookies, document text, provider/parser bodies and exception messages are never logged. Tracebacks are dropped; only the exception type name is written. Message arguments are interpolated only for this application's `cgl.*` loggers, whose call sites pass identifiers, counts and codes. Third-party loggers (Uvicorn, Celery, HTTP clients) emit their static message template without arguments, so a library cannot write a URL, task argument or exception text into a line.

**Request correlation.** An HTTP middleware accepts an inbound `X-Request-ID` only if it is a canonical UUID; otherwise it generates one. The ID is stored in a `ContextVar`, returned in the `X-Request-ID` response header and written on one access line per request (`event: "request"`). `common.audit()` uses the same ID, so an audit row's `request_id` matches the response header. Workers and CLI commands have no request and keep a fresh `uuid4()`. `tenant_id` on the access line comes from the authenticated actor and is used for correlation only, never for authorization.

**Error mapping.** Database errors are classified by SQLSTATE before type, because psycopg classes `42501` as a programming error and `55000` as an operational error:

| Condition                                   | Response                      | Logged  |
| ------------------------------------------- | ----------------------------- | ------- |
| `IntegrityError` (unchanged)                | 409 `WRITE_CONFLICT`          | no      |
| SQLSTATE `42501` (privilege/RLS refusal)    | 500 `ACCESS_POLICY_VIOLATION` | error   |
| SQLSTATE `55000` (immutability trigger)     | 409 `IMMUTABLE_RECORD`        | warning |
| other `OperationalError` / `InterfaceError` | 503 `DATABASE_UNAVAILABLE`    | warning |
| any other `SQLAlchemyError`                 | 500 `INTERNAL_ERROR`          | error   |
| any other unhandled exception (catch-all)   | 500 `INTERNAL_ERROR`          | error   |

500 responses carry the `request_id` in `detail` so a user can quote it. 500 responses and their access lines also carry the same ID in `X-Request-ID`. The catch-all is an `Exception` handler. Starlette re-raises the exception after the handler responds, so the server also writes its own error line; that line goes through the same formatter, which drops the traceback and message and keeps only the exception type.

**Worker.** The Celery `setup_logging` signal installs the same formatter for the worker and scheduler. The job-failure line carries `job_id`, `tenant_id`, `attempt`, `code` and `exception_type`.

## Limitations

- Logs go to container stdout only. Central collection, retention, access control, alerting and dashboards remain launch work.
- The inquiries and parser services are separate applications and are not covered.
- The template-only rule for third-party loggers loses some library detail (for example Celery task names). That is deliberate.
- `ACCESS_POLICY_VIOLATION` is reported as 500 so that an RLS or grant refusal is treated as a defect to investigate, not as a client error.

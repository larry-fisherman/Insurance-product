# ADR 0005: explicit versions and bounded authentication/parser/provider boundaries

Date: November 1, 2026. Status: accepted for the authorized local synthetic audit-remediation work.

## Context

The [platform audit](../WEB_PLATFORM_AUDIT.md) reproduced conflicting version metadata, same-host cookie configuration, loss of provider authentication age and isolated-parser error identity. Provider response bodies were decoded without a transport-byte bound. Correcting these must preserve historical review/approval evidence and fail-closed processing.

## Decisions

- Explicit domain versions belong to the writer that executes the domain rule. Migration `0008_versions` removes old schema/rule defaults without updating existing rows. New revisions bind their column and snapshot schema version and snapshot rule/profile metadata. Current conflict findings carry the reconciliation version; accepted missing-information findings carry the requirements profile. Existing immutable snapshots and hashes remain unchanged. This does not claim a complete version registry.
- Marketing and operations must have different cookie hostnames. One public-only TypeScript validator runs in both builds/runtimes; Python enforces the same basic hostname separation at backend startup. Ports and schemes are insufficient separation. Compose passes both public origins to both frontends without sharing credentials.
- OIDC session authentication time comes from the validated signed ID token. Callback/session creation time cannot renew it. Allowed future skew is clamped to current time; sensitive-action checks retain the existing ten-minute window. Session rotation does not refresh authentication time.
- Parser errors cross the isolation boundary through a closed vocabulary. Remote text or a remote `retryable` property cannot define retry policy. Known hostile/resource/format failures remain nonretryable automatically; parser capacity/configuration failures and unavailable transport retain bounded retries. Quarantine and the existing unsafe-document manual-retry guard remain enforced.
- Extraction transport reads uncompressed raw response chunks with a 2 MiB maximum before JSON parsing. Compression is rejected even if a server ignores the identity-encoding request. JSON nesting is checked before decoding outer responses, embedded structured outputs and fact values. An elapsed deadline plus socket timeouts bounds slow responses; schema and semantic evidence validation still follow transport validation.

## Consequences and limits

New database writes cannot silently inherit obsolete version labels. Historical mismatches remain visible and require compatibility-aware interpretation. Coordinate migrations with writer restarts.

Configured gateways must return uncompressed bounded JSON. Limit failures produce safe error codes, without logging provider bodies or treating them as facts. Actual usage budgets, approved provider egress and live identity-provider testing remain separate work.

Retained upload reservations and cleanup are deliberately still open. Removing parts inside a business transaction alone is insufficient: storage cannot roll back with PostgreSQL, finalization and cleanup can race, and crash-created orphans need durable accounting. The next change must cover retained/in-flight quotas and retry/crash handling without erasing original documents or approved artifacts.

No customer-data, public deployment, payment/provider-spend or external messaging authorization follows from these corrections.

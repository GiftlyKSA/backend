# Giftly outstanding task tracker

**Created:** 2026-10-08 · **Last updated:** 2026-10-09 · **Source:** [codebase review](codebase_review.md).

Fresh outstanding work only. Completed review fixes are recorded in the review, not repeated as open tasks. Scores are urgency estimates, not CVSS. Implementation and deployment are separate.

## Current review and release work

| ID | Task | Short description | Status | Urgency | Created | Note / completion requirement |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-19 | Public testing-secret rejection | Reject published placeholder keys in production. | Deferred by user | High 7 | 2026-10-04 | Explicitly excluded from this fix request; do not silently implement. Protect real deployment secrets operationally. |
| OPS-01 | Remote CI and deployment verification | Verify Linux image/CI, worker processes and public rollout. | Local service checks complete; remote verification blocked | Medium 5 | 2026-10-04 | Python3.13/PostgreSQL16/Redis7/native FFmpeg full suite: 1,241 passed, zero skips, 89.35% coverage. Hooks/review passed. GitHub dispatch returned 401; user declined CranL sandbox. Linux image and public rollout remain unverified. |
| SQL-01 | Representative capacity measurement | Measure growing histories, pool waits, p95/p99 and decoder RSS/CPU under real workloads. | Partially verified | Medium 5 | 2026-10-04 | Repeated 17 synthetic plans and an 80-subscription/800-request Redis pool probe passed on 2026-10-09. Warmed Redis p95 2.477 ms / p99 2.741 ms; not deployed API capacity. |
| PERF-09 | Audit growth monitoring | Monitor metadata and recipient-snapshot growth. | Archival excluded for dev by user | Medium 5 | 2026-10-04 | No archive, retention purge or automatic deletion is planned for this development server. Revisit capacity and retention before production. |
| SEC-15 | Ingress credential redaction | Verify query/header/subprotocol redaction with fake-token probes. | Excluded from current work by user | Medium 5 | 2026-10-04 | Chat supports safer header/protocol transport; legacy query compatibility remains. |
| SEC-20 | Remote dependency alert disposition | Reconcile GitHub alerts with exact current lockfile and deployed image. | Excluded from current work by user | Medium 5 | 2026-10-04 | Earlier dated complete-graph scan was clear; unavailable alert metadata is not zero alerts. |
| INT-01 | Live vendor integration | Verify SMS/email/push and Dhamen authentication, retries and callback semantics. | Excluded from current work by user | Medium 5 | 2026-10-04 | No real-provider success claimed; do not enable production payments based on mocks. |
| PAY-DEPLOY | Published payment contract rollout | Resolve valid provider configuration and verify new public payment-session routes. | Operational approval / deployment verification pending | High | 2026-10-08 | Latest build succeeded but CranL remains deploying; readiness200/public OpenAPI still old on 2026-10-09. Runtime logs not_ready; prior DHAMEN_APP_ID blocker is historical evidence. Provider changes await approval. |
| UI-CHAT | Optional retry-safe chat wiring | Reuse a UUID for retries, handle explicit validation/conflict frames, deduplicate messages. | Mobile integration pending | Medium | 2026-10-08 | Existing callers remain compatible; chat-only handoff and generated mobile OpenAPI define the contract. |
| UI-INVOICE | Final item price rollout | Remove obsolete VAT input/display in the mobile app. | Mobile verification pending | High | 2026-10-08 | Backend final item prices include no separately added/returned VAT; service/courier fees remain. |

SEC-16/SEC-17 remain accepted High 9 risks under the explicit unrestricted admin CRUD decision, not newly authorized implementation tasks. No automatic financial-history or audit immutability change is planned.

## Existing screen capabilities awaiting product decisions

These are separate proposal-first work, not defects resolved by the current review. Existing occasion CRUD/date filters, invoice reads/listing, wallet statements, media reads and owned payment sessions are implemented in source; public deployment must still be verified.

| ID | Task | Short description | Status | Created | Urgency / next decision |
| --- | --- | --- | --- | --- | --- |
| MOB-05 | Courier earnings and targets | Settled-ledger reports, comparable periods and report preferences. | Earnings basis / delivery policy pending | 2026-10-07 | High; wallet balance is not earnings; define week/timezone and functional financial notifications. |
| MOB-06 | Notification inbox and timeline | Persistent user notifications and actual order status history. | Proposal pending | 2026-10-07 | High; chat/order live streams do not prove a complete historical inbox/timeline. |
| MOB-07 | Occasion reminders | Deliver reminders with explicit channel/time/retry policy. | Product decision pending | 2026-10-07 | Medium; stored settings do not prove delivery or annual recurrence. |
| MOB-08 | Courier avatar | Secure owned upload/change/removal flow. | Proposal pending | 2026-10-07 | Medium; customer avatars excluded. |
| MOB-09 | Phone change | Verified new-number ownership and session policy. | Security/product decision pending | 2026-10-07 | High; normalization, uniqueness and reauthentication required. |
| MOB-10 | Account deletion / termination | Recoverable request, detailed email and support-call fund-return notice. | Remaining business/retention policy pending | 2026-10-07 | High; user approved 14-day recovery; active orders, financial liabilities and legal retention still need explicit policy. |
| MOB-11 | Customer order search | Bounded own-order text/number search with existing filters. | Searchable-field/index decision pending | 2026-10-07 | Medium; no invented order number or unbounded history scan. |
| MOB-12 | Optional calendar/bank/document features | Independent courier appointments, bank profile, annual recurrence and statement PDF. | Explicit approval pending | 2026-10-07 | Medium; do not expose unsupported switches or invent legal/business rules. |

## Usage checkpoint — 2026-10-08

At 90% five-hour usage, verified review fixes and this outstanding-work tracker are published to master. Full unit checks, hooks, scoped native PostgreSQL tests and independent review passed; final offline aggregate passed 983 tests with 258 service/native skips; complete PostgreSQL/Redis CI remains outstanding. No Docker or production changes ran. SEC-19 remains excluded by the user.

## Verification follow-up — 2026-10-09

Local service verification supersedes the unavailable-service checkpoint: all 1,241 tests passed on Python3.13 with PostgreSQL16, Redis7 and native media decoding, no skips and 89.35% coverage. Test fixture/contract repairs preserve exact financial and access-control assertions. No new API, migration, mobile change, archival, Docker execution or production data change was introduced. Public rollout and authenticated remote CI remain distinct outstanding gates.

# Giftly outstanding task tracker

**Created / last updated:** 2026-10-08 · **Source:** [codebase review](codebase_review.md).

Fresh outstanding work only. Completed review fixes are recorded in the review, not repeated as open tasks. Scores are urgency estimates, not CVSS. Implementation and deployment are separate.

## Current review and release work

| ID | Task | Short description | Status | Urgency | Created | Note / completion requirement |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-19 | Public testing-secret rejection | Reject published placeholder keys in production. | Deferred by user | High 7 | 2026-10-04 | Explicitly excluded from this fix request; do not silently implement. Protect real deployment secrets operationally. |
| OPS-01 | Full service and deployment verification | Run PostgreSQL/Redis CI, native media decoding, scheduler and Python3.13 image checks. | Needs validation | Medium 5 | 2026-10-04 | Focused native PostgreSQL checks passed; broad service suite has unavailable Redis and legacy fixture/contract failures. Preserve meaningful assertions and reach the 85% CI coverage gate. |
| SQL-01 | Representative capacity measurement | Measure growing histories, pool waits, p95/p99 and decoder RSS/CPU under real workloads. | Partially verified | Medium 5 | 2026-10-04 | Synthetic plans/query counts and index write costs recorded; no production-capacity claim. |
| PERF-09 | Audit archive and retention | Define protected archive, retention and restoration before bounded archival. | Policy needed | Medium 5 | 2026-10-04 | No automatic deletion; metadata writes and outbox recipient growth require monitoring. |
| SEC-15 | Ingress credential redaction | Verify query/header/subprotocol redaction with fake-token probes. | Needs validation | Medium 5 | 2026-10-04 | Chat now supports safer header/protocol transport; legacy query compatibility remains. |
| SEC-20 | Remote dependency alert disposition | Reconcile GitHub alerts with exact current lockfile and deployed image. | Authorized security-maintainer validation needed | Medium 5 | 2026-10-04 | Earlier dated complete-graph scan was clear; unavailable alert metadata is not zero alerts. |
| INT-01 | Live vendor integration | Verify SMS/email/push and Dhamen authentication, retries and callback semantics. | External validation | Medium 5 | 2026-10-04 | No real-provider success claimed; do not enable production payments based on mocks. |
| PAY-DEPLOY | Published payment contract rollout | Resolve valid provider configuration and verify new public payment-session routes. | Operational approval / deployment verification pending | High | 2026-10-08 | Last recorded startup blocker: missing DHAMEN_APP_ID; old image healthy/new route404. Do not silently change provider. |
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

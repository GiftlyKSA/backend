# Giftly task tracker

## Invoice final-price change — created/updated 2026-10-08

| Task | Short description | Status | Created | Urgency / note |
| --- | --- | --- | --- | --- |
| INV-PRICE-01 | Remove separate VAT from pricing, contracts, PDF/admin/receipts and settlement | Implemented and locally verified | 2026-10-08 | High; retain service/courier fees and server totals |
| INV-PRICE-02 | Apply and roll back migration0024 on disposable PostgreSQL | Pending environment verification | 2026-10-08 | High; refuses tax-bearing history; backup before cleanup |
| INV-PRICE-03 | Update mobile invoice authoring and display to final prices | Pending mobile integration | 2026-10-08 | High; remove obsolete tax inputs before rollout; handoff in chat |

## Mobile capability delivery — created 2026-10-07

| Task | Short description | Status | Created | Urgency / note |
| --- | --- | --- | --- | --- |
| MOB-01 | Owned invoice list and revision/date filters | Verified; deployment unconfirmed | 2026-10-07 | High; deploy unconfirmed |
| MOB-02 | Dated wallet statement, SQL totals and linked ledger rows | Verified; deployment unconfirmed | 2026-10-07 | High; totals are live per page |
| MOB-03 | Authorized paginated order photos/proof reads | Verified; deployment unconfirmed | 2026-10-07 | High; signed URLs expire in five minutes |
| MOB-04 | Top-up idempotency and lost-request recovery | Pending review of unreleased workspace payment work | 2026-10-07 | High; pushed contract remains three fields |
| MOB-05 | Courier proceeds/profit reports and target settings | Pending earnings-basis decision | 2026-10-07 | High; balance is not earnings |
| MOB-06 | Durable inbox and order timeline | Planned | 2026-10-07 | High; push/live snapshots do not provide history |
| MOB-07 | Delivered occasion reminders | Planned | 2026-10-07 | Medium; stored reminder preferences are not delivery |
| MOB-08 | Courier avatar flow | Planned | 2026-10-07 | Medium; customer avatars excluded |
| MOB-09 | Phone change with scoped proof and session revocation | Planned | 2026-10-07 | High; both-number verification proposed |
| MOB-10 | Recoverable account deletion/contract termination | Policy clarification pending | 2026-10-07 | High; user approved 14-day recovery and detailed email/support-call fund-return notice |
| MOB-11 | Customer text order search | Search/index policy pending | 2026-10-07 | Medium; preserve dates/status/ownership |
| MOB-12 | Appointments, bank profiles, annual recurrence, statement PDF | Optional approval pending | 2026-10-07 | Medium; do not expose unsupported switches |


**Created / last updated:** 2026-10-04 · **Source:** [current codebase review](codebase_review.md).

Fresh outstanding work only. No demo tasks or completed historical plans. Security
precedes logic/SQL performance, memory/CPU efficiency and maintainable scalability.
`Open` requires implementation; `Needs validation` requires evidence from the stated
environment; `External validation` depends on provider/deployment capabilities.

| ID | Task name | Short description | Status | Urgency | Created | Note / completion evidence |
| --- | --- | --- | --- | --- | --- | --- |
| FIN-01 | Bound discount rounding | Prevent negative/oversized per-line discounts and incorrect final prices. | Open | High7 | 2026-10-04 | Tiny-price item regressions; review existing invoice correction separately. |
| FIN-02 | Preserve reserved wallet funds | Validate debit/holds from freshly locked wallet state with consistent lock order. | Open | High7 | 2026-10-04 | PostgreSQL two-session payment/reservation regression and ledger invariants. |
| SEC-19 | Reject published production secrets | Refuse known public testing/placeholder keys in production without changing local modes. | Open | High7 | 2026-10-04 | Negative boot tests; safe key/ciphertext rotation plan. |
| SEC-18 | Revoke access on refresh replay | Make detected refresh compromise invalidate previously minted access credentials. | Open | Medium6 | 2026-10-04 | Access/refresh/socket denial survives401 and concurrent rotation. |
| PERF-10 | Bound cancelled decoding | Hold decoder admission until actual work stops; enforce hard resource deadlines. | Open | Medium6 | 2026-10-04 | Cancellation/load probe proves bounded decoder count and memory/CPU. |
| REL-12 | Prevent receipt starvation | Skip/defer missing-email backlog without blocking newer deliverable paid receipts. | Open | Medium6 | 2026-10-04 | 100 no-email invoices plus valid recipient; later email retry. |
| PERF-11 | Bound active push devices | Add concurrent-safe device quota, stale-token cleanup and paged fanout. | Open | Medium5 | 2026-10-04 | Quota/ownership races and bounded SQL/memory/delivery latency. |
| REL-13 | Make chat retries recoverable | Add owned client idempotency and bounded durable delivery/reconciliation. | Open | Medium5 | 2026-10-04 | Retry after committed timeout produces one message; reconnect/outage tests. |
| SQL-01 | Measure SQL/pool/resource budgets | Capture representative query plans/counts, pool waits and notification/media costs. | Needs validation | Medium5 | 2026-10-04 | Disposable PostgreSQL and deployment-like load; no speculative indexes. |
| PERF-09 | Define audit retention/export | Agree retention, independent archive, storage alerts and restoration. | Needs validation | Medium5 | 2026-10-04 | No silent purge; archive survives privileged local CRUD. |
| SEC-15 | Verify socket credential redaction | Verify ingress logging and replace legacy chat query-token transport compatibly. | Needs validation | Medium5 | 2026-10-04 | Fake-token probes; bounded ticket expiry/replay/compatibility if introduced. |
| SEC-20 | Confirm dependency alert refresh | Verify current GitHub alert disposition after runtime and hook-dependency patches. | Needs validation | Medium5 | 2026-10-04 | Earlier pushes20→4 alerts; metadata access401; complete locked graph now scans clear. |
| OPS-01 | Validate current deployment | Apply migrations0016–18, concurrency regressions and decoder/startup gates. | Needs validation | Medium5 | 2026-10-04 | Disposable PostgreSQL/Redis or exact-commit CI; no local Docker. |
| INT-01 | Validate live provider contracts | Verify SMS/email/push acceptance and Dhamen authentication before payment enablement. | External validation | Medium5 | 2026-10-04 | Outside prior vendor-fix scope; production payments remain disabled. |
| API-02 | Reject malformed inbox cursors | Return a stable client error instead of restarting the first page. | Open | Medium4 | 2026-10-04 | Empty/invalid/valid cursor and participant-scope checks. |
| REL-14 | Keep500 correlation and headers | Preserve request ID/security/CORS policy on unexpected failures. | Open | Medium4 | 2026-10-04 | Safe correlated error envelopes and redacted operational logs. |
| QUAL-02 | Unify socket text validation | Apply the REST message schema to incoming WebSocket text. | Open | Low3 | 2026-10-04 | Null/object/length rejection and literal-code text parity. |
| QUAL-03 | Repair stale source references | Replace misleading links to removed design/spec records in affected modules. | Open | Low2 | 2026-10-04 | Reference scan; focused docstring edits without runtime churn. |

Accepted SEC-16/SEC-17 admin CRUD risks are recorded in the review, not represented
as pending restrictions that contradict the user's decision. Independent archival and
reconciliation work remains necessary. Mark tasks complete only with recorded evidence;
update the review and this tracker together when findings or verification change.

## Courier performance follow-up — created 2026-10-08

| Task | Short description | Status | Created / last update | Urgency / note |
| --- | --- | --- | --- | --- |
| CP-DEPLOY | Apply0021–22 and run worker/scheduler | Needs deployment validation | 2026-10-08 | High; transactional index builds may block writes; downgrade0022 discards pending push intents. |
| CP-MEASURE | Measure endpoint p50/p95, query plans, pool waits and resource costs | Needs validation | 2026-10-08 | Medium; use disposable PostgreSQL/Redis and representative data, not production mutations. |
| CP-PUSH | Check chat push backlog, terminal failures and crash retries | Needs validation | 2026-10-08 | Medium; at-least-once delivery, minute schedule, bounded20 pages per run; no guaranteed instant push. |
| CP-CACHE | Verify bounded PDF Lua eviction/TTL on Redis | Needs validation | 2026-10-08 | Medium; real-Redis integration added; local service unavailable. Additional order/ledger caches require measured benefit and safe invalidation. |


## Payment-session release checkpoint — 2026-10-08

| Task | Short description | Status | Created | Urgency / note |
| --- | --- | --- | --- | --- |
| PAY-01 | Release owned payment-session recovery, status, refresh and cancellation | Verified; published by this release | 2026-10-08 | High; WebSockets preserved; deployment remains UNCONFIRMED until exact live contract verification. |
| PAY-02 | Verify callback batch lock order and strict provider status | Fixed; PostgreSQL validation remains | 2026-10-08 | High; strict status, global ledger/wallet locks, durable late-payment quarantine and eligibility regressions pass; PostgreSQL regressions added but not locally executed. |
| PAY-03 | Validate migration graph and hosted ledger runtime | Offline verified; runtime pending | 2026-10-08 | High; retain applied 0021/0022 and join 0019/0020 using 0023. Disposable PostgreSQL required; no Docker on user machine. |
| PAY-04 | Publish payment contracts and UI handoff | Verified; published by this release | 2026-10-08 | High; both OpenAPI references and maintained docs updated; chat-only handoff on release; production payments remain disabled. |
| PAY-DEPLOY | Apply valid provider selection and verify new public routes | Blocked on deployment configuration approval | 2026-10-08 | High; selected Dhamen is missing DHAMEN_APP_ID; proposed PAYMENT_PROVIDER=disabled. Old service remains healthy; new route404 until rollout passes. |

The earlier usage-limit checkpoint published status only. This release supersedes
that payment/PDF checkpoint; unrelated SMS/email/payout edits remain local.
No live payment-provider or database verification was run.


## Invoice PDF delivery — created 2026-10-08

| Task | Short description | Status | Created | Urgency / note |
| --- | --- | --- | --- | --- |
| PDF-01 | Share approved Giftly purple English PDF across download and paid email | Implemented and visually checked | 2026-10-08 | Medium; GMT+3 issue/payment dates; UTC persistence/API unchanged. |
| PDF-02 | Reuse identical PDF one hour, refresh changed content/status | Verified | 2026-10-08 | Medium; exact3600-second expiry, item/status refresh, template fingerprint, private ownership and outage fallback tests. |

The earlier usage-limit checkpoint above is historical; payment/PDF release status is
now updated. Provider/live database validation and query-plan/load measurements remain
pending. No Docker, live-provider payment or production database operation was performed.

## Remaining API performance work — updated 2026-10-08

| Task | Status | Created | Urgency / remaining note |
| --- | --- | --- | --- |
| AP-P01/AP-P02 | Previously implemented | 2026-10-08 | High7/Medium6; live Redis/load verification remains. |
| AP-P03 | Implemented; focused tests/review pass | 2026-10-08 | Medium6; native decoder/S3 load pending. |
| AP-P04/AP-P05 | Implemented; PostgreSQL/query-count tests pass | 2026-10-08 | Medium6/5; live socket/pool latency pending. |
| AP-P06/AP-P07 | Measured; unsafe/unhelpful cache/index rejected | 2026-10-08 | Medium5; exact aggregates retained; real growing-history latency remains to measure. |
| AP-P08/AP-P09 | Candidate indexes pending; not released | 2026-10-08 | Medium5/4;0025 up/down/re-up verified; deploy migration and measure actual distributions. Broad calendar/status opportunity remains. |
| AP-P10 | Implemented; nine tests/review pass | 2026-10-08 | Low4; bounded fingerprint coalescing; multi-worker/load pending. |
| AP-VERIFY | Complete PostgreSQL/Redis and native media gate | Pending | 2026-10-08; date-dependent older tests fail and Redis/native decoder unavailable. |

No new API endpoint or response field was introduced. Prior product/security tasks
above remain separate; this performance release does not claim to resolve them.

### Usage-limit checkpoint — 2026-10-08

At91% five-hour usage, the approved checkpoint publishes AP-P03/AP-P04/AP-P05/AP-P10 only. AP-P06/AP-P07 retain exact queries after measurement. AP-P08/AP-P09 candidate indexes are NOT published: generic prepared plans may lose partial-index and expression-order benefits; approved-only write costs need validation. Candidate source and raw plans are preserved in the attached worktree's ignored workflow folder. No new migration is required for this checkpoint. Full unit suite passed; broad service suites remain incomplete (PostgreSQL run interrupted on existing date/Redis failures, non-service integration checks also too slow for the checkpoint). Deployment and full PostgreSQL/Redis/native decoder checks remain unconfirmed.

# Giftly task tracker

**Created / last updated:** 2026-10-04 · **Source:** [current codebase review](codebase_review.md).

Fresh outstanding work only. No demo tasks or completed historical plans. Security
precedes logic/SQL performance, memory/CPU efficiency and maintainable scalability.
`Open` requires implementation; `Needs validation` requires evidence from the stated
environment; `External validation` depends on provider/deployment capabilities.

| ID | Task name | Short description | Status | Urgency | Created | Note / completion evidence |
| --- | --- | --- | --- | --- | --- | --- |
| FIN-01 | Bound discount rounding | Prevent negative/oversized per-line discounts and incorrect VAT. | Open | High7 | 2026-10-04 | Tiny/mixed-rate item regressions; review existing invoice correction separately. |
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
| SEC-20 | Reconcile dependency alerts | Compare remaining GitHub alert packages/manifests with the current master graph. | Needs validation | Medium5 | 2026-10-04 | Push reports 20 alerts; metadata access returned HTTP401; production-lockfile scan is clear. |
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

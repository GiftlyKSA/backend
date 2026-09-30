# Giftly backend task tracker

Last reviewed: **2026-09-30**. Source: [2026-09-30 backend review](2026-09-30-backend-review.md). This is the follow-up list for that review, not a claim that any finding has been fixed. The **last update** column records when the task was last checked against the code; update the status, date, and note together when work or verification changes it.

**Status:** `Open` = work remains; `Decision needed` = the requested admin capability affects the fix; `Needs validation` = a suspected risk needs deployment or load evidence. **Urgency:** `Urgent` = score 8–10, `High` = 6–7, `Medium` = 4–5, `Low` = 1–3. Urgency follows the review score and is not a delivery deadline.

| Task | Status | Urgency | Last update | Note / next step |
| --- | --- | --- | --- | --- |
| SEC-16 — Protect financial admin maintenance | Decision needed | Urgent · 9/10 | 2026-09-30 | Admin CRUD currently bypasses ledger and issued-invoice guards. Define a separately authorized repair grant that preserves the requested admin capability; test and reconcile on disposable PostgreSQL. |
| SEC-17 — Make audit history tamper-resistant | Decision needed | Urgent · 9/10 | 2026-09-30 | Generic admin CRUD can edit/delete `audit_logs`. Add an independently retained append-only audit destination and decide how to restrict or break-glass database audit edits. |
| SEC-13 — Enforce the actual HTTP body limit | Open | Urgent · 8/10 | 2026-09-30 | Count bytes received by ASGI, including absent or false `Content-Length`; test malformed, streaming, and oversized bodies through the app and ingress. |
| PERF-08 — Shorten media database transactions | Open | High · 7/10 | 2026-09-30 | Move S3 network work outside database locks, then recheck grant ownership and status atomically; test slow S3 and concurrent claims. |
| SEC-14 — Define Redis-outage rate-limit behavior | Open | High · 7/10 | 2026-09-30 | Ordinary HTTP throttling fails open. Fail closed on sensitive routes and define a bounded fallback or 503 policy for other traffic; test outage behavior. |
| REL-10 — Make chat sends retry-safe | Open | High · 7/10 | 2026-09-30 | A message can commit before push/Redis delivery fails. Add send idempotency and a durable delivery event, or preserve a successful response for the committed message; test failure and retry cases. |
| REL-09 — Stabilize courier push recipient paging | Open | High · 6/10 | 2026-09-30 | Random UUID paging can miss new/replaced device tokens. Snapshot recipients or use an ordered high-water mark; test concurrent registration and multi-page sends. |
| PERF-09 — Plan audit-log capacity and retention | Open | High · 6/10 | 2026-09-30 | Nearly every request adds another database write and indexed audit row. Define retention/archival rules and measure request latency and table growth while keeping required events. |
| REL-11 — Guarantee critical-action audit records | Open | High · 6/10 | 2026-09-30 | HTTP audit rows are written after business commits and can be lost. Write critical action events in the business transaction and forward them durably; test audit failures and interruption. |
| DATA-01 — Complete the requested user lifecycle | Open | High · 6/10 | 2026-09-30 | Add the requested gender, `DELETED` status, and deletion reason through a reviewed migration and API/admin contract update; test existing rows. |
| DATA-02 — Remove obsolete courier identity collection | Open | High · 6/10 | 2026-09-30 | The identity-document form, encrypted fields, fingerprint, and duplicate rule remain. Remove them together with a reviewed migration and onboarding tests. |
| DEP-01 — Run migrations once per deployment | Open | Medium · 5/10 | 2026-09-30 | Compose has a migration service, then each service entrypoint runs migrations again. Use one migration owner and verify three-worker startup and failure behavior. |
| API-01 — Reject invalid pagination anchors | Open | Medium · 5/10 | 2026-09-30 | Missing order/wallet cursors silently restart the list. Return the established client error for missing or out-of-scope anchors; test deleted and foreign anchors. |
| ENH-01 — Add authorized order status events | Open | Medium · 5/10 | 2026-09-30 | Chat has a WebSocket, but orders do not stream assignments/status. Define participant-scoped committed events and reconnect reconciliation; until then, clients must refresh or poll. |
| SEC-15 — Check WebSocket token exposure at ingress | Needs validation | Medium · 5/10 | 2026-09-30 | Chat WebSockets put JWTs in query strings. Inspect actual proxy/APM logs; if exposed, use short-lived one-use tickets or an authorized subprotocol. |
| QUAL-01 — Correct the admin user rating display | Open | Medium · 4/10 | 2026-09-30 | User detail references removed `rating` fields. Remove the generic row or render a courier-only aggregate; test customer and courier pages. |
| SQL-01 — Measure large-query plans | Needs validation | Low · 3/10 | 2026-09-30 | No specific missing index or N+1 defect was confirmed. Measure order, inbox, and reconciliation query counts/plans on representative disposable PostgreSQL data before changing indexes. |
| CLEAN-01 — Correct the OTP lifetime comment | Open | Low · 2/10 | 2026-09-30 | The service comment says 180 seconds, while the configured default is 60. Refer to the configured TTL and verify the expiry test. |

## Release and external checks

These are validation tasks or intentional limitations from the review, separate from confirmed code fixes.

| Task | Status | Urgency | Last update | Note / next step |
| --- | --- | --- | --- | --- |
| Validate SMS/email/push vendor contracts | Needs validation | High · release dependent | 2026-09-30 | Real provider credentials and responses were not tested. Run sandbox contract checks before relying on OTP, receipts, or push in production. Earlier remediation explicitly excluded vendor-contract work. |
| Confirm exact-commit CI results | Needs validation | High · release dependent | 2026-09-30 | Confirm a passing CI run tied to the deployed master SHA, including PostgreSQL/Redis integration, coverage, dependency audit, OpenAPI, and image checks. |
| Implement and verify Dhamen before enabling production payments | Open | High · release dependent | 2026-09-30 | Production payments are intentionally disabled. Validate callback authentication, amounts, idempotency, and settlement with the provider before enabling them. |

No Docker was run on the user's machine for this tracker. PostgreSQL, Redis, vendor, ingress, and production behavior remain unverified where noted above.

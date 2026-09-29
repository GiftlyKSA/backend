# Giftly backend review — 2026-09-29

This is a fresh source and offline-test review after the 2026-09-29 fixes. It follows [AGENTS.md](../AGENTS.md) and uses OWASP Top 10:2025 as a security baseline. Scores are engineering priority from 1 to 10, not CVSS: High 8–10, Mid 4–7, Low 1–3. `UNCONFIRMED` means the needed runtime evidence was unavailable. The user/courier/wallet redesign in `docs/superpowers/specs/` remains a proposal, not implemented code.

Scope: authentication and token flows, OTP and rate limits, admin access and CRUD, orders/invoices/wallets, media ownership, chat/WebSockets, workers, provider adapters, queries, migrations, configuration, CI and API documentation. This is not a line-by-line proof or a zero-vulnerability claim. No Docker or production secrets/data were used. PostgreSQL, Redis, vendor contracts, deployment controls and representative query plans were unavailable locally.

## Global priority index

| Priority | ID | Category | Severity | Status | Impact if unresolved |
| --- | --- | --- | --- | --- | --- |
| 1 | INT-01 | Integration / release | High 8 | UNCONFIRMED | OTP or receipts may fail with real vendors |
| 2 | CI-02 | Quality / release | Mid 6 | Confirmed missing run evidence | Master changes lack verified CI evidence |
| 3 | TEST-01 | Quality / security | Mid 5 | Open | Generic admin CRUD can regress per table |
| 4 | PERF-03 | SQL performance | Mid 5 | Open | Deep admin pages can scan millions of rows |
| 5 | SEC-09 | Supply chain | Mid 4 | UNCONFIRMED | Historical GitHub alerts may differ from local audit |
| 6 | SQL-01 | SQL optimization | Low 3 | Suggested | Growing data may expose sort and ledger-scan cost |
| 7 | OPT-01 | Optimization | Low 3 | Open | Repeated S3 client setup adds per-object cost |
| 8 | MAINT-02 | Maintainability | Low 3 | Open | Mobile OpenAPI copy can drift |
| 9 | MAINT-03 | Security / auditability | Low 3 | Suggested | Admin audit records share the application DB trust domain |

## Security

### High

No confirmed high-severity exploit was established in inspected source. Deployment and vendor controls remain unverified.

### Mid

**SEC-09 — Dependency alert discrepancy — 4/10, UNCONFIRMED.** A previous GitHub alert count is historical; a prior local production-dependency scan reported no known vulnerabilities, but the GitHub alert API returned 401. A current advisory could be missed. Compare the locked graph against authorized GitHub alerts in CI, and upgrade only affected packages with compatibility checks. This improves supply-chain visibility without altering runtime behavior.

### Low

**MAINT-03 — Independent admin audit sink — 3/10, suggested.** `app/admin/router.py` enforces authentication, CSRF and step-up on sensitive actions, but database-local audit rows share the same administrative persistence domain. A privileged database actor could undermine forensic confidence. Forward sensitive events to a restricted external sink with retention and correlation IDs; verify delivery and redaction before relying on it.

### Fixed, pending live proof

| ID | Fix and system effect | Verification still needed |
| --- | --- | --- |
| SEC-07 (Mid 7) | `app/core/ws_connections.py` adds atomic Redis leases with per-account/global socket caps; `app/routers/chat.py` renews/releases them. Idle socket amplification is bounded across API workers. | Live Redis, multi-instance and crash cleanup tests. |
| SEC-08 (Mid 6) | `app/repositories/media_repository.py` caps outstanding grants by count/bytes under the owner lock; `app/services/media_cleanup_service.py` deletes abandoned objects in bounded batches. | Disposable PostgreSQL/S3 cleanup and race tests. |
| SEC-10 (Mid 6) | Weak explicit OTP signing keys fail startup. | Verify deployed settings without exposing secrets. |
| SEC-11 (Mid 6) | Production provider URLs require HTTPS at startup. | Verify actual vendor endpoints and TLS. |

## Performance and SQL optimization

### High

**PERF-02 — City push before order commit — 8/10, fixed offline.** `app/repositories/order_repository.py` now inserts a durable outbox row in the order transaction. `app/workers/order_notifications.py` sends bounded recipient pages after commit, outside DB transactions. Order creation no longer waits on the provider, and rolled-back orders do not notify couriers. Delivery is at least once: provider success followed by a failed cursor stamp may resend a page. Verify rollback, concurrency and throughput with PostgreSQL and the real push provider.

### Mid

**PERF-03 — Deep admin OFFSET — 5/10, open.** `app/admin/router.py` accepts page 100,000; `app/repositories/admin_read_repository.py` uses 50-row OFFSET pages, potentially skipping 4,999,950 rows. Authorized browsing can consume shared DB capacity. Use a stable keyset cursor while preserving relationship search and CRUD access. Test duplicate timestamps, deleted anchors and large-table plans.

**PERF-04 — Whole-table key rotation — 6/10, fixed offline.** `app/services/key_rotation_service.py` now locks and commits primary-key-ordered batches of 100. This bounds memory/lock time and permits resume after interruption. Only the rotation CLI and test call this service; it now owns commits on its supplied session. Test interruption and concurrent edits on disposable PostgreSQL before real key rotation. Old message keys must remain available.

### Low

**SQL-01 — Query/index alignment — 3/10, suggested.** Order radar, inbox and actor lists combine filtering and ordering; reconciliation aggregates a growing ledger. No missing-index incident was measured. Capture `EXPLAIN (ANALYZE, BUFFERS)` on representative disposable PostgreSQL data, then add only justified indexes or incremental reconciliation. Compare latency with write/storage cost.

**OPT-01 — Repeated S3 client setup — 3/10, open.** `app/integrations/storage/real.py` creates a client for each object operation. Sequential checks and cleanup pay repeated setup cost; impact is unmeasured. Reuse an appropriately scoped async client only if timing justifies it, with lifecycle/error tests.

## Scalability and reliability

### Mid

| ID | Fix and system effect | Verification still needed |
| --- | --- | --- |
| REL-05 (6) | `app/services/receipt_service.py` uses durable invoice claims and sends email outside DB locks; overlapping sweeps skip claimed rows. | PostgreSQL concurrency and vendor idempotency. Accepted send before failed stamp can repeat. |
| REL-06 (6) | `app/services/order_service.py` cancels issued invoices, expires payment reservations and releases promos in the order transaction. Pending financial state is not stranded. | PostgreSQL payment/cancellation race and lock-order tests. |
| REL-07 (6) | Chat conversation rows are locked before unread/latest-message mutations. | Two-session PostgreSQL race test. |
| REL-08 (6) | `app/core/redis.py` enforces three-second connect/read budgets and a bounded pool; Taskiq bounds pool acquisition while leaving its blocking queue read open. | Live Redis outage and worker queue behavior. |

## Quality, readability and maintainability

### Mid

**TEST-01 — Generic admin CRUD coverage — 5/10, open.** The dynamic browser and mutation path have authentication/CSRF checks, but not every mapped table and relationship has a database-backed create/edit/delete round trip. A model change could break one form at runtime. Add a disposable-PostgreSQL matrix for field types, FK selection, uniqueness, nullable clearing and protected writes. This catches regressions without changing product behavior.

**CI-02 — Master CI evidence — 6/10, confirmed missing run evidence.** On 2026-09-29, `gh run list --workflow CI --branch master` returned no runs, including for the pushed commit `6c939f3`. The workflow file is active and declares a master push trigger. A manual dispatch attempt returned HTTP 401, so this session could not run it. This is a release-verification gap, not proof that application code fails. An authorized repository maintainer should check Actions policy/permissions, trigger CI, and verify PostgreSQL/Redis integration, coverage, dependency audit, OpenAPI generation and build checks. Fixing the trigger would make future master changes verifiable.

### Low

**MAINT-02 — Mobile OpenAPI drift — 3/10, open.** The mobile screen/API map and backend OpenAPI are maintained separately. A contract change can silently break UI integration. Add a reproducible generated-schema comparison in CI and review intentional differences; no new routes are required.

### Earlier fixes rechecked at their source boundaries

QUAL-01/02 map invalid status/cursor and registration-token inputs to client errors; QUAL-03 includes safe exception diagnostics in structured logs; QUAL-04 uses an atomic device-token upsert; PERF-05 bounds refresh-token cleanup and indexes expiry; PERF-06 reduces admin session writes; PERF-07 closes owned receipt clients. Unit/offline evidence exists, with database/deployment proof still needed where applicable.

## Integration and release verification

### High

**INT-01 — Production SMS/email contracts — 8/10, UNCONFIRMED.** `app/integrations/sms/real.py` and `app/integrations/email/sndr_client.py` target vendor APIs, but this review has no authenticated live contract test. API, credential or template mismatch could block OTP delivery or paid-invoice receipts. Validate request/response mapping, timeout/retry behavior and redacted logs with vendor sandbox credentials before production activation. Keep fakes for local tests.

## Verification and limits

- Unit suite: 321 passed with dummy test settings and a writable pytest temporary directory. The Starlette/httpx deprecation warning is upstream.
- Combined Ruff lint/format and strict mypy were run; identified socket-patch issues were corrected. Re-run final gates on the staged tree.
- Alembic rendered revisions `0001` through `0005` as offline PostgreSQL SQL.
- Database-backed integration cases collected but skipped without PostgreSQL. A full-suite attempt was stopped after repeated database skips; it is not represented as a passing full suite.
- Redis Lua behavior, provider delivery, migration application, query plans, load, deployed settings and CI remain unverified locally. No Docker was run.

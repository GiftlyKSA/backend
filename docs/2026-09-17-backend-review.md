# Giftly backend review — 2026-09-29

This is a fresh source and offline-test review after the 2026-09-29 fixes. It follows [AGENTS.md](../AGENTS.md) and uses OWASP Top 10:2025 as a security baseline. Scores are engineering priority from 1 to 10, not CVSS: High 8–10, Mid 4–7, Low 1–3. `UNCONFIRMED` means the needed runtime evidence was unavailable. The user/courier/wallet redesign in `docs/superpowers/specs/` remains a proposal, not implemented code. A confirmed source behavior is not necessarily a demonstrated remote exploit.

Scope: authentication and token flows, OTP and rate limits, admin access and CRUD, orders/invoices/wallets, media ownership, chat/WebSockets, workers, provider adapters, queries, migrations, configuration, CI and API documentation. This is not a line-by-line proof or a zero-vulnerability claim. No Docker or production secrets/data were used. PostgreSQL, Redis, vendor contracts, deployment controls and representative query plans were unavailable locally.

## Global priority index

| Priority | ID | Category | Severity | Status | Impact if unresolved |
| --- | --- | --- | --- | --- | --- |
| 1 | SEC-16 | Security / financial integrity | High 9 | Confirmed, intentional capability | Raw admin financial edits bypass normal service invariants |
| 2 | SEC-13 | Security / resource exhaustion | High 8 | Confirmed, open | Actual body bytes can exceed the declared cap |
| 3 | INT-01 | Integration / release | High 8 | UNCONFIRMED; excluded by user | OTP or receipts may fail with real vendors |
| 4 | PERF-08 | Performance / database | Mid 7 | Confirmed, open | S3 latency can hold database transactions open |
| 5 | SEC-14 | Security / abuse prevention | Mid 7 | Confirmed, open | Redis failure disables ordinary HTTP throttling |
| 6 | REL-09 | Scalability / reliability | Mid 6 | Confirmed risk, open | Token churn can omit or repeat push recipients |
| 7 | CI-02 | Quality / release | Mid 6 | Confirmed missing run evidence | Master changes lack verified CI evidence |
| 8 | TEST-01 | Quality / security | Mid 5 | Partially mitigated | Some generic admin forms may regress per table |
| 9 | SEC-15 | Security / credential exposure | Mid 5 | UNCONFIRMED | WebSocket JWT URLs may enter upstream logs |
| 10 | SEC-09 | Supply chain | Mid 4 | UNCONFIRMED | Historical GitHub alerts may differ from local audit |
| 11 | OPT-02 | Optimization / operations | Mid 4 | Confirmed, open | Many ledger drifts can grow report and log output |
| 12 | MAINT-03 | Security / auditability | Mid 4 | Partially mitigated | External independent log retention is unverified |
| 13 | SQL-01 | SQL optimization | Low 3 | Suggested, UNCONFIRMED | Growing data may expose sort and ledger-scan cost |

## Security

### High

**SEC-16 — Raw admin table maintenance overrides financial guards — 9/10, confirmed, intentional.** Source: `app/admin/table_router.py:89-148`, `app/services/admin_table_service.py:81-160`, `app/repositories/admin_table_repository.py:151-187`, `app/migrations/versions/0002_schema_guards.py:18-85`. The user requested unrestricted authenticated CRUD. The browser now supports it and checks CSRF, current administrator session, and writes an audit row. The database maintenance scope deliberately overrides normal ledger and issued-invoice immutability triggers. **Impact:** a mistaken or compromised administrator can create financial inconsistencies despite the normal API invariants. **Minimal fix:** retain the requested capability but require a separately authorized, time-limited financial-maintenance grant, reason, and second approver for money and issued-invoice tables. **System impact:** ordinary table edits remain available; sensitive repairs gain a stronger trust boundary. **Verify:** deny such writes without the grant, test grant expiry and independent approval on disposable PostgreSQL, then reconcile changed accounts.

**SEC-12 — Production admin TOTP — 8/10, fixed offline.** Source: `app/core/config.py`, `app/services/admin_auth_service.py`, `app/admin/router.py`, and `tests/unit/test_admin_totp.py`. Production dashboard startup now requires a Base32 secret decoding to at least 20 bytes. Login verifies a six-digit code using the existing cryptography TOTP implementation and atomically claims its time step in Redis before creating a session; development login is unchanged. **Impact addressed:** a stolen password alone no longer establishes a production admin session. **Verify:** focused code, replay, and config tests passed; deployed authenticator setup, Redis outage behavior, and multi-instance login still need live validation.

**SEC-13 — Actual request bytes are not capped — 8/10, confirmed, open.** Source: `app/main.py:205-244`. The guard compares `Content-Length` to the cap and rejects explicit chunked transfer, but treats malformed lengths as zero, accepts other missing-length bodies, and never counts incoming bytes. **Impact:** a proxy or ASGI client that delivers more than declared may cause memory and parser exhaustion; PaaS exploitability is unverified. **Minimal fix:** enforce the byte cap in an ASGI receive wrapper while retaining early header rejection. **System impact:** a reliable cap with small per-chunk overhead. **Verify:** missing, malformed, understated, exact-limit, oversized, and streaming bodies in process and through the deployed proxy.

### Mid

**SEC-14 — Ordinary HTTP rate limiter fails open — 7/10, confirmed, open.** Source: `app/core/ratelimit.py:83-99`, `app/main.py:248-295`. Any Redis exception makes the ordinary HTTP limiter allow the request; the guarded WebSocket path separately fails closed. **Impact:** general API abuse and resource-exhaustion protection disappears during Redis failure. This does not claim OTP/admin login lose their independent guards. **Minimal fix:** fail closed on sensitive routes and define a bounded shared fallback or explicit 503 policy for ordinary traffic. **System impact:** preserves security through cache outages with an explicit availability tradeoff. **Verify:** Redis timeout/unavailable tests per route class and a multi-worker outage drill.

**SEC-15 — WebSocket JWT may reach upstream URL logs — 5/10, UNCONFIRMED.** Source: `app/routers/chat.py:180-185,352-365`. The browser authenticates WebSockets with `?token=...`; application JSON logging redacts token fields, but PaaS/proxy/observability logging was unavailable. **Impact:** a logged URL could expose a bearer token until expiration or revocation. **Minimal fix:** use a short-lived single-use WebSocket ticket or supported authorization subprotocol, and redact query strings at every ingress. **System impact:** reduces credential exposure but requires a client handshake update. **Verify:** inspect sanitized logs at all hops and test ticket replay/expiry if implemented.

**SEC-09 — Dependency alert discrepancy — 4/10, UNCONFIRMED.** Source: `uv.lock`, `.github/workflows/ci.yml`. A previous GitHub alert count is historical; a prior local production-dependency scan reported no known vulnerabilities, but the GitHub alert API returned HTTP 401 on 2026-09-29. **Impact:** a current advisory could be missed. **Minimal fix:** compare the locked graph against authorized GitHub alerts in CI and upgrade only affected packages with compatibility checks. **System impact:** better supply-chain visibility without speculative dependency churn. **Verify:** a saved CI audit for the current master SHA and an authorized alert review.

**MAINT-03 — Independent admin audit retention — 4/10, partially mitigated.** Source: `app/repositories/audit_repository.py:24-45`, `app/core/db.py:26-46`, `app/admin/deps.py:74-84`. Committed audit metadata is now mirrored to structured application logs in addition to the database row, without secret values. **Impact:** if logs are retained in the same trust domain, a privileged actor could undermine forensic evidence. **Minimal fix:** forward `giftly.audit` to a restricted immutable collector with retention and alerting. **System impact:** stronger independent evidence; application emission is in place. **Verify:** committed and rolled-back edits, redaction, collector receipt, access controls, and retention. External forwarding remains UNCONFIRMED.

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

**PERF-08 — Media requests hold database transactions across S3 calls — 7/10, confirmed, open.** Source: `app/services/media_service.py:44-78,84-137`, `app/repositories/media_repository.py:105-126`, `app/core/db.py:63-76`. `request_upload_url` issues a grant under a user `FOR UPDATE` lock and then awaits S3 URL presigning before the request transaction commits. Confirm/claim reads a grant and awaits S3 metadata and image-byte checks in the same transaction; multi-object attachment repeats this work. **Impact:** storage latency holds row locks, connections and snapshots, increasing tail latency and limiting throughput. **Minimal fix:** perform presigning before acquiring the quota lock or make grant issuance a short transaction; verify uploaded objects outside the claim transaction, then atomically recheck ownership, status and expected metadata. Bound S3 timeouts. **System impact:** shorter locks and transactions without weakening quotas or one-time claims. **Verify:** delayed/failing S3 fake, two-session PostgreSQL race, quota enforcement and connection-hold timing.

**PERF-03 — Deep admin OFFSET — 5/10, fixed offline.** Source: `app/repositories/admin_read_repository.py:92-163`, `app/admin/router.py:203-218`. Generic table pages now use stable UUID keyset cursors and bounded `LIMIT`, so deep browsing need not skip millions of rows. **Impact addressed:** lower work for deep pages. **Verify:** unit cursor tests; deleted anchors, duplicate timestamps and large-table plans on disposable PostgreSQL.

**PERF-04 — Whole-table key rotation — 6/10, fixed offline.** `app/services/key_rotation_service.py` now locks and commits primary-key-ordered batches of 100. This bounds memory/lock time and permits resume after interruption. Only the rotation CLI and test call this service; it now owns commits on its supplied session. Test interruption and concurrent edits on disposable PostgreSQL before real key rotation. Old message keys must remain available.

### Low

**SQL-01 — Query/index alignment — 3/10, suggested, UNCONFIRMED.** Source: `app/repositories/order_repository.py:173-236`, `app/repositories/chat_repository.py`, `app/repositories/wallet_repository.py:158-205`. Order radar and inbox combine filtering and sorting; reconciliation aggregates a growing ledger. No missing-index incident was measured. **Impact:** data growth may increase latency. **Minimal fix:** capture `EXPLAIN (ANALYZE, BUFFERS)` on representative disposable PostgreSQL before choosing indexes or incremental aggregation. **System impact:** targeted changes avoid needless write/storage cost. **Verify:** plans and timings under representative data and concurrency.

**OPT-01 — Repeated S3 client setup — 3/10, fixed offline.** Source: `app/integrations/storage/real.py:31-121`, `app/main.py:52-82`. Storage now shares one async client per lifecycle and drains in-flight operations before closing; standalone workers close owned clients. **Impact addressed:** removes repeated setup for each object. **Verify:** fake-client lifecycle/concurrency tests passed; actual S3 timing and shutdown remain unmeasured.

## Scalability and reliability

### Mid

**REL-09 — Random UUID recipient cursor can miss new devices — 6/10, confirmed risk, open.** Source: `app/repositories/order_notification_repository.py:78-97`, `app/services/order_notification_service.py:31-57`. Recipient pages use `DeviceToken.id > cursor`; random UUIDs do not follow insertion time. A token inserted or replaced while a multi-page delivery runs may sort behind the cursor; retry after provider success can repeat a page. **Impact:** some couriers miss a city push while others get duplicates. **Minimal fix:** snapshot eligibility or use a monotonic creation key and high-water mark per outbox item. **System impact:** deterministic coverage at the cost of storage or schema change. **Verify:** concurrent insertion/replacement/revocation, multi-page delivery, and lease-expiry cases on disposable PostgreSQL.

**OPT-02 — Reconciliation output is unbounded in discrepancy count — 4/10, confirmed, open.** Source: `app/repositories/wallet_repository.py:158-205`, `app/services/money_service.py:489-522`, `app/workers/reconciliation.py:26-47`. SQL avoids loading healthy rows, but appends every discrepant wallet/correlation to a Python report and logs each drift. **Impact:** widespread drift can grow memory and trigger a log/alert burst during recovery. **Minimal fix:** retain exact counts with a bounded sample and separate paginated forensic export. **System impact:** bounded worker output without hiding incident scale. **Verify:** many seeded discrepancies with correct totals and measured memory/output bounds.

| ID | Fix and system effect | Verification still needed |
| --- | --- | --- |
| REL-05 (6) | `app/services/receipt_service.py` uses durable invoice claims and sends email outside DB locks; overlapping sweeps skip claimed rows. | PostgreSQL concurrency and vendor idempotency. Accepted send before failed stamp can repeat. |
| REL-06 (6) | `app/services/order_service.py` cancels issued invoices, expires payment reservations and releases promos in the order transaction. Pending financial state is not stranded. | PostgreSQL payment/cancellation race and lock-order tests. |
| REL-07 (6) | Chat conversation rows are locked before unread/latest-message mutations. | Two-session PostgreSQL race test. |
| REL-08 (6) | `app/core/redis.py` enforces three-second connect/read budgets and a bounded pool; Taskiq bounds pool acquisition while leaving its blocking queue read open. | Live Redis outage and worker queue behavior. |

## Quality, readability and maintainability

### Mid

**TEST-01 — Generic admin CRUD coverage — 5/10, partially mitigated.** Source: `tests/integration/test_admin_table_crud.py:48-233`, `tests/unit/test_admin_navigation.py:13-66`, `app/admin/table_router.py:68-148`. New tests cover every table form and representative create/edit/delete, FK choices, nullable clear, uniqueness rejection, audit, maintenance scope, and credential invalidation. They do not independently round-trip every table/field type; local integration tests were skipped without PostgreSQL. **Impact:** a schema change may break a less-used form. **Minimal fix:** expand a disposable-PostgreSQL matrix for required/nullable/unique/FK/encrypted/financial cases. **System impact:** earlier detection without product behavior change. **Verify:** run the matrix in CI, including rejected writes and rollback.

**CI-02 — Master CI evidence — 6/10, confirmed missing run evidence.** Source: `.github/workflows/ci.yml`. On 2026-09-29, `gh run list --workflow CI --branch master` returned no runs, including for pushed commit `6c939f3`; the workflow is active and declares a master push trigger. Manual dispatch and Actions-permission requests returned HTTP 401. **Impact:** PostgreSQL/Redis integration, coverage, dependency audit, OpenAPI and build gates have no remote proof. **Minimal fix:** an authorized maintainer checks Actions permissions/policy, triggers CI for current master, and repairs the trigger if needed. **System impact:** future releases gain a verifiable gate; missing evidence is not proof of application failure. **Verify:** save a passing run tied to the exact master SHA.

### Low

**MAINT-02 — Mobile OpenAPI drift — 3/10, fixed offline.** Source: `tests/unit/test_mobile_openapi_drift.py:16-45`, `docs/mobile-openapi.json`, `docs/MOBILE-API-INTEGRATION.md`. A new unit check compares all 44 non-admin operations' path/method, identifiers, parameters, request bodies, responses and shared schemas with generated OpenAPI. The `OrderSummary` description was aligned. **Impact addressed:** accidental mobile contract changes fail a test. **Verify:** focused test passed; confirm master CI runs the unit suite.

### Earlier fixes rechecked at their source boundaries

QUAL-01/02 map invalid status/cursor and registration-token inputs to client errors; QUAL-03 includes safe exception diagnostics in structured logs; QUAL-04 uses an atomic device-token upsert; PERF-05 bounds refresh-token cleanup and indexes expiry; PERF-06 reduces admin session writes; PERF-07 closes owned receipt clients. Unit/offline evidence exists, with database/deployment proof still needed where applicable.

The dedicated invoice and promo pages now link to generic table Add/Edit/Delete, and the navigation menu links every application section to those actions (`app/admin/templates/base.html:38-55`, `app/admin/templates/invoices.html:4-10`, `app/admin/templates/promos.html:4-11`). All application tables appear in the generic catalog. Routes use administrator authentication and CSRF. Live database round trips remain unverified locally. This capability has the financial integrity boundary described in SEC-16.

## Readability and naming/style

The changed admin CRUD, storage, audit, worker, and OpenAPI-check code was inspected for focused functions, explicit names, typed boundaries, and comments limited to non-obvious invariants. Ruff formatting and lint and strict mypy passed for the reviewed tree. No separate, evidenced readability or naming defect was found that warrants an additional finding; broad restyling would add risk without a demonstrated behavior or maintenance benefit. This is a scoped review result, not a line-by-line style certification.

## Integration and release verification

### High

**INT-01 — Production SMS/email contracts — 8/10, UNCONFIRMED; explicitly excluded from remediation.** Source: `app/integrations/sms/real.py`, `app/integrations/email/sndr_client.py`. Local fakes do not establish real credential, payload, template, or response compatibility. **Impact:** OTP delivery or paid-invoice receipts may fail. **Minimal fix:** verify mapping, timeout/retry behavior and redacted logs in vendor sandboxes. **System impact:** production release confidence without API changes. **Verify:** authenticated vendor contract runs; no vendor fix is claimed here.

## Verification and limits

- Unit suite: 368 passed with dummy test settings and a writable pytest temporary directory. The Starlette/httpx deprecation warning is upstream. This is not a full integration-suite pass.
- Combined Ruff lint/format and strict mypy passed on the current edits, including S3 lifecycle, worker cleanup, audit logging, admin navigation/cursor, and mobile OpenAPI checks. Re-run final gates on the staged tree.
- Alembic rendered revisions `0001` through `0005` as offline PostgreSQL SQL.
- Database-backed integration cases collected but skipped without PostgreSQL. A full-suite attempt was stopped after repeated database skips; it is not represented as a passing full suite.
- Redis Lua behavior, provider delivery, migration application, query plans, load, deployed settings, independent audit-log retention, and CI remain unverified locally. No Docker was run. This review does not claim zero vulnerabilities.

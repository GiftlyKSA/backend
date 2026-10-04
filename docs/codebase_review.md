# Giftly codebase review

**Review date:** 2026-10-04 · **Status:** current review after prior-finding fixes.

[Project documentation](documentation.md) · [API](api.md) · [Outstanding tasks](tasks.md)

## Executive assessment

The previous confirmed HTTP, media-transaction, notification paging, migration ownership,
profile and misleading-admin-display findings were addressed with targeted patches and
regression checks. The new review still finds financial correctness, authentication,
delivery and resource-control issues. This is **not** a vulnerability-free certification
or proof that production integration and capacity are ready.

Security takes precedence, followed by application/SQL performance, memory/CPU efficiency
and scalable maintainability. Severity scores are this review's prioritization estimates
(0–10), not CVSS. **High**: 7–10; **Medium**: 4–6; **Low**: 1–3. Findings within each
category are ordered by severity and impact. **Open** requires a change; **Needs validation**
requires measurements/external proof; **Accepted risk** reflects an explicit user decision.

## Global priority index

| Priority | ID | Category / finding | Severity | Status |
| --- | --- | --- | --- | --- |
| P1 | FIN-01 | Discount rounding creates negative line discounts | High 7 | Open; reproduced |
| P1 | FIN-02 | Wallet payment can consume another reservation | High 7 | Open; locking gap confirmed |
| P1 | SEC-19 | Production settings accept published development keys | High 7 | Open; configuration gap |
| P2 | SEC-18 | Refresh reuse leaves existing access credentials valid | Medium 6 | Open; reproduced |
| P2 | PERF-10 | Cancelled decoding releases admission too early | Medium 6 | Open; reproduced |
| P2 | REL-12 | Missing-email receipt backlog starves deliverable receipts | Medium 6 | Open; source confirmed |
| P2 | PERF-11 | Unlimited device registration creates unbounded push work | Medium 5 | Open; growth path confirmed |
| P2 | REL-13 | Committed chat has no durable delivery/idempotent retry | Medium 5 | Open; known limitation |
| P2 | API-02 | Malformed inbox cursor silently restarts pagination | Medium 4 | Open; source confirmed |
| P2 | REL-14 | Unexpected errors lose correlation/security headers | Medium 4 | Open; reproduced |
| P3 | QUAL-02 | WebSocket text bypasses REST input schema | Low 3 | Open; boundary reproduced |
| P3 | QUAL-03 | Source comments reference removed design documents | Low 2 | Open; source confirmed |
| Gate | SQL-01 | Representative query/pool/resource capacity proof | Medium 5 | Needs validation |
| Gate | PERF-09 | Audit retention/export and write amplification | Medium 5 | Needs validation |
| Gate | SEC-15 | Query-token and ingress logging exposure | Medium 5 | Needs validation |
| Gate | SEC-20 | Reconcile GitHub dependency alerts with current lockfile | Medium 5 | Needs validation; alert details unavailable |
| Gate | OPS-01 | New migrations, decoder and service startup on deployment | Medium 5 | Needs validation |
| Gate | INT-01 | Live vendor contracts and Dhamen readiness | Medium 5 | External scope; needs validation |
| Accepted | SEC-16 | Privileged maintenance bypasses financial immutability | High 9 | User-approved unrestricted CRUD |
| Accepted | SEC-17 | Privileged admin can alter/delete action audit history | High 9 | User-approved unrestricted CRUD |

## 1. Security and authentication

### High

#### SEC-19 — Known testing keys are accepted by production configuration

- **Status / score:** Open, High 7/10; source-confirmed startup validation gap. Real deployment keys were not inspected.
- **Evidence:** `app/core/config.py:190` checks encryption lengths/key versions; JWT validation checks length; production interlocks check nonempty provider configuration. `.env.example` publishes an all-zero AES key and predictable local JWT/OTP/session keys. None is rejected merely for matching a public testing value.
- **Trigger / impact:** copying testing secrets into otherwise valid production configuration can permit token forgery or decryptable personal/chat data. It is unsafe to infer production key strength from a successful Settings construction.
- **Minimal fix:** reject the exact published test values and clearly patterned placeholders in production, preserve development/test behavior, and enforce externally generated keys through deployment secret handling. Do not claim entropy can be fully measured from string length.
- **Expected impact:** unsafe deployments fail before traffic; no API contract change. Existing unsafe environments require key rotation and a reviewed ciphertext/session migration.
- **Verify:** negative boot tests for each published secret; valid randomized production settings; rotation/rollback on disposable encrypted data. Never silently replace encryption keys.

### Medium

#### SEC-18 — Refresh replay detection does not revoke minted access tokens

- **Status / score:** Open, Medium 6/10; reproduced with offline collaborators.
- **Evidence:** `app/services/auth_service.py:234` revokes only `row.family_id` and commits on replay. `app/repositories/auth_repository.py:152` does not change `users.auth_version`; `validate_access_claims` relies on that version and JTI denial.
- **Trigger / impact:** after reuse detection, an already issued access JWT still passes the current-account check until expiry. A detected compromised session can retain HTTP/WebSocket access.
- **Minimal fix:** invalidate account credentials in the committed replay transaction, or add family-bound access revocation. Preserve the lock order and explicit commit that makes denial durable.
- **Expected impact:** compromised credentials lose access immediately; account-wide invalidation also signs out other devices and must be documented.
- **Verify:** replay a rotated token and reject earlier/newer access tokens, refresh tokens and socket actions; assert revocation survives the 401 and concurrent rotation.

#### SEC-15 — Chat query-token exposure depends on unverified ingress logging

- **Status / score:** Needs validation, Medium 5/10; legacy design present, credential capture UNCONFIRMED.
- **Evidence:** `app/routers/chat.py:536` uses the `token` query parameter. The order stream also supports Authorization/subprotocol methods. No production ingress configuration was available for inspection.
- **Impact:** a proxy/access/error log that records query strings can retain live access credentials. Backend redaction cannot undo logs written by upstream infrastructure.
- **Minimal fix:** verify query/header redaction and migrate chat to a short-lived socket ticket or supported header/subprotocol authentication while preserving existing clients during rollout.
- **Expected impact:** safer credential transport; a ticket changes client connection sequencing and needs bounded expiry/replay controls.
- **Verify:** controlled fake-token probes at every logging boundary, ticket expiry/reuse tests and compatibility checks. Never use real tokens for log inspection.

### Supply-chain validation

#### SEC-20 — GitHub dependency-alert disposition remains unconfirmed

- **Status / score:** Needs validation, Medium 5/10; alert presence is confirmed, current package applicability is UNCONFIRMED.
- **Evidence:** the master push reported 20 dependency alerts (one Critical, ten High, nine Moderate). A read-only request for alert package/manifest metadata returned HTTP 401. The separate current locked-production audit reported no known advisories after the targeted updates.
- **Impact:** the scan cannot establish whether remaining alerts concern another manifest, stale dependency metadata or an uncovered deployment graph. The push warning must not be described as resolved or automatically dismissed as stale.
- **Minimal action:** an authorized repository security maintainer should inspect each alert against the current master lockfile, relevant environment and upstream advisory, resolve applicable versions and document disposition with evidence.
- **Expected impact:** reliable supply-chain release evidence; no automatic alert suppression or unrelated upgrades.
- **Verify:** current-master graph scan, package/manifest reconciliation and exact-commit CI audit. System/container packages need their own scan.

### Accepted High risks

#### SEC-16 — Financial maintenance bypass

- **Status / score:** Accepted risk, High 9/10. The user explicitly retained unrestricted authorized admin CRUD.
- **Evidence:** `app/repositories/admin_table_repository.py:184`, `app/services/admin_table_service.py:109`, and `app/migrations/versions/0002_schema_guards.py:35` permit authenticated maintenance to bypass normal posted-record guards.
- **Impact:** an authorized malicious, compromised or mistaken administrator can alter financial history and undermine balance/settlement invariants. Authentication/CSRF does not prove an accounting change is valid.
- **Mitigation / system impact:** preserve requested CRUD; restrict admin credentials operationally, require reviewed financial maintenance and independent reconciliation/backup recovery. An immutable accounting design would require a new user decision.
- **Verify:** PostgreSQL authorized/unauthorized maintenance boundaries and deliberate corruption detection by reconciliation on disposable data.

#### SEC-17 — Editable/deletable audit history

- **Status / score:** Accepted risk, High 9/10; explicit user decision.
- **Evidence:** audit logs remain in `Base.metadata` and generic admin table CRUD. The action-only trigger deliberately avoids recursively auditing audit-log mutations.
- **Impact:** a privileged actor can erase or alter the record used for investigation. The same database is not an independent tamper-evident archive.
- **Mitigation / system impact:** preserve CRUD; externally archive attributable audit changes and verify restore/retention access controls. Actual external export is UNCONFIRMED.
- **Verify:** independent archive survives local edits/deletion and access boundaries cannot be bypassed with a non-admin account.

## 2. Financial correctness and concurrency

### High

#### FIN-01 — Rounding residue can produce negative item discounts

- **Status / score:** Open, High 7/10; reproduced without external services.
- **Evidence:** `app/core/pricing.py:209` rounds each proportional share, then assigns the entire residual to one last component. With twenty SAR0.01 items, a 50% promo and zero courier fee, the total discount is SAR0.10 but the last line discount becomes **−0.09**, taxable value **0.10**, and VAT **0.02**.
- **Impact:** valid small-price invoices contain nonsensical negative discounts and incorrect item VAT. Arithmetic-only invoice-item constraints permit the outcome.
- **Minimal fix:** deterministic bounded largest-remainder allocation with every share between zero and its component net, plus persisted nonnegative/bounded discount checks after reviewing old data.
- **Expected impact:** correct cent allocation and tax; some edge-case totals change legitimately. Existing issued/paid invoices need separate reviewed remediation, not silent rewriting.
- **Verify:** tiny prices, twenty items, mixed tax rates, fixed/percent/full discounts, zero fees and exact sum invariants; PostgreSQL persistence and invoice revision behavior.

#### FIN-02 — Available funds are read before wallet locking

- **Status / score:** Open, High 7/10; source-confirmed locking gap. PostgreSQL concurrent reproduction remains UNCONFIRMED.
- **Evidence:** `app/services/payment_service.py:235` reads available balance before escrow funding takes wallet locks. `app/services/money_service.py:130` debits balance without enforcing remaining holds; `app/models/tables.py:1013` requires nonnegative balance/held balance separately.
- **Trigger / impact:** transaction A reads available100; B reserves80; A later debits80 for another invoice, leaving balance20/held80. Another invoice's reserved funds can be consumed, causing later settlement failures.
- **Minimal fix:** reservation-aware debit validation against freshly locked wallet state, with consistent lock ordering and correct distinction between consuming this operation's hold and unrelated holds. Consider a reviewed balance-versus-held constraint/backfill.
- **Expected impact:** concurrent insufficiency becomes a stable conflict instead of an invalid financial state; careless earlier locks can introduce deadlocks, so avoid an isolated lock-order change.
- **Verify:** two-session barrier regression for distinct invoices sharing one wallet; mixed gateway/wallet paths, retries, cancellation, withdrawal and ledger invariants.

## 3. Performance, SQL, memory and CPU

### Medium

#### PERF-10 — Cancelled image validation releases admission before its thread finishes

- **Status / score:** Open, Medium 6/10; cancellation reproduced locally.
- **Evidence:** `app/services/chat_media_service.py:156` controls leases; line197 runs Pillow validation through `asyncio.to_thread`. Probe results: `decoder_running_after_request_cancel=True`, `decoder_lease_released=True`.
- **Impact:** repeated cancelled requests can exceed the intended two global decoding slots while background threads continue using CPU/memory. Per-file limits alone do not bound this accumulation.
- **Minimal fix:** retain admission until actual decoder completion during cancellation; prefer a terminable process boundary for hard deadlines. Keep cancellation-safe cleanup and finite lease renewal/fencing.
- **Expected impact:** bounded work under disconnect/timeout; process isolation can add startup cost and requires resource measurements.
- **Verify:** blocking-decoder cancellation regression, repeated aborted sends, bounded active decoders, timeout cleanup and measured peak memory/CPU on deployment hardware.

#### PERF-11 — Device registrations and push fanout are unbounded

- **Status / score:** Open, Medium 5/10; confirmed growing query/work path, production cost unmeasured.
- **Evidence:** `app/repositories/device_token_repository.py:28,51` imposes no per-user quota/expiry and materializes all device tokens; `app/services/notification_service.py:28` sends the resulting batches sequentially.
- **Impact:** a valid account can grow persistent token rows, memory and notification latency/cost. HTTP throttling slows growth but does not cap it.
- **Minimal fix:** concurrent-safe active-device quota, invalid/stale token cleanup, bounded recipient pages and bounded delivery concurrency. Preserve multi-device use and ownership checks.
- **Expected impact:** stable memory and delivery cost; legitimate old devices may need re-registration under a documented expiry policy.
- **Verify:** quota races, token reassignment/revocation, cleanup, fixed-size SQL results and delivery latency across increasing account sizes.

#### SQL-01 — Query plans, pool budget and snapshot costs lack representative proof

- **Status / score:** Needs validation, Medium 5/10; not a confirmed missing-index or N+1 defect.
- **Evidence:** inspected list queries, joins, keyset anchors, admin labels and worker pages; no disposable PostgreSQL/load environment was available. Snapshot `INSERT SELECT` and its row audit trigger scale with eligible token count.
- **Impact:** unmeasured sort/scan/lock/pool costs can dominate latency as the dataset grows; three default API pools allow 45 connections before other processes.
- **Minimal action:** capture query counts and `EXPLAIN (ANALYZE, BUFFERS)` on disposable representative data; measure pool waits, snapshot/audit writes and media peak RSS/CPU before choosing indexes or concurrency.
- **Expected impact:** evidence-based capacity limits; indexes have storage/write costs and should not be added speculatively.
- **Verify:** fixed query counts as list size grows, indexed keysets, bounded lock duration, pool headroom and agreed p95 latency/memory budgets.

#### PERF-09 — Action audit storage needs a retention/export policy

- **Status / score:** Needs validation, Medium 5/10; row growth confirmed, production pressure unmeasured.
- **Evidence:** migration `0009_action_only_audit` records committed changes; migration0016 adds recipient-row activity. There is no implemented externally approved archival/retention policy.
- **Impact:** audit/index/backup growth and recipient write amplification can consume database I/O/storage. Deleting activity without policy would also undermine traceability.
- **Minimal action:** agree retention requirements, independent archive/access controls and measured capacity alerts; then implement bounded archival and verified restoration.
- **Expected impact:** predictable storage with preserved investigation evidence; never silently purge history to improve performance.
- **Verify:** archive replay/restore, incremental paging, index/storage growth and user/admin/system attribution after retention.

## 4. Reliability and API behavior

### Medium

#### REL-12 — Missing-email invoices can starve deliverable receipts

- **Status / score:** Open, Medium 6/10; source confirmed.
- **Evidence:** `app/repositories/invoice_repository.py:228` selects oldest100 paid/unsent invoices; `app/services/receipt_service.py:73` defers claims when optional customer email is absent. Claims expire before the next five-minute sweep.
- **Impact:** a backlog of 100 older no-email invoices keeps filling every sweep, preventing newer valid recipients from receiving paid-invoice PDFs.
- **Minimal fix:** select reachable-email candidates or persist a deferred state with retry when an email is later provided; preserve ownership, stable idempotency and provider retry semantics.
- **Expected impact:** fair progress for deliverable receipts without falsely marking missing-email invoices sent.
- **Verify:** 100 no-email invoices followed by an emailable one, later-added email, retries, overlapping sweeps and claim-expiry races.

#### REL-13 — Live chat remains best-effort without durable retries

- **Status / score:** Open, Medium 5/10; limitation retained after the postcommit-error fix.
- **Evidence:** `app/routers/chat.py:198` isolates notification failures; Redis Pub/Sub has no durable event retention and send schemas have no client idempotency key.
- **Impact:** disconnect/delivery failure can hide committed messages until history reconciliation; an uncertain client retry can create duplicate messages.
- **Minimal fix:** persisted idempotent client-send identity and a bounded outbox/retry path, with REST history reconciliation and delivery sequence semantics.
- **Expected impact:** stronger recovery at the cost of extra indexed rows and worker throughput; do not promise exactly-once network delivery.
- **Verify:** committed-send timeout, duplicate retries, Redis outage, reconnect replay, ownership and worker duplicate delivery.

#### API-02 — Invalid inbox cursors restart the first page

- **Status / score:** Open, Medium 4/10; source confirmed.
- **Evidence:** `app/routers/chat.py:587` returns `None` for nonempty malformed input instead of rejecting it. Order/wallet anchors now reject invalid scope.
- **Impact:** clients see duplicate inbox pages and hidden synchronization errors; valid-looking malformed inputs do not receive a consistent client error.
- **Minimal fix:** reject malformed nonempty cursors with a stable documented error; retain the empty-first-page contract and valid timestamp/UUID keyset behavior.
- **Expected impact:** invalid input becomes an explicit failure; compliant clients retain normal pagination.
- **Verify:** malformed separator/time/UUID, empty cursor, valid next page and participant scoping.

#### REL-14 — Unexpected500 responses lose request correlation and security headers

- **Status / score:** Open, Medium 4/10; synthetic route exception reproduced.
- **Evidence:** `app/core/middleware.py:47,122` resets context before the outer server exception handler renders. Probe returned request_id `-`, no `X-Request-ID` and no `X-Content-Type-Options`.
- **Impact:** error investigation loses correlation, and failed responses do not receive the same protective headers as ordinary responses.
- **Minimal fix:** retain correlation in request state and handle/stamp unexpected errors inside a protected ASGI boundary; avoid leaking exception text.
- **Expected impact:** consistent failure observability/header policy without exposing internals.
- **Verify:** exception before/during routing, safe500 envelope, request ID, CORS/security headers and logging redaction.

## 5. Quality, readability and maintainability

### Low

#### QUAL-02 — WebSocket text input differs from REST validation

- **Status / score:** Open, Low 3/10; boundary calls reproduced.
- **Evidence:** `app/routers/chat.py:519` string-coerces null/dictionaries and accepts4001-character strings inside the frame limit; `app/schemas/chat.py:12` rejects these inputs.
- **Impact:** inconsistent validation admits surprising encrypted content and complicates client behavior/testing. Safe literal rendering is still required; this alone is not proof of executable injection.
- **Minimal fix:** validate text frames with the shared message schema and reject invalid values before persistence.
- **Expected impact:** one typed boundary for both transports; malformed legacy socket clients receive an explicit failure.
- **Verify:** null/object/oversized text, valid literal code text, rate limits and REST/socket parity.

#### QUAL-03 — Source docstrings reference removed design records

- **Status / score:** Open, Low 2/10; source confirmed.
- **Evidence:** `app/models/tables.py:87` references deleted `DECISIONS.md`; scattered `SPEC SECTION` comments have no maintained linked specification in this repository.
- **Impact:** stale references make maintenance/review harder and may imply requirements no longer authoritative.
- **Minimal fix:** change misleading references only while touching relevant modules; link current documentation or state the invariant plainly. Avoid broad formatting churn.
- **Expected impact:** clearer maintenance with no runtime change.
- **Verify:** repository reference scan, readable affected docstrings and unchanged runtime contracts.

No additional confirmed naming/style defect or N+1 issue was manufactured to populate a
category. SQL/runtime measurements remain necessary before asserting broad optimization.

## 6. External release validation

| ID | Status / score | Evidence, impact, next action and verification |
| --- | --- | --- |
| OPS-01 | Needs validation, Medium5 | New migrations and prepared-claim races only received offline/local checks. Apply upgrade/downgrade on disposable PostgreSQL; exercise decoder image, three-worker startup, Redis outages and worker/scheduler operation. Failure here can prevent deployment or invalidate concurrency assumptions. |
| INT-01 | Needs validation, Medium5; outside prior vendor-fix scope | SMS/email/push payloads and provider acceptance are unproven; Dhamen production payments intentionally return503. Verify sandbox contracts/authentication/timeouts/idempotency and controlled delivery before enabling production flows. No production credentials/data were used. |

## 7. Disposition of previous review findings

| Previous ID | Current disposition | Evidence / scope |
| --- | --- | --- |
| SEC-13 | Fixed locally | Cumulative ASGI body limit before parsing/dependency side effects; oversized-byte regressions. |
| SEC-14 | Fixed locally | HTTP limiter dependency failure returns503; health/preflight remain exempt. |
| API-01 | Fixed locally | Missing/foreign/out-of-filter order and wallet anchors return identical404; scoped SQL regressions. Moving status-filter anchors may invalidate a page; clients refresh. |
| PERF-08 | Fixed for production request flows | S3 waits before write locks; typed scoped prepared metadata, reauthentication and locked claim rechecks. Direct internal calls retain validation behavior. PostgreSQL race proof remains OPS-01. |
| REL-10 (old chat error) | Fixed locally; durable-delivery residual REL-13 | Committed sends retain success when fanout/push/cleanup fails; authenticated socket sender fallback. |
| REL-09 | Fixed locally | First-claim recipient snapshot, eligible scoped pages, fenced completion cleanup and migration0016. PostgreSQL proof remains OPS-01. |
| DEP-01 | Fixed configuration | Compose app services wait for the migration service and bypass inherited migration entrypoints; normal PaaS gate retained. |
| DATA-01 | Implemented | Optional typed gender; DELETED/reason fields, migration0017 and access denial tests. |
| DATA-02 | Implemented per latest clarified requirement | Encrypted numbers retained; fingerprints, index, pepper and duplicate checks removed through forward0018. Applied migration history preserved. |
| QUAL-01 | Fixed | Generic user detail no longer renders removed customer rating fields. |
| CLEAN-01 | Fixed | Obsolete180-second OTP comment removed; configured default remains60. |
| REL-11 | Superseded | Transactional PostgreSQL action auditing already replaced HTTP request audit writes; independent archival remains PERF-09/SEC-17. |
| ENH-01 | Superseded | Participant order WebSocket and snapshots already implemented; durable replay is a limitation, not a missing route. |
| SEC-09 | Dependency graph updated and re-audited | Targeted PyJWT/urllib3 updates; no other package changed. Results below. |
| SEC-16 / SEC-17 | Accepted High risks | Explicit user instruction preserves unrestricted admin CRUD; not reported fixed. |
| SQL-01 / PERF-09 / SEC-15 / INT-01 | Reassessed above | Unverified operational risks kept explicit; no fabricated live proof. |

## 8. Review method and verification record

Inspected HTTP/WebSocket routers, auth/JWT/OTP/admin, schemas, services/repositories,
financial state/locks/pricing, private media/decoder/cleanup, all worker families,
configuration/pools, models/migrations, Compose/entrypoint, locked dependencies and CI.
Independent change review found no additional confirmed introduced High/Medium defect.

- New regression failures were observed before fixes for body/rate/cursor, media/delivery and profile boundaries.
- Ruff lint/format and strict mypy results are recorded after the final combined gate below.
- Offline Alembic upgrade0015→0018 and downgrade0018→0015 SQL generation passed. No database was migrated.
- Dependency audit on the full locked production graph initially reported16 advisories in PyJWT2.13.0 and urllib3 2.7.0. Targeted resolution changed only PyJWT→2.15.1 and urllib3→2.8.0. The repeated `pip-audit2.10.1 --strict --no-deps --disable-pip` scan reported **no known vulnerabilities** on 2026-10-04. This excludes system/image packages and undisclosed flaws.
- Upstream sources checked 2026-10-04: [PyJWT PEM guard advisory](https://github.com/jpadilla/pyjwt/security/advisories/GHSA-ffc3-869f-jxw9), [PyJWT release history](https://github.com/jpadilla/pyjwt/releases), [urllib3 2.8.0](https://github.com/urllib3/urllib3/releases/tag/2.8.0). The application already pins one allowed JWT algorithm; upstream advisory presence is not proof of exploitability in this deployment.
- Read-only remote CI inspection found successful older runs, latest returned SHA3352467 ([run](https://github.com/GiftlyKSA/backend/actions/runs/37133529592)). These do not validate this change or current source SHA.
- Push verification confirmed master commit `72e08ba`, with local/origin hashes matching. The remote dependency warning persisted; alert details returned HTTP 401, recorded as SEC-20. This does not contradict the separately scoped clean production-lockfile scan or establish that the remote alerts are stale.
- Final aggregate gate: `uv run --locked pytest -n 4 -o addopts="" -q -p no:cacheprovider --basetemp <fresh temporary directory>` finished with **668 passed, 198 skipped, four existing Starlette/httpx deprecation warnings**. The unavailable PostgreSQL/Redis and native decoder checks remain skipped. Ruff lint/format, strict mypy (184 source files), both pre-commit stages, mobile OpenAPI drift, local documentation links and `git diff --check` passed.

No Docker, live PostgreSQL/Redis, production repair, secrets inspection or vendor delivery
was performed. Native decoder availability, database concurrency/query plans, actual
deployment settings, independent backups/audit export and production load remain
UNCONFIRMED. New tasks contain only outstanding work from this review.

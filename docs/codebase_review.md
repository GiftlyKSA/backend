# Giftly codebase review

## Scoped mobile-read review — 2026-10-07

Scope: invoice listing, wallet statement/history date filters, order-media reads and
their new account eligibility guard. This is not a new full-codebase security certification.

| ID | Category | Severity / score | Status | Evidence / impact | Fix / verification |
| --- | --- | --- | --- | --- | --- |
| MOB-R1 | Reliability / validation | Medium (5/10) | Fixed | reporting_dates.py converted the first calendar day before adding the exclusive day, causing an OverflowError/500 for a valid upper-only bound. | Advance the local day before UTC conversion; reproduced failing regression now passes. |
| MOB-R2 | Performance / SQL verification | Unverified risk | Pending disposable DB | Bounded page queries and SQL sums compiled; real invoice revision/ledger aggregation plans and PostgreSQL integration were not available locally. | Run new integration tests and EXPLAIN on representative data; existing ownership/date indexes reused, no speculative migration. |

Independent read-only review found no other concrete defects in ownership/cursor scope,
latest-visible invoice revisions, money serialization, signed media URLs or the final
active-account guard. New reads use private,no-store; collections are bounded and do
not perform per-row relationship queries. Statement page/totals share one SQL snapshot,
but later pages refresh totals if ledger state changes. Existing unrestricted financial
and audit admin CRUD remains an accepted integrity risk; this change does not claim
immutable statements. Deployment and real-provider behavior remain UNCONFIRMED.

Verification: focused regression tests passed, including HTTP schema/role boundaries
and inactive/deleted/unverified-account cases. Ruff/format/strict mypy passed on the
isolated release checkout. Final full suite: 740 passed, 204 skipped, four Starlette deprecation warnings.
Both pre-commit and pre-push hook suites passed.
No Docker, real-provider request or production database operation ran.


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
- **Evidence:** an initial master push reported 20 alerts; the next reported four (three High, one Moderate). Alert metadata access returned HTTP 401. The production graph scan was clear; an expanded production/development scan identified four distinct virtualenv advisories (duplicated by platform markers), resolved by locking the minimal patched version21.7.13. The repeated complete-graph scan is now clear.
- **Impact:** the local scan cannot prove GitHub's alert disposition or cover another deployment/image graph. The earlier warning must not be automatically dismissed as stale; current remote refresh needs authorized confirmation.
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
| SEC-09 | Dependency graph updated and re-audited | Targeted PyJWT/urllib3 runtime updates and minimal virtualenv hook-dependency patch. Complete locked graph scan is clear; remote disposition remains SEC-20. |
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
- Expanded audit found four distinct development-only virtualenv advisories in21.7.10. The locked minimal patch is21.7.13 ([upstream activation-script advisory](https://github.com/pypa/virtualenv/security/advisories/GHSA-p58f-9548-mpm2)). Repeated `uv export --locked --all-groups` plus the same strict audit reported **no known vulnerabilities**. No application runtime dependency changed in this follow-up. Subsequent admin/config tests passed37 checks, and both hook stages passed.
- Read-only remote CI inspection found successful older runs, latest returned SHA3352467 ([run](https://github.com/GiftlyKSA/backend/actions/runs/37133529592)). These do not validate this change or current source SHA.
- Push verification confirmed master commit `72e08ba`, then documentation commit `405b9f3`; each push succeeded. The remote alert count decreased20→4 before the hook-dependency patch; metadata access remained HTTP401. Current complete-lockfile scan and remote alert disposition are separate evidence, recorded as SEC-20.
- Final aggregate gate: `uv run --locked pytest -n 4 -o addopts="" -q -p no:cacheprovider --basetemp <fresh temporary directory>` finished with **668 passed, 198 skipped, four existing Starlette/httpx deprecation warnings**. The unavailable PostgreSQL/Redis and native decoder checks remain skipped. Ruff lint/format, strict mypy (184 source files), both pre-commit stages, mobile OpenAPI drift, local documentation links and `git diff --check` passed.

No Docker, live PostgreSQL/Redis, production repair, secrets inspection or vendor delivery
was performed. Native decoder availability, database concurrency/query plans, actual
deployment settings, independent backups/audit export and production load remain
UNCONFIRMED. New tasks contain only outstanding work from this review.

## Courier performance verification — 2026-10-08

Scope: courier-accessible order/radar/realtime, chat/media, invoices, account lookup
and wallet read paths. This is a scoped performance/security regression review,
not a new whole-codebase security certification. Production latency was not measured.

| ID | Category | Severity / score | Status | Evidence / unresolved impact | Minimal change / expected impact / verification |
| --- | --- | --- | --- | --- | --- |
| CP-01 | Reliability / resource use | Medium6 | Fixed | `app/services/invoice_pdf_cache.py:97`: cancelled requests released permits while render threads kept running, admitting excess work. | Shielded rendering owns permit until completion; blocked-thread cancellation regression now enforces four renderers. |
| CP-02 | Reliability / background work | Medium5 | Fixed | `app/services/chat_notification_service.py:91`: retirement of exhausted retries was treated as an empty queue, delaying healthy work by scheduled sweeps. | Named claim batch distinguishes retirement from exhaustion; mixed backlog and bounded20retirement regressions. |
| CP-03 | Performance / latency | Medium5 | Fixed | `app/routers/chat.py` waited for synchronous push after message persistence. | Transactional outbox, bounded paged worker and retries; provider calls outside transactions; response no longer waits for push. |
| CP-04 | Performance / SQL | Medium5 | Fixed | `app/repositories/invoice_repository.py:141` flushed every item; repeated user/courier repository lookups and realtime order hydration caused avoidable round trips. | One item-batch flush; transaction-local read reuse; one scoped realtime projection; query-count and authorization regressions. |
| CP-05 | Performance / CPU | Medium4 | Fixed | media signing and per-message cipher construction repeated synchronous work on the event loop. | Off-loop signing, reused signer/cipher, rotation snapshot and thread regressions. |
| CP-06 | Performance / SQL | Low3 | Fixed | `app/services/order_service.py:322` queried customer rating eligibility even for couriers; keyset indexes omitted ID tie-breakers. | Skip courier-only futile lookup; migration0021 covers actor/revision ordering and NEW city radar. Compiled indexes tested; real plans pending. |
| CP-07 | Performance / cache | Low3 | Fixed | identical invoice PDFs regenerated for each owned download. | Content/template fingerprint reuse, one-hour TTL, capped memory/concurrency,100msRedis budgets and outage regressions. |
| CP-08 | Performance / evidence | Unverified risk | Needs deployment validation | Query plans, actual pool/Redis/network costs and endpoint p95 are unmeasured. | Run disposable PostgreSQL/Redis integrations and representative EXPLAIN/load comparisons; timing logs now separate successful SQL execution from other waits. |

Priority: CP-08 is the remaining validation work; no unresolved concrete defect was
found in the final scoped review. Fresh authorization and financial aggregates are
intentional costs, not justified candidates for stale response caching. Index builds
can block writes; chat push is at least once and may arrive after the next minute
sweep. Local database/container/provider checks did not run. Existing findings below
and unrelated unreleased payment work retain their previous status.

Verification on the isolated master-based release: full pytest suite **788 passed,
207 skipped** (four upstream Starlette deprecation warnings); both all-file
pre-commit and pre-push gates passed, including Ruff and strict mypy. Generated
development OpenAPI exactly matches the pushed specification. Skips include absent
disposable PostgreSQL/Redis services; migration graph/compiled DDL tests do not
substitute for migration execution or query plans. Final independent scoped review
found no remaining concrete defects after CP-01/CP-02 regressions were corrected.


## Scoped payment/PDF release review — 2026-10-08

This supplements the existing full-codebase review; it does not mark unrelated open
findings resolved. The release excludes unrelated workspace SMS, email-copy and payout
changes. Independent review inspected runtime, ownership, ledger/checkpoints, migrations,
PDF/cache and focused regressions; remedies were independently rechecked.

| Finding | Category | Severity / score | Status | Trigger / impact | Minimal fix / system effect | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| PS-FIN-01 | Financial integrity / reliability | High7/10 | Fixed | Late PAID callback after closure could roll back its REVIEW marker, allowing replacement despite unresolved received money. | Preflight fresh locked intents and persist review-only quarantine before batch settlement; no partial financial settlement. | Callback regression; disposable PostgreSQL receiver-rollback regression added, runtime unverified. |
| PS-SEC-01 | Security / provider input | High7/10 | Fixed | Literal numeric status accepted bool/decimal values as PAID/PENDING, weakening provider response validation. | Require actual integer0/1 before interpreting status; fail closed on malformed provider data. | Bool/decimal negative tests reproduced and passed. |
| PS-REL-01 | Reliability / concurrency | Medium6/10 | Fixed | Multi-intent callbacks retained first invoice/wallet locks while requesting later subsets; concurrent payments could deadlock. | Batch locks every invoice/order/intent, then bounded ledger rows and full referenced/payer/system wallet union in globally sorted order; atomic settlement retained. | Lock-order and overflow regressions pass; disposable two-connection test added, runtime unverified. |
| PS-SEC-02 | Access control | Medium5/10 | Fixed | Session role checks could admit an active courier after verification revocation despite wallet eligibility restrictions. | Reuse current account/courier eligibility service before owned recovery or mutation. | All five route role/ownership checks; revoked-courier403 regression. |
| PDF-01 | Readability / delivery | Low3/10 | Fixed | Download used the old plain layout rather than approved branded attachment design. | Shared English Giftly purple renderer, itemized pricing, GMT+3 issue/payment times; static escaped PDF. | Branding/date/static/pagination tests and rendered visual check. |

### Verification boundaries

Production payment execution remains disabled. Dhamen testing mocks do not prove a
live integration; merchant/callback protocols remain external validation. The published
mobile contract adds only the five owned session operations and compatible creation
metadata. HTTP payment responses are private,no-store; no financial response cache is
introduced. PDF reuse is server-side only, after current ownership/content reads, with
one-hour TTL and content/template fingerprint,128 entries/256KiB maximum and bounded
render concurrency. Updates regenerate rather than serve the prior status.

Alembic has one0023 merge head; complete PostgreSQL upgrade SQL generated offline.
Applied0021/0022 are unchanged. Duplicate open-order upgrades and unresolved financial
downgrades refuse rather than discard state. Database upgrade/downgrade/concurrency
execution, representative query plans and provider behavior require disposable CI/staging.

Final verification: 878 tests passed, 218 skipped, and four upstream TestClient
deprecation warnings. PostgreSQL/Redis-dependent tests were skipped because no
disposable services were available. Full pre-commit and pre-push gates passed,
including Ruff formatting/lint and strict mypy over 213 application files.
No Docker or live database/provider writes were performed. Deployment is a separate
check from this source release.

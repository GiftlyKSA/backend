# Giftly backend review — 2026-09-29 update

This report supersedes the earlier review at this filename. Audited application revision:
`2f1b0c6` on `master`. The original findings below remain as the evidence and impact record;
the 2026-09-29 fix status is recorded separately below.
It follows [AGENTS.md](../AGENTS.md) and the
[OWASP Top 10:2025](https://top10.owasp.org/2025/) baseline (source checked 2026-09-27).
It covers security, performance, optimization, SQL, quality, improvements, and scalability.
This is a source/offline-test review, not a zero-vulnerability certification.

## Severity, confidence, and scope

- **High: 8–10/10:** serious availability, integrity, or release risk requiring priority work.
- **Mid: 4–7/10:** meaningful defect/control gap with narrower exposure or prerequisites.
- **Low: 1–3/10:** smaller resource cost or preventive maintenance opportunity.
- Scores reflect engineering impact and prerequisites, **not CVSS**. A verification gap's
  score is investigation priority, not a proven vulnerability score.
- **Confirmed** means demonstrated by source or the stated offline probe. **Conditional**
  requires the configuration/scale described. **UNCONFIRMED** means runtime evidence is
  insufficient. Suggestions are identified separately. Existing IDs are retained.

Inspection included repository-wide risk-pattern searches and detailed review of HTTP/WS
authentication, OTP, registration, refresh/logout, admin sessions/CRUD, media ownership,
orders/invoices/wallets, chat, provider adapters, workers, models/indexes, configuration,
logging, migration patterns, CI, and API-documentation generation. This was not an independent
line-by-line proof of every file. PostgreSQL plans/concurrency, live Redis Lua execution,
deployed controls, actual vendor behavior, and load capacity remain unverified locally.
No Docker was started or run. No real environment secrets were inspected.

## Global priority index

### Fix status — 2026-09-29

The index and detailed findings below preserve the 2026-09-27 assessment and severity.
The following findings have source fixes and offline regression tests in this change; live
PostgreSQL/Redis and deployment verification remains pending where applicable.

| IDs | Change | Verification still needed |
| --- | --- | --- |
| SEC-10, SEC-11 | Reject weak explicit OTP signing keys and non-HTTPS production provider URLs at startup | Check actual deployed configuration without exposing secrets |
| QUAL-01, QUAL-02 | Return client errors for invalid status/cursors and registration tokens | Client contract smoke test in deployment |
| QUAL-03 | Include safe exception type and source location in JSON logs | Verify log ingestion fields |
| PERF-05 | Bound refresh-token expiry cleanup and index its expiry | PostgreSQL query plan and worker backlog under load |
| PERF-07 | Close worker-owned receipt integration clients | Worker lifecycle smoke test |
| REL-07 | Lock chat conversation rows before unread/latest-message mutations | Two-session PostgreSQL race test |

The OTP default is 60 seconds and the new city catalog is seeded with 20 active Saudi
cities. Customer orders and courier profiles now store UUID foreign keys to `cities.id`;
admin and API mutations validate active selections. The city migrations and seed script need
a disposable PostgreSQL/PostGIS run in CI or another authorized environment. No Docker was
run locally. All other findings below remain open or unverified as originally classified.

The UUID relationship is a 2026-09-29 follow-up correction to the original name-keyed
city migration. Its additive/backfill/drop migration is rendered offline, but has not
been applied to a live database. Existing name-based API request fields remain accepted
to avoid breaking older clients; new clients can submit city IDs.

| Priority | ID | Category | Severity / score | Status | Finding |
| --- | --- | --- | --- | --- | --- |
| 1 | PERF-02 | Performance | High 8 | Confirmed design defect | City push fan-out precedes order commit |
| 2 | INT-01 | Improvements / release verification | High 8 | UNCONFIRMED vendor compatibility | Production SMS/email contracts need verification |
| 3 | SEC-07 | Security | Mid 7 | Confirmed missing control | No per-account concurrent chat connection cap |
| 4 | SEC-08 | Security | Mid 6 | Confirmed missing control | No cumulative upload quota/abandoned-object cleanup |
| 5 | SEC-10 | Security | Mid 6 | Fixed offline; deployment check pending | Empty/weak dedicated OTP HMAC key accepted |
| 6 | SEC-11 | Security | Mid 6 | Fixed offline; deployment check pending | Production provider URLs need not use HTTPS |
| 7 | REL-06 | Scalability / reliability | Mid 6 | Confirmed, latent | Order cancellation leaves invoice/hold pending |
| 8 | REL-05 | Scalability / reliability | Mid 6 | Confirmed design defect | Receipt sends retain locks; sweep can outlive lease |
| 9 | REL-07 | Scalability / reliability | Mid 6 | Fixed offline; DB race proof pending | Concurrent chat updates can lose unread counts |
| 10 | REL-08 | Scalability / reliability | Mid 6 | Confirmed default; outage untested | Redis lacks an explicit operation timeout budget |
| 11 | PERF-04 | Optimization | Mid 6 | Confirmed, scale-dependent | Key rotation buffers whole tables in one transaction |
| 12 | CI-02 | Improvements / verification | Mid 6 | Missing run evidence | No master CI runs returned by workflow query |
| 13 | PERF-05 | SQL optimization | Mid 5 | Fixed offline; DB plan pending | Refresh purge is unbounded and lacks expiry index |
| 14 | PERF-03 | SQL optimization | Mid 5 | Confirmed, scale-dependent | Admin browser supports very deep OFFSET scans |
| 15 | QUAL-01 | Quality | Mid 5 | Fixed offline | Invalid status/cursor input produces HTTP 500 |
| 16 | QUAL-02 | Quality / authentication | Mid 5 | Fixed offline | Invalid registration token produces HTTP 500 |
| 17 | QUAL-03 | Quality / observability | Mid 5 | Fixed offline | JSON logger discards exception diagnostics |
| 18 | TEST-01 | Improvements / verification | Mid 5 | UNCONFIRMED table failures | Generic admin writes lack complete DB coverage |
| 19 | QUAL-04 | Quality / reliability | Mid 4 | Source race; DB proof pending | Device-token upsert can race into unique failure |
| 20 | OPT-01 | Optimization | Low 3 | Setup confirmed; cost unmeasured | S3 client setup repeats per operation |
| 21 | PERF-06 | Performance | Low 3 | Confirmed | Admin reads repeatedly update the session row |
| 22 | PERF-07 | Optimization | Low 3 | Fixed offline | Receipt worker does not close owned clients |
| 23 | SQL-01 | SQL optimization | Low 3 | Suggestion; plans needed | Measure sort/index alignment and reconciliation |
| 24 | MAINT-02 | Code quality suggestions | Low 3 | Preventive improvement | Mobile OpenAPI has no reproducible CI drift check |
| 25 | MAINT-03 | Security / quality suggestions | Low 3 | Preventive improvement | Independent audit trail for unrestricted admin CRUD |

**SEC-09 is an additional unscored external-signal investigation.** The old report's four
GitHub alerts are historical, not a verified current count. Today's production dependency
scan found no known vulnerabilities; the alert API returned HTTP 401. Details below.

## Security

### High

No new High security exploit was established. This does not clear unverified deployment
controls, provider release readiness, or the historical dependency-alert discrepancy.

### Mid

#### SEC-07 — Concurrent chat connections are unbounded per account — 7/10

- **Evidence/status:** Confirmed in `app/routers/chat.py:182–225`. Each authorized connection
  allocates a Redis subscription and three tasks, with recurring database authorization
  checks. Message throttling does not constrain idle simultaneous sockets.
- **Impact if unresolved:** an eligible account can multiply memory, subscriptions, and DB
  checks without sending messages. Reverse-proxy connection limits are **UNCONFIRMED**.
- **Minimal fix / system effect:** atomic shared per-account connection cap with expiring
  leases, cleanup, and bounded global capacity. Reject excess connections before resource
  allocation; preserve live membership checks and reconnect behavior.
- **Verification:** multi-instance caps, disconnect/crash cleanup, Redis failure, and measured
  idle-connection load. A process-local counter would not protect multiple API instances.

#### SEC-08 — Upload accumulation has no cumulative quota or cleanup — 6/10

- **Evidence/status:** Confirmed in `app/services/media_service.py:41–72` and upload persistence.
  Individual size/URL lifetime are bounded; cumulative account storage, abandoned grants,
  and object cleanup are not enforced by the inspected code. Bucket lifecycle is unknown.
- **Impact if unresolved:** repeated valid uploads grow storage costs and grant rows.
  Request throttling limits speed, not eventual accumulated size.
- **Minimal fix / system effect:** atomically limit outstanding uploads/bytes and add bounded
  expiry cleanup coordinated with attachment claims. Retain owner/purpose/magic/size checks
  and create-only signing; never delete attached order or delivery evidence.
- **Verification:** concurrent issuance at quota, abandoned/attached races, expired URLs,
  cleanup retry failures, and documented retention behavior.

#### SEC-10 — Explicit OTP HMAC key bypasses strength validation — 6/10

- **Evidence/status:** Confirmed conditional misconfiguration. `app/core/config.py:108,160`
  accepts the optional key without strength validation; `app/services/otp_service.py:68`
  selects it whenever non-None, including empty text. An offline settings probe accepted `""`.
- **Impact if unresolved:** a weak known key plus Redis read access permits offline enumeration
  of the small OTP space. This is not an established remote login bypass. The default
  fallback secret is validated; no deployed secret was inspected.
- **Minimal fix / system effect:** fail startup for explicit keys shorter than 32 bytes;
  document secure random generation and purpose separation. Preserve the validated fallback.
  Length validation cannot itself prove entropy.
- **Verification:** empty/short/non-ASCII byte-length tests across environments, plus unchanged
  verification with valid dedicated and fallback keys.

#### SEC-11 — Production provider URLs do not require HTTPS — 6/10

- **Evidence/status:** Confirmed configuration gap, conditional on misconfiguration.
  `app/core/config.py:83,248–263` checks provider values for presence, not HTTPS;
  `app/integrations/factory.py:63–75` passes URLs to credential-bearing clients.
- **Impact if unresolved:** an accidental HTTP URL can expose provider keys, OTPs, or email
  data in transit. Actual deployed URL schemes are **UNCONFIRMED**.
- **Minimal fix / system effect:** validate parsed HTTPS URLs/hosts in production, rejecting
  embedded credentials and unsafe forms. Keep explicit development/test exceptions and use
  known provider host allowlists where supported by the deployment contract.
- **Verification:** reject HTTP/malformed production URLs; accept approved HTTPS and local
  test fakes without disabling certificate verification.

### Low

No additional confirmed Low security defect. MAINT-03 is an optional audit improvement;
unrestricted authenticated/authorized admin CRUD was explicitly requested by the owner.

### SEC-09 — Dependency alerts: fresh result and unresolved discrepancy

On 2026-09-27, the locked production requirements export audited with pip-audit 2.10.1
completed with **No known vulnerabilities found**. This covers the exported Python graph
applicable to the audit environment, not container OS packages, every target platform,
development tools, or vulnerabilities absent from the advisory feed.

The old report recorded two High and two Moderate GitHub alerts. Reading current
[Dependabot alerts](https://github.com/GiftlyKSA/backend/security/dependabot) returned HTTP 401.
Their current packages/status remain **UNCONFIRMED**; the historical count is not current
evidence. An authorized maintainer should reconcile alert IDs, affected lock paths, platform
applicability, and fixed versions. Minimally patch applicable packages and verify compatibility.
This item has no substantiated current vulnerability score.

## Performance

### High

#### PERF-02 — City notification fan-out delays order commit — 8/10

- **Evidence/status:** Confirmed. `app/routers/orders.py:166` awaits city push before
  `app/core/deps.py:54` commits. `app/repositories/device_token_repository.py:55` loads all
  matching tokens; `app/services/notification_service.py:40` sends serial batches of 500.
- **Impact if unresolved:** city growth/provider latency extends requests and database locks.
  A notification may reference an order whose transaction later rolls back. Swallowing push
  errors does not resolve transaction duration or notification-before-commit ordering.
- **Minimal fix / system effect:** write a notification/outbox item in the order transaction;
  a bounded worker paginates recipients after commit with deduplication/retries. Responses
  no longer depend on provider latency. Requires a small migration and compatible worker
  rollout; an in-memory background task alone does not provide durable delivery.
- **Verification:** rollback suppresses notifications, retry idempotency, bounded memory,
  and short order transactions under a slow-provider test.

### Mid

Related Mid costs are recorded once under SQL, optimization, and reliability below.

### Low

#### PERF-06 — Ordinary admin reads update the session row — 3/10

- **Evidence/status:** Confirmed in `app/admin/deps.py:126`,
  `app/services/admin_auth_service.py:146`, `app/repositories/admin_session_repository.py:56`.
  Most authenticated GETs extend expiry and flush an UPDATE.
- **Impact if unresolved:** extra writes and contention between concurrent dashboard requests
  using the same session; row locks can persist through the request transaction.
- **Minimal fix / system effect:** refresh near a defined expiry threshold using a conditional
  update. Still validate expiry, revocation, and credential version on every request.
- **Verification:** fixed-clock expiry/revocation tests and concurrent request UPDATE counts.

## Optimization and resource lifecycle

### High

No separate confirmed High finding.

### Mid

#### PERF-04 — Key rotation buffers growing tables in one transaction — 6/10

- **Evidence/status:** Confirmed, scale-dependent. `app/services/key_rotation_service.py:78,100,113`
  selects whole profile/conversation/withdrawal tables using buffered ORM results and flushes
  under the caller's transaction, without resumable batch commits.
- **Impact if unresolved:** memory, lock duration, and rollback work increase as data grows,
  making security key maintenance difficult when it is needed.
- **Minimal fix / system effect:** bounded primary-key batches, explicit short commits, and
  restartable progress. Use locks/version checks to avoid overwriting concurrent field edits.
  Retain old keys referenced by immutable messages.
- **Verification:** interruption/resume, concurrent edits, unchanged AAD/decryptability, and
  representative peak-memory and lock-duration measurements.

### Low

#### OPT-01 — S3 client setup repeats for sequential object checks — 3/10

- **Evidence/status:** Setup pattern confirmed; cost unmeasured.
  `app/integrations/storage/real.py:64,82,100` constructs/closes clients per operation;
  `app/services/media_service.py:100` checks multiple objects serially within the caller's
  transaction. Attachment count is bounded; this is not an unbounded list-query finding.
- **Impact if unresolved:** connection setup and sequential HEAD/range-read latency extend
  media requests and transactions. No improvement percentage has been measured.
- **Minimal fix / system effect:** lifecycle-scoped client reuse with explicit timeout/retry
  budgets. Preserve stable database claim ordering and ownership/integrity checks; only
  parallelize remote reads when bounded and compatible with those invariants.
- **Verification:** client/connection counts, shutdown/cancellation, slow-S3 behavior, and
  latency for one versus the maximum allowed attachments.

#### PERF-07 — Receipt worker does not close its owned clients — 3/10

- **Evidence/status:** Confirmed in `app/workers/receipts.py:46,75–78`. Default construction
  retains only `build_clients(settings).email`; cleanup disposes the engine, not that bundle.
- **Impact if unresolved:** repeated production sweeps can retain HTTP resources/connections.
  API lifecycle cleanup does not own worker-created clients; fakes hide this behavior.
- **Minimal fix / system effect:** retain the owned bundle and close it in `finally`; do not
  close injected collaborators. Receipt business behavior remains unchanged.
- **Verification:** cleanup after success, exception, cancellation, and repeated sweeps;
  injected clients remain open.

## SQL query optimization

### High

No confirmed High query-plan defect; no production EXPLAIN/load data was available.

### Mid

#### PERF-05 — Refresh-token expiry purge is unbounded — 5/10

- **Evidence/status:** Confirmed, scale-dependent. `app/repositories/auth_repository.py:165–173`
  deletes all expired rows; `app/models/tables.py:67–99` has no expiry index. The worker at
  `app/workers/expiry.py:164` uses a 120-second lease.
- **Impact if unresolved:** long scans/deletes, WAL/vacuum pressure, and overlapping sweeps
  when duration exceeds the lease. Current row count/runtime is unmeasured.
- **Minimal fix / system effect:** bounded expiry batches, suitable expiry/ID index, and short
  commits. Retain replay evidence until the existing retention cutoff. Review migration
  lock/index creation and rollback costs before deployment.
- **Verification:** EXPLAIN (ANALYZE, BUFFERS), batch caps, expiry boundaries, retained replay
  detection, and concurrent sweep behavior on disposable PostgreSQL.

#### PERF-03 — Admin pagination permits expensive deep OFFSET scans — 5/10

- **Evidence/status:** Confirmed. `app/admin/router.py:198` permits page 100,000;
  `app/repositories/admin_read_repository.py:121` uses page size 50 and OFFSET, allowing
  4,999,950 skipped rows. Small response size does not imply bounded small scan work.
- **Impact if unresolved:** authorized deep browsing can become slow and consume shared DB
  capacity. No public unauthenticated path was established.
- **Minimal fix / system effect:** stable created-at/PK cursors, or an explicit smaller
  deep-page limit as an interim measure. Preserve relationship search and CRUD access.
- **Verification:** duplicate timestamps, deleted anchors, final pages, and shallow/deep plans.

### Low

#### SQL-01 — Measure ordering-index alignment and ledger growth — 3/10

- **Status/evidence:** Suggestion, not a measured missing-index incident.
  `app/models/tables.py:370–384,807–818` and order/chat queries combine filters and timestamp/ID
  ordering not fully represented in every shown index. Reconciliation aggregates a growing
  settled ledger in one statement. Existing indexes may be adequate at actual selectivity.
- **Impact if ignored:** sorts, aggregation, or temporary storage may dominate at scale.
- **Minimal improvement / effect:** obtain EXPLAIN (ANALYZE, BUFFERS) for radar, inbox,
  customer/courier lists, and reconciliation. Add only justified composite/partial indexes
  or incremental reconciliation, accounting for index storage/write costs.
- **Verification:** compare equivalent data/plans, buffer reads, latency, and write cost.

**N+1 result:** no growing-list N+1 was established in inspected order lists, inbox, or admin
relationship-label paths. Rating state is batched; the single-order coordinate lookup is
not performed per row in list enrichment. Add query-count tests for increasing page sizes
as preventive coverage, without claiming an existing N+1 defect.

## Scalability and reliability

### High

PERF-02 is recorded once under Performance.

### Mid

#### REL-06 — Order cancellation leaves issued invoice/reservation pending — 6/10

- **Evidence/status:** Confirmed latent lifecycle gap. `app/services/order_service.py:182–200`
  changes only the order; WAITING_PAYMENT is cancellable and can have an invoice/wallet hold.
  `app/services/invoice_service.py:196` has separate invoice cleanup.
- **Impact if unresolved:** a cancelled order can retain a pending invoice and unavailable
  wallet funds until later cleanup. Disabled production payments limit present exposure;
  simulations and future payment enablement still need correct lifecycle behavior.
- **Minimal fix / system effect:** coordinate invoice, reservation, promo, and order cleanup
  in one service transaction. Preserve invoice-before-order lock order, ownership, and
  idempotency; taking inverse locks would introduce deadlocks.
- **Verification:** WAITING_PAYMENT cancellation, retries, payment/expiry races, reserved
  balances, and rollback after partial cleanup in PostgreSQL.

#### REL-05 — Receipt network calls retain locks and may outlive sweep lease — 6/10

- **Evidence/status:** Confirmed in `app/services/receipt_service.py:62–97` and
  `app/workers/receipts.py:30–88`. Invoice locks span email delivery. Up to 100 serial sends
  share a 300-second lease without renewal/fencing; the email timeout is 10 seconds, so a
  batch can exceed that lease. This is a possibility, not a measured duration bound.
- **Impact if unresolved:** slow providers retain DB resources and overlapping sweeps add
  contention. Send-success followed by rollback/crash can duplicate delivery. Invoice locks
  still serialize normal stamps; lease expiry alone does not prove duplicate receipts.
- **Minimal fix / system effect:** bounded durable claims in short transactions, send outside
  the lock, then record completion. Use provider idempotency if available; otherwise retain
  explicit at-least-once semantics. Use leases/renewal appropriate to the claim protocol.
- **Verification:** two workers, timeouts, send-success/stamp-failure, restart, lease expiry,
  and stable provider idempotency keys with real database locking.

#### REL-07 — Chat counters and latest-message metadata can race — 6/10

- **Evidence/status:** Confirmed source race; DB reproduction pending.
  `app/repositories/chat_repository.py:29–39` reads without a lock; `:79–87` uses Python
  counter increments, allowing two readers of N to both write N+1. `:172–190` resets counters
  during mark-read without a shared serialization rule for sending and reading.
- **Impact if unresolved:** unread badges undercount; previews can become stale when writes
  reorder. Mark-read races can disagree with message read flags.
- **Minimal fix / system effect:** serialize conversation mutations before reading state,
  or use atomic SQL plus a defined read watermark/latest-message ordering rule. Counter-only
  changes do not solve preview ordering. Preserve authorization and short transactions.
- **Verification:** independent concurrent PostgreSQL sessions sending and marking read;
  assert counters, message read flags, preview, and timestamp.

#### REL-08 — Redis operations lack an explicit timeout budget — 6/10

- **Evidence/status:** Confirmed default in `app/core/redis.py:17`. Offline introspection
  with a plain dummy Redis URL returned `socket_timeout=None`, pool limit **100**. The pool
  is not being reported as unlimited; actual deployment URL options were not inspected.
- **Impact if unresolved:** a connected but stalled Redis server can retain requests/tasks
  for OTP, rate limits, and locks. Fail-closed behavior does not bound the wait for failure.
- **Minimal fix / system effect:** explicit connection/operation deadlines and deployment
  connection budgets, including workers. Return safe temporary errors without bypassing
  authentication/limits/locks. Treat long-lived pub/sub waits separately.
- **Verification:** stalled Redis, cancellation cleanup, bounded failure latency, pool
  exhaustion, and absence of fail-open behavior.

### Low

Other resource/reliability issues are recorded once as PERF-07 and QUAL-04.

## Quality, readability, naming, and maintainability

### High

No confirmed High finding in this category.

### Mid

#### QUAL-01 — Invalid query values become HTTP 500 — 5/10

- **Evidence/status:** Offline real-route probes with stubbed dependencies returned 500 for
  `/api/orders?status=INVALID` and `?cursor=invalid`. `app/routers/orders.py:187,194,212` converts
  strings to Enum/UUID without translation; `app/routers/wallets.py:157` and
  `app/routers/chat.py:123` use the same UUID pattern.
- **Impact if unresolved:** client mistakes create misleading 5xx alerts and retries;
  this is not evidence of authorization bypass.
- **Minimal fix / system effect:** validated enum/UUID boundary parameters or consistent
  conversion to the existing 4xx contract; preserve valid pagination behavior.
- **Verification:** malformed, empty, unknown, and valid filters/cursors on each affected
  route; align OpenAPI and client docs with the chosen error response.

#### QUAL-02 — Invalid registration JWT escapes as HTTP 500 — 5/10

- **Evidence/status:** Reproduced through `/api/auth/register` with the real service and stub
  collaborators. `app/services/auth_service.py:141` calls the decoder;
  `app/core/jwt.py:134–154` raises `JwtError` without translation at this boundary. Issuer,
  audience, and algorithm checks exist; no forged-token acceptance was demonstrated.
- **Impact if unresolved:** expired/malformed login continuation tokens appear as server
  failures, confusing clients and incident monitoring.
- **Minimal fix / system effect:** translate invalid-token errors to safe unauthorized
  responses; explicitly require expected registration claims including subject/expiry.
  Preserve purpose separation and never expose internal decoder messages.
- **Verification:** malformed/expired/missing-claim/wrong-purpose/issuer/audience/algorithm
  tokens create no account or credentials; valid registration remains unchanged.

#### QUAL-03 — JSON logging loses exception diagnostics — 5/10

- **Evidence/status:** Synthetic exception probe confirmed `app/core/logging.py:80–94`
  ignores `exc_info`: output contained only level, logger, and message.
- **Impact if unresolved:** worker/HTTP `.exception()` calls lose type and stack context,
  delaying diagnosis and security investigation (OWASP A09).
- **Minimal fix / system effect:** safely scrubbed exception type/stack frames with correlation
  IDs; omit locals, raw requests, credentials, and sensitive exception payloads. Client
  responses stay generic.
- **Verification:** exception and ordinary records, nested failures, useful frame/type output,
  and explicit credential/PII redaction tests.

#### QUAL-04 — Device-token upsert is not atomic — 4/10

- **Evidence/status:** Source race; DB reproduction pending.
  `app/repositories/device_token_repository.py:29–42` selects then inserts under a unique
  token constraint. Concurrent first registrations can both observe no row.
- **Impact if unresolved:** one valid request can fail with an integrity error instead of
  idempotently registering a token, reducing push-registration reliability.
- **Minimal fix / system effect:** PostgreSQL ON CONFLICT with explicit ownership/device-field
  semantics. Preserve intended reassignment when a device changes accounts.
- **Verification:** concurrent same/different tokens and account switching; one correct row
  remains and duplicate registration does not become an unexplained 500.

### Low — preventive suggestions

#### MAINT-02 — Reproducible mobile OpenAPI generation and drift checks — 3/10

- **Evidence/status:** Preventive improvement, not an alleged current mismatch.
  `app/export_openapi.py:15–22` and CI export full `docs/openapi.json`, not the independently
  enriched [mobile specification](mobile-openapi.json).
- **Impact if ignored:** later schema/security changes can leave UI integration docs stale
  even when normal export CI is green.
- **Minimal improvement / effect:** deterministic filtered export with retained screen/role/
  dependency metadata and a CI drift check. Unsupported screens remain future notes.
- **Verification:** regenerate without diff, detect intentional schema change, resolve all
  references, include every public operation, and exclude admin operations.

#### MAINT-03 — Independent audit records for unrestricted admin maintenance — 3/10

- **Evidence/status:** Optional defense in depth. The owner authorized all-table authenticated/
  authorized CRUD, including financial/audit maintenance. This is not an access-control bug.
- **Impact if ignored:** authorized edits can alter records needed to investigate mistakes
  or a compromised administrator account.
- **Minimal improvement / effect:** redacted attributable events in an independently protected
  append-only sink with retention/correlation IDs. Preserve requested CRUD, without silently
  adding read-only restrictions.
- **Verification:** write/delete events, no secret payloads, independent retention/access,
  and explicitly tested sink-outage behavior.

No independent naming-only/comment-only defect with material impact was found. Broad
cosmetic refactoring would conflict with the minimal-change rule. Keep concise comments,
precise types, Ruff style, and service/repository boundaries while fixing concrete issues.

## Improvements and remaining verification

### High

#### INT-01 — Verify production SMS/email contracts before release — 8/10

- **Status:** High release-verification priority; **UNCONFIRMED compatibility**, not a proven
  deployed outage or authentication exploit.
- **Evidence:** `app/integrations/sms/real.py:25` marks the vendor contract for refinement and
  sends generic `/send` data; `app/integrations/factory.py:72` supplies `SUPABASE_URL`.
  `app/integrations/email/sndr_client.py:1–5` says its mapping is provisional. Comments establish
  missing repository evidence, not current facts about vendor documentation availability.
- **Impact if unresolved:** wrong endpoint/authentication/payload can prevent production OTP
  delivery and sign-in; receipt delivery can repeatedly fail.
- **Minimal improvement / effect:** verify authoritative vendor/deployed-function contracts
  and authorized sandbox delivery. Keep corrections isolated in adapters. Dhamen remains
  separately disabled pending its own verified integration.
- **Verification:** recorded contract examples, successful sandbox OTP/email, authentication/
  timeout failures, and logs without codes or credentials.

### Mid

#### CI-02 — Backend CI evidence remains absent — 6/10

- **Evidence/status:** On 2026-09-27, `gh run list --repo GiftlyKSA/backend --workflow CI
  --branch master --limit 5 --json status,conclusion,headSha,name` returned `[]`. General
  listing showed historical dependency-graph runs. Trigger/root cause is **UNCONFIRMED**.
- **Impact if unresolved:** configured database/migration/coverage/image checks have not
  established that this revision passes; offline tests cannot substitute for them.
- **Minimal fix / system effect:** investigate trusted Actions permissions/trigger diagnostics,
  correct the demonstrated issue, and obtain a CI run on the current master SHA.
- **Verification:** green quality/test/security/docs/image jobs, disposable PostgreSQL/PostGIS/
  Redis tests, migration round trips, and configured 85% coverage gate.

#### TEST-01 — Generic admin writes lack complete database coverage — 5/10

- **Evidence/status:** Verification gap; no specific table failure established.
  `tests/integration/test_admin_table_crud.py` renders forms across all tables but persists
  writes on a subset. Offline parsing/form mocks do not prove each DB constraint/type.
- **Impact if unresolved:** encrypted fields, PK/FK shapes, defaults, or deletion constraints
  may fail on a table without a meaningful regression detecting the failure.
- **Minimal improvement / effect:** compact database-backed matrix of distinct schema shapes,
  relationship filtering, persisted writes/deletes, rollback, audit, and permission denials.
  No runtime CRUD policy change is required.
- **Verification:** disposable service-backed CI; assert persisted values, not just HTML.

### Low

SQL-01, MAINT-02, and MAINT-03 contain the concrete lower-priority suggestions. Do not add
caching, indexes, abstractions, or broad refactors just to fill a category.

## OTP storage and authentication behavior

**The default is now 60 seconds.** `OTP_TTL_SECONDS` can still be overridden by deployment
configuration; actual deployment overrides were not inspected. The code stored in Redis is
an HMAC digest, not plaintext. The attempt counter uses the same 60-second expiry.

| Aspect | Current implementation |
| --- | --- |
| Storage | Redis `otp:code:{normalized_phone}` holds an HMAC digest, not plaintext OTP |
| Expiry | Issuance Lua uses SET EX with `OTP_TTL_SECONDS`, default 60 |
| Resend | Replaces the previous code and resets its verification-attempt counter |
| Verification | Atomic Lua compares supplied HMAC and consumes a successful code |
| Success cleanup | Deletes code, attempts, and request-rate keys; does not delete a block key |
| Guess limit | At most five comparisons per code; sixth request deletes code and returns rate-limited |
| Request limit | Three requests per 300 seconds by default; exceeding creates a 1,800-second block |
| Development | Secure random five-digit code 10000–99999; numeric `otp_dev` and `expires_in` returned |
| Test/production | Existing six-digit generator; API excludes `otp_dev` |
| Verification input | Submit OTP as the schema's string field, preserving production leading zeros |
| SMS failure | Stored before sending; failed delivery may leave a code until expiry and consume request allowance |

The single-use/attempt rules are implemented atomically, but this audit did not execute
Lua on live Redis. Unit fakes/mocks do not prove actual TTL or multi-client behavior.
The dedicated-key issue SEC-10 is fixed in settings validation. Phone numbers are present in Redis key names; Redis
access, TLS, backups, and retention remain deployment controls even with hashed codes.

Authentication source controls retained: access algorithm/issuer/audience validation, live
account/role/version checks, serialized refresh rotation and replay revocation, all-device
logout, WS membership rechecks, admin authentication/CSRF/step-up. QUAL-02 is fixed offline.
No new cross-account access bypass was established in inspected paths; that does not prove
universal authorization correctness.

## Prior repairs and intentional limits

| Earlier IDs / area | Current source assessment | Remaining proof |
| --- | --- | --- |
| SEC-01/02/03/05 | Refresh serialization, live actor checks, credential invalidation, atomic OTP retained | Real DB/Redis concurrency |
| SEC-04 | Owner/purpose media grants, one-use claims, create-only signed uploads retained | Real S3 policy and DB races |
| SEC-06, CI-01, BUILD-01 | Explicit production lock audit and pinned image inputs retained | Current image/OS audit and CI |
| REL-01 | Registered tasks and dedicated scheduler definition retained | Live scheduled delivery |
| REL-02/03, MAINT-01 | Per-intent reservations and service-owned financial expiry retained | Migration/lock races |
| REL-04, PERF-01, READ-01 | Single-statement discrepancy reconciliation and typing retained | Representative query plan |
| SEC-F01/F02/F04/F05 | All-device logout, locked order refresh, identity fingerprint, quota locks retained | Service-backed regressions |
| FPERF-04, MAINT-F02 | Purpose filtering before expiry LIMIT and rollback warning retained | Backlog/migration checks |

- Dhamen production payments intentionally fail closed pending verified integration.
- Development OTP disclosure and wildcard non-credentialed CORS are explicitly requested;
  they are not production controls and do not disable authentication.
- All-table admin CRUD is authorized. Direct maintenance can violate business assumptions;
  keep authentication, authorization, CSRF, and attributable events.
- Prior clean-migration authorization does not authorize resetting a current database.

## Verification update — 2026-09-29

| Check | Result / limitation |
| --- | --- |
| `uv run --locked pytest tests/unit -q` | Passed all 282 unit tests; one upstream Starlette/httpx deprecation warning |
| `uv run --locked pytest tests/integration/test_app.py -q` | 7 passed |
| `uv run --locked pytest tests/integration/test_city_catalog.py -q -rs` | Skipped: no local PostgreSQL service |
| Full `pytest -q` | Attempted; stopped after slow database-dependent skips with no PostgreSQL/Redis services. No Docker was run |
| Pre-commit and pre-push all-file hooks | Passed Ruff, format, mypy, YAML/TOML/JSON, merge markers, private-key checks |
| Alembic offline `upgrade head --sql` | Rendered through the city/refresh-index migration; no live schema apply or downgrade tested |
| Mobile OpenAPI | 44 non-admin operations, including public `GET /api/cities` |

The UUID city-relationship correction passed `pytest tests/unit tests/integration/test_app.py`
and the full Ruff/mypy gates. The new migration's forward and downgrade SQL rendered
offline. Four city/admin relationship integration cases skipped because no PostgreSQL
service was listening. The mobile OpenAPI retained 44 non-admin operations, valid
schema references, UUID city fields, and bearer-auth declarations.

## Historical verification record — 2026-09-27

| Check | Result / limitation |
| --- | --- |
| `uv run --locked pytest tests/unit tests/integration/test_app.py -o addopts='' -q`, explicit dummy settings | **271 passed**, one upstream Starlette/httpx deprecation warning; 4.55 seconds |
| Real-route offline probes with stubbed dependencies | Invalid order status/cursor and registration token returned **500**; no real DB/network |
| Settings/client/logging probes | Empty OTP key accepted; Redis socket timeout None/pool 100; exception diagnostics absent |
| Locked production export + `uv tool run pip-audit==2.10.1 --strict --no-deps --disable-pip --requirement <export>` | **No known vulnerabilities found**; advisory/platform scope limits apply |
| GitHub CI workflow query | No CI runs returned for master; general listing had historical graph jobs |
| Dependabot alert API | HTTP 401; current alert details **UNCONFIRMED** |
| `uv run --locked pre-commit run --all-files` | All seven configured pre-commit checks passed |
| `uv run --locked pre-commit run --all-files --hook-stage pre-push` | Full-project Ruff lint/format and strict mypy passed |
| PostgreSQL/PostGIS/Redis suite, migration round trips, full coverage | **Not verified in this audit**; disposable service-backed CI required |
| Docker, real providers/S3, load tests, EXPLAIN | **Not run locally**; local Docker prohibited |

An earlier full-suite attempt without available services did not establish a passing full
suite. No performance gain is claimed: this update changes only the report. Fixes require
targeted reproductions, minimal compatible patches, regression tests, and renewed review.
Remaining deployment checks include proxy trust/query-token log redaction, Redis isolation,
private storage/IAM/lifecycle, DB roles, restore tests, alerts, worker capacity, and vendor
delivery contracts.

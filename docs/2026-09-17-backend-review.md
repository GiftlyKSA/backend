# Giftly backend review — updated 2026-09-24

This report supersedes the 2026-09-17 review. It evaluates the current backend against
[AGENTS.md](../AGENTS.md) and OWASP Top 10:2025 across authentication, authorization,
admin CRUD, media, orders, money, workers, persistence, dependencies, CI, and deployment
source. Independent passes reviewed security, performance/scalability/reliability, and
maintainability/readability. Code paths described below are confirmed by source inspection;
production frequency, real concurrency, load capacity, deployed controls, and live provider
behavior are **UNCONFIRMED**. This is not a zero-vulnerability certification.

Severity: **High** is a serious financial, operational, or availability failure under the
stated trigger; **Medium** is a meaningful defect with narrower prerequisites or blast
radius; **Low** is a smaller resource or maintenance cost. Within each category, findings
are ordered by severity then impact. Every open item states the impact if left unfixed,
the minimal proposed fix, its system effect, and verification needed. Line positions may
shift after this report; linked files are the source of truth.

## Global priority index — open findings

| Priority | ID | Category | Severity | Finding |
| --- | --- | --- | --- | --- |
| 1 | PERF-02 | Scalability/reliability | High | New-order push is unbounded and precedes commit |
| 2 | SEC-09 | Supply-chain security | Reported High/Medium, unverified | GitHub reports four dependency alerts after push |
| 3 | SEC-07 | Security, OWASP A06 | Medium | No concurrent chat-socket cap per account |
| 4 | SEC-08 | Security, OWASP A06 | Medium | Upload issuance has no cumulative quota or retention cleanup |
| 5 | REL-05 | Reliability | Medium | Receipt sends hold row locks across network calls and can outlive the sweep lease |
| 6 | REL-06 | Financial reliability | Medium, latent | Order cancellation leaves an issued invoice and wallet hold pending |
| 7 | PERF-03 | Query scalability | Medium | Deep admin pages use large OFFSET scans |
| 8 | PERF-04 | Maintenance scalability | Medium | Key rotation buffers growing tables in one transaction |
| 9 | PERF-05 | Maintenance scalability | Medium | Refresh-token purge is one unbounded delete |
| 10 | CI-02 | Verification | Medium | No CI run started for the master push |
| 11 | TEST-01 | Verification | Medium, unverified | Actual CRUD across all 29 admin table shapes lacks DB proof |
| 12 | PERF-06 | Admin contention | Low | Every authenticated admin GET writes its session row |
| 13 | PERF-07 | Worker resource lifecycle | Low | Worker-owned email client is not closed |

## Security

### SEC-09 — GitHub dependency alerts require triage

- **Status/severity:** Open external-signal discrepancy / reported two High and two
  Moderate; affected package, applicability, and current status **UNCONFIRMED**.
- **Trigger/evidence:** GitHub's response to the 2026-09-24 master push reported four
  default-branch dependency vulnerabilities and linked its [Dependabot alerts](https://github.com/GiftlyKSA/backend/security/dependabot).
  The earlier explicit `uv.lock` production export audited with pip-audit reported
  zero known advisories. GitHub completed a dependency-graph update for the commit,
  but alert details require repository authentication unavailable to this review.
- **Impact if unresolved:** an affected deployed dependency could remain unnoticed,
  or stale/development-only alerts could be mistaken for a runtime defect. Neither
  interpretation is established by the alert count alone.
- **Minimal fix / system impact:** inspect each alert's package, version, advisory,
  scope, and dependency path; reconcile it with the current production export and
  graph update; upgrade compatible vulnerable pins or document/dismiss non-applicable
  entries with evidence. This removes affected artifacts or resolves false signals
  without blind dependency churn.
- **Verification:** repeat the production-lock audit, compare GitHub's refreshed
  alerts to `uv.lock`, run storage/crypto integration checks for any changed package,
  and verify the alert state after the graph refresh.

### SEC-07 — Concurrent chat-socket admission is unbounded

- **Status/severity:** Open / Medium; OWASP A06 and API resource exhaustion.
- **Trigger/evidence:** [chat.py](../app/routers/chat.py) accepts each authenticated,
  authorized WebSocket, allocates a dedicated Redis pubsub and tasks, and performs
  recurring authorization checks. HTTP rate limiting does not cap socket handshakes;
  message throttling runs only after a frame. One account can leave many sockets idle.
- **Impact if unresolved:** finite process, Redis, and database capacity can be
  exhausted. No deployed edge cap or actual outage was established.
- **Minimal fix / system impact:** atomically cap active per-account socket leases in
  shared Redis, renew them, release in `finally`, and expire after crashes. Bound IP
  handshakes and reject when admission cannot be checked. This adds bounded lease
  state while limiting resources across API instances.
- **Verification:** multi-worker cap, idle sockets, disconnect/cancellation cleanup,
  crashed-worker expiry, Redis failure, and revocation timeliness.

### SEC-08 — Upload grants and abandoned objects lack a cumulative bound

- **Status/severity:** Open / Medium; OWASP A06 and API resource exhaustion.
- **Trigger/evidence:** [media_service.py](../app/services/media_service.py) issues a
  new grant for each valid presign request. [media_uploads](../app/models/tables.py)
  stores creation, confirmation, and attachment but no expiry. No grant/object cleanup
  worker was found. An authenticated actor can upload then never confirm or attach.
- **Impact if unresolved:** private objects and grant indexes grow indefinitely.
  At the defaults of 120 requests/minute and 10 MiB/object, one actor can be issued
  URLs for up to 1,200 MiB/minute; throughput, storage policy, and cost are unmeasured.
- **Minimal fix / system impact:** enforce an outstanding grant/byte quota under an
  actor lock, expire unattached grants, reject late claims, and delete abandoned rows
  and objects in bounded idempotent batches. Coordinate claim and cleanup so attached
  evidence cannot be deleted. This bounds retained storage but requires a migration,
  storage-delete interface, and recovery rule.
- **Verification:** quota races, expired confirm/claim, repeated cleanup, claim versus
  cleanup, attached retention, and actual S3 lifecycle/billing policy.

No further concrete SQL/template injection or broken cryptographic primitive was
established in the reviewed paths; that is a limited source finding, not a guarantee.
The owner explicitly authorized authenticated, CSRF-checked, recently reauthenticated
admin CRUD on **all** application tables, including ledger and audit rows. Database
constraints remain active, but direct edits bypass ordinary business transitions.
An editable same-database audit log is not tamper-resistant against a fully authorized
admin. An independent append-only sink is needed if that property is required; this
report does not propose restricting the requested CRUD.

## Performance, scalability, and reliability

### PERF-02 — New-order push is unbounded and precedes commit

- **Status/severity:** Open / High.
- **Trigger/evidence:** [orders.py](../app/routers/orders.py) awaits courier
  notification before the request dependency commits. [device_token_repository.py](../app/repositories/device_token_repository.py)
  loads all eligible city tokens; [notification_service.py](../app/services/notification_service.py)
  sends serial external batches of 500.
- **Impact if unresolved:** API latency, memory, and open transaction time grow with
  city population and push latency. A push can announce an order that later rolls back.
  Population and latency are unmeasured.
- **Minimal fix / system impact:** commit order/upload claims before delivery, then
  dispatch a durable idempotent outbox job that pages recipients with bounded retries.
  This shortens the request transaction and prevents pre-commit push, while adding
  durable delivery state and recovery handling.
- **Verification:** failed commit sends nothing; slow/failing push does not hold the
  request transaction; token paging, retry, and duplicate delivery are bounded.

### REL-05 — Receipt sweep spans row locks and an expiring lease

- **Status/severity:** Open / Medium.
- **Trigger/evidence:** [receipt_service.py](../app/services/receipt_service.py) locks
  an invoice while awaiting email. [receipts.py](../app/workers/receipts.py) handles
  up to 100 serial sends under a 300-second Redis lease without renewal/fencing.
- **Impact if unresolved:** slow sends hold DB connections/locks; a second sweep can
  overlap or wait. Crash-after-send may repeat an email. Provider latency is unmeasured.
- **Minimal fix / system impact:** durable per-receipt claim/retry state, provider I/O
  outside row locks, and a renewed/fenced or work-bounded lease. This reduces lock
  time and duplicate work but adds explicit recovery state.
- **Verification:** slow provider, concurrent sweeps, lease expiry, crash/retry,
  idempotency, and exact receipt transitions.

### REL-06 — Order cancellation leaves an issued invoice and wallet hold pending

- **Status/severity:** Open / Medium; live-payment impact is latent while production
  payment processing remains disabled.
- **Trigger/evidence:** [order_service.py](../app/services/order_service.py) allows a
  `WAITING_PAYMENT` order to become `CANCELLED` without changing its issued invoice or
  releasing the associated wallet reservation. [invoice_service.py](../app/services/invoice_service.py)
  has an explicit invoice-cancel path that releases the reservation, but order
  cancellation does not use it. A subsequent payment callback cannot legally move
  a cancelled order to `IN_PROGRESS`.
- **Impact if unresolved:** invoice and order states disagree and reserved customer
  funds may remain unavailable until the later expiry sweep; payment/callback behavior
  becomes inconsistent if a live gateway is enabled. No live settlement was executed.
- **Minimal fix / system impact:** give order cancellation an invoice-first path for
  `WAITING_PAYMENT`: lock the active invoice and intent, release the held amount and
  promo idempotently, then lock/revalidate the order and cancel both atomically.
  Preserve the invoice→order lock order used by settlement to avoid deadlock. This
  makes cancellation release funds immediately while adding a coordinated transaction.
- **Verification:** PostgreSQL races for cancel versus pay/expiry, repeated cancel,
  correct invoice/order terminal states, and exact wallet/promo balances.

### PERF-03 — Deep admin pages use OFFSET scans

- **Status/severity:** Open / Medium.
- **Trigger/evidence:** [admin/router.py](../app/admin/router.py) accepts pages through
  100,000; [admin_read_repository.py](../app/repositories/admin_read_repository.py)
  uses 50-row `LIMIT` with offset up to 4,999,950.
- **Impact if unresolved:** large tables can incur deep scans and sorting for one page,
  holding a connection. Table sizes/plans are unmeasured.
- **Minimal fix / system impact:** indexed `(created_at, id)` keyset pagination, or
  PK cursor where no timestamp exists. This bounds page work but changes navigation
  from arbitrary page numbers to cursors.
- **Verification:** representative `EXPLAIN (ANALYZE, BUFFERS)` for first/deep pages
  and stable ordering during inserts.

### PERF-04 — Key rotation buffers whole tables

- **Status/severity:** Open / Medium.
- **Trigger/evidence:** [key_rotation_service.py](../app/services/key_rotation_service.py)
  selects all courier profiles, conversations, and withdrawals and mutates ORM rows
  in one transaction.
- **Impact if unresolved:** peak memory, lock duration, and rollback cost grow with
  data; a late failure loses all progress. Data volume is unmeasured.
- **Minimal fix / system impact:** bounded keyset batches with commits and restart
  cursor; retain old keys until verification. This caps resource use and adds resume
  handling.
- **Verification:** large disposable fixture, memory/lock measurement,
  interrupt/resume, decryptability, and exact rotated counts.

### PERF-05 — Refresh-token cleanup has no row or time budget

- **Status/severity:** Open / Medium.
- **Trigger/evidence:** [expiry.py](../app/workers/expiry.py) runs a nightly purge;
  [auth_repository.py](../app/repositories/auth_repository.py) deletes every expired
  token in one transaction. No expiry index or batch bound is present; lease is 120s.
- **Impact if unresolved:** a backlog can produce high WAL, lock contention, and a
  run beyond its lease. Token volume is unmeasured.
- **Minimal fix / system impact:** measure plan, add expiry index if justified,
  delete ordered primary-key batches with per-run budget and intermediate commits.
  This smooths load and requires resume/lease management.
- **Verification:** representative backlog, plan/WAL/lock duration, interruption,
  repeated runs, and token semantics.

### PERF-06 — Admin GET touches its session row every time

- **Status/severity:** Open / Low.
- **Trigger/evidence:** [admin/deps.py](../app/admin/deps.py) extends most GET
  sessions; [admin_session_repository.py](../app/repositories/admin_session_repository.py)
  updates before page work completes.
- **Impact if unresolved:** parallel tabs serialize on one row and navigation writes
  WAL. Effect at current traffic is unmeasured.
- **Minimal fix / system impact:** coarse near-expiry threshold and atomic conditional
  touch after/apart from reads. This cuts writes while retaining sliding expiry.
- **Verification:** concurrent tabs, slow query, near-expiry/cap, and revocation.

### PERF-07 — Worker-owned email client is not closed

- **Status/severity:** Open / Low.
- **Trigger/evidence:** [receipts.py](../app/workers/receipts.py) creates an email client
  per sweep and closes only the DB engine. The real client owns an `httpx.AsyncClient`
  with explicit `aclose`.
- **Impact if unresolved:** pooled HTTP resources may linger until collection across
  repeated jobs; actual retention is unmeasured.
- **Minimal fix / system impact:** close worker-owned clients in `finally` or use a
  defined worker lifespan. Cleanup becomes deterministic.
- **Verification:** success/failure closes owned clients exactly once, not injected ones.

**N+1 assessment:** No new growing-list N+1 was established in reviewed API/admin
serializers: order rating state and related admin labels are batched; inbox previews
are read with the conversation page. Reconciliation now uses one statement returning
discrepancies, removing the original full-wallet Python retention. Its SQL still
groups the full settled ledger; PostgreSQL plan, temp space, and latency on realistic
data are **UNCONFIRMED**. No index change is proposed without measurement.

## Maintainability, readability, naming, and verification

### CI-02 — The master push has no backend CI run

- **Status/severity:** Open verification gap / Medium; trigger root cause **UNCONFIRMED**.
- **Trigger/evidence:** GitHub lists the checked-in `CI` workflow as active, but its
  workflow-run API returned zero historical runs after commit `74f1edc` was pushed
  to master. The only commit check was a successful dependency-graph update.
- **Impact if unresolved:** disposable PostgreSQL/PostGIS/Redis tests, migrations,
  coverage, image build, docs export, and security gates are configured but have not
  verified this commit. Local unit/static checks cannot replace them.
- **Minimal fix / system impact:** inspect repository Actions permissions and workflow
  trigger diagnostics, then start a trusted CI run for this commit (or a follow-up
  commit), correcting workflow configuration if necessary. This supplies the missing
  service-backed evidence; it may require repository administration.
- **Verification:** a completed `CI` workflow on master with green quality, test,
  security, docs, and Docker jobs, including migration and 85% coverage checks.

### TEST-01 — Admin CRUD across all schema shapes lacks database proof

- **Status/severity:** Open verification risk / Medium; actual defect **UNCONFIRMED**.
- **Trigger/evidence:** [test_admin_table_crud.py](../tests/integration/test_admin_table_crud.py)
  renders forms for all 29 tables but persists writes on only a subset. Other unit
  tests cover parsing/relationships/routes without real DB behavior.
- **Impact if unresolved:** a table-specific trigger, encrypted field, PK FK, generated
  default, or type may fail add/edit/delete without a regression detecting it. No
  particular table failure was established.
- **Minimal fix / system impact:** compact PostgreSQL-backed matrix of distinct
  schema shapes and rollback paths, checking persisted values and audit rows. This
  improves evidence without changing runtime behavior.
- **Verification:** CI disposable PostgreSQL/PostGIS and Redis services; no local DB.

The inspected code preserves the route/service/repository direction and Ruff style.
No independent naming-only or comment-only defect with material impact was found;
broad cosmetic rewriting would conflict with the minimal-change rule.

## Resolved source findings and intentional limits

| Original IDs | Resolution | Remaining proof |
| --- | --- | --- |
| SEC-01/02/03/05 | Serialized refresh rotation, live HTTP/WS account checks, credential-version invalidation, atomic OTP script | PostgreSQL/Redis races and live sockets need CI |
| SEC-04 | Owner/purpose upload grants, atomic one-time attachment, create-only signed S3 PUT | Real S3 and DB behavior unverified |
| SEC-06, CI-01, BUILD-01 | Patched dependency lock, explicit production-graph audit, digest-pinned production Python/uv images | Earlier audit found zero known advisories; image build needs CI |
| REL-01 | Registered tasks and dedicated Compose scheduler | Live cron delivery needs CI/deployment |
| REL-02/03, MAINT-01 | Per-intent wallet reservation ownership and service-owned financial expiry/release | Populated migration and races need PostgreSQL CI |
| REL-04, PERF-01, READ-01 | One-statement discrepancy-only reconciliation; precise invoice/worker typing | Realistic query plan unmeasured |
| Final SEC-F01/F02/F04/F05 | All-device logout, refreshed locked order transitions, canonical identity fingerprint, actor quota locks | Focused offline tests pass; real concurrency needs CI |
| Final FPERF-04, MAINT-F02 | SQL purpose filter before expiry LIMIT; corrected README rollback warning | Live backlog/migration test pending |

Logout now revokes the account's access, refresh, and dashboard credentials on all
devices. Existing clients should clear both local tokens after 204. Dhamen remains
selected but has no verified live integration; production payments fail closed. The
owner authorized a clean migration history because no existing database needed
legacy-provider upgrades. The all-table admin interface is a maintenance tool and
does not itself enforce every business invariant after direct edits.

## Verification record and conditions

- **Local 2026-09-24 pass:** unit suite (`pytest tests/unit -q`) with explicit dummy
  test settings; repository-wide Ruff lint and format (219 files); strict mypy over
  142 application source files. Earlier focused checks and offline migration SQL
  rendering are recorded in the development ledger. The first unit run without
  required dummy environment values failed two settings-dependent entrypoint tests;
  rerun with the documented test values passed.
- **Not run locally:** Docker; PostgreSQL/PostGIS/Redis full suite and live migration
  upgrade/downgrade; socket/S3/provider integration; load and query-plan studies.
  The user prohibited local Docker. CI's disposable PostgreSQL and Redis services
  must provide database/image evidence; CI-02 records that this has not occurred.
- **Deployment unknowns:** bucket/IAM/CORS policy for `If-None-Match`, proxy trust
  and access-log redaction, Redis isolation, DB roles, backups, alert routing,
  scheduler singleton/leases, and actual notification delivery are **UNCONFIRMED**.
- **Security stance:** payment processing remains disabled until the Dhamen protocol
  and financial state flow are implemented and reviewed. The repairs reduce known
  defects, but open resource and delivery findings prevent a claim of no flaws.

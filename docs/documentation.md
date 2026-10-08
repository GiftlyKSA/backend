# Giftly backend documentation

**Updated:** 2026-10-04 · **Scope:** implemented backend, administration and workers.

| Reference | Contents |
| --- | --- |
| [High-level architecture](architecture.md) | Components, boundaries, data flows and deployment overview. |
| [API reference](api.md) | Non-admin endpoints, schemas, screens, flows and WebSockets. |
| [Current review](codebase_review.md) | Findings, accepted risks, evidence and verification limits. |
| [Task tracker](tasks.md) | Outstanding work from the current review. |
| [Full OpenAPI](openapi.json) / [non-admin OpenAPI](mobile-openapi.json) | Machine-readable contracts. |
| [Development rules](../AGENTS.md) | Security, performance, style and delivery constraints. |

This document replaces completed plans, dated feature notes and duplicated configuration
guides. Source code and current user decisions take precedence. Production payments
are deliberately disabled: Dhamen is selected but is not a verified live integration.
No claims below establish production capacity, live vendor acceptance or absence of flaws.

## 1. Product and architecture

Giftly connects customers requesting custom gifts with verified couriers in the same city.
A courier accepts an available order, prepares an invoice and performs delivery.
Payment, escrow, settlement, refunds and withdrawal rules belong in backend services.
The repository contains no mobile application.

```text
Customer/courier application      Admin browser
            |                         |
         HTTPS / WebSocket         Jinja2 dashboard
            +-------------+-----------+
                          |
                    FastAPI routers
                          |
                    Domain services
                      /         \
              Repositories     Provider adapters
                    |          SMS / email / push / private S3
                PostgreSQL

              Redis: shared security state, Pub/Sub and Taskiq queue
                         |
               Taskiq worker + one scheduler
```

The application is a modular monolith. Routers/admin call services; repositories own
SQLAlchemy queries; models describe persistence. Shared configuration, cryptography,
money, rate limits and connection primitives live in `app/core/`. Provider adapters live
in `app/integrations/`; jobs in `app/workers/`; migrations in `app/migrations/`.

| Component | Implementation | Responsibility |
| --- | --- | --- |
| Runtime | Python 3.13 in Docker; project supports 3.11+ | Async application and worker processes. |
| HTTP / validation | FastAPI, Pydantic v2 | Typed inputs, explicit schemas and error handling. |
| Server | Uvicorn / Gunicorn | API workers; default image uses three workers, 80-second timeout. |
| Database | PostgreSQL 16, SQLAlchemy 2 async, asyncpg | Durable business state, constraints, row locks and audit changes. |
| Schema changes | Alembic | Forward versioned migrations before serving traffic. |
| Shared state | Redis 7 | OTP HMACs, rate windows, JWT revocation, connection/decoder leases, Pub/Sub. |
| Jobs | Taskiq with Redis broker | Bounded background maintenance and delivery retries. |
| Admin | Jinja2, packaged CSS/JavaScript | Authenticated table maintenance, activity and overview. |
| Private media | S3-compatible adapter and signed CloudFront access | Owned upload grants and temporary playback links. |
| Dependencies | `uv`, `uv.lock` | Reproducible runtime and development graph. |

Cloudflare, Cranl secrets storage, backups and production ingress settings are deployment
choices. Their actual configuration is **UNCONFIRMED**; they are not implemented by an
architecture drawing or a local fake.

## 2. Data model and invariants

| Domain | Main tables and relationships |
| --- | --- |
| Accounts | One `users` table for CUSTOMER, COURIER and ADMIN. Optional gender, birth date, name and email; unique phone and seven-digit public identifier. |
| Courier extension | `courier_profiles.user_id` references a courier user; encrypted national/passport number, city FK and verification state. No fingerprint or duplicate-document rule. |
| Cities | `cities`: UUID, English `name`, Arabic `name_ar`, unique shortcut, active flag and creation timestamp. |
| Orders | Customer and courier user FKs, city FK, delivery date, optional plain-text address note and lifecycle. No geometry, coordinates, radius, distance or map URL. |
| Billing | Versioned `invoices`, `invoice_items`, promos/redemptions and idempotent invoice-promo operations. |
| Money | User/system wallets, double-entry transactions, payment intents, topups, payouts and withdrawals. |
| Chat | An order conversation references customer/courier users; encrypted messages and private attachments reference the conversation. |
| Customer dates | Owner-scoped `occasions` for personal dates and anniversaries. |
| Reviews | One order-associated customer rating for a completed delivery; courier summaries are derived from ratings. |
| Delivery bookkeeping | Notification claims and recipient snapshots; upload grants and cleanup claims; receipt claims on invoices. |
| Activity | Metadata audit rows for committed changes, attributed to SYSTEM, ADMIN, CUSTOMER or COURIER. |

UUID identifiers and creation/update/deletion timestamps are generated or managed by the
backend/database, and are read-only in admin forms. Optional `gender` uses MALE, FEMALE,
OTHER or PREFER_NOT_TO_SAY. `DELETED` disables authentication; deletion retains the date
and a reason. Encrypted identification numbers remain required for courier onboarding,
but the same number is no longer used to reject another account.

Database timestamps use timezone-aware UTC. Admin display and datetime inputs use Riyadh
UTC+3, translating filters back to UTC. APIs return ISO timestamps; clients localize the
display. Birthdays, occasion dates and delivery dates are calendar dates, not instants.

Money uses `Decimal` and two-place quantization through `app/core/money.py`. API amounts
and rates are decimal strings. Invoice/order totals, commission and payout are derived
through pricing and settlement, not trusted client totals. Courier-entered item prices
are final prices; the platform adds no separate VAT. The current review identifies rounding and
reservation-concurrency defects that need follow-up; these invariants are not a claim
that every edge case has been proven.

User creation receives a wallet through the database-backed creation path, including
administrative creation. System wallets represent escrow obligations, platform revenue,
gateway clearing; they are internal accounting buckets, not user roles.

## 3. Authentication, authorization and safe input

Phone OTP request/verification uses Redis Lua scripts for atomic counters and single use.
Only an HMAC is stored, with a default 60-second expiry and at most five verification
attempts. Development returns a random five-digit numeric `otp_dev`; other modes use
six-digit codes and do not expose them. A new phone receives a registration token;
existing accounts receive access/refresh tokens and their role.

Access JWTs validate configured algorithm, issuer, audience, expiry, current role,
deletion/ban status, credential version and revocation. Refresh tokens are stored as
hashes, rotate and detect family reuse. Logout invalidates account credentials on all
devices. Current review SEC-18 records the remaining access-token consequence of replay.
Admin dashboard login uses environment-managed credentials and production-only TOTP,
server-side sessions, CSRF protection and secure cookie settings.

Service checks prove role, current eligibility and object ownership. Courier acceptance
uses a conditional database claim and restricts orders to the courier's verified city.
Chat, order streams, history, invoices, media and customer occasions are participant/
owner scoped. UUID complexity alone supplies no authorization.

Inbound models reject undeclared fields and constrain identifiers, pagination, amounts,
types and upload sizes. SQL is parameterized; admin dynamic sort/filter fields come from
table metadata allowlists. Jinja escapes text. JSON stores and returns literal user text,
including code-like strings: clients must render it as text rather than HTML, execute it
or interpret it as a template. Removing characters is not an XSS defense.

Default HTTP body limit is 1 MiB, enforced on actual received bytes before request handlers.
Chunked bodies remain rejected by the existing guard. With HTTP throttling enabled, Redis
failure returns `503 RATE_LIMIT_UNAVAILABLE`; it does not disable the limiter.

| Bucket | Default | Identity |
| --- | --- | --- |
| Authenticated public HTTP | 60 / 60 seconds | Verified bearer-token identity. |
| Anonymous public HTTP | 30 / 3,600 seconds | Client IP, subject to trusted proxy configuration. |
| Admin routes/dashboard | 100 / 60 seconds | Admin bearer identity or cookie-only client IP. |
| Chat messages | 30 / 60 seconds | Authenticated user; separate frame cap. |
| OTP issuance | 3 / 300 seconds; 1,800-second block | Phone number. |

Health probes and CORS preflight are exempt. Rate windows are shared across processes.
An edge firewall can complement these controls; it cannot replace application ownership
checks, transactional money invariants or authenticated callbacks.

## 4. Administration and activity

### Database export

Open **Database backup** on the admin homepage. Download plain `giftly.sql` or
password-encrypted `giftly.sql.enc` (8–1024 characters). Both capture the configured
PostgreSQL database schema and data through `pg_dump`, including tables, rows,
constraints, indexes, sequences, functions, triggers and Alembic history. Stored
encrypted fields are preserved as ciphertext. The dump includes database creation
and ownership/privilege statements; cluster roles must already exist when restoring.
Redis state, S3 files, deployment secrets and PostgreSQL cluster roles are separate
backups. Preserve field encryption keys separately to read encrypted data after restore.

Exports require an active admin session and CSRF validation. One export runs at a
time across replicas. A 60-second dump timeout and 512 MiB limit fail explicitly;
there is no successful partial download. Temporary files are private and removed
after response completion/disconnection or handled failure. Hard process termination
can leave temporary files until the ephemeral container storage is removed.
The Docker runtime includes `postgresql-client`; use a client compatible with the
deployed PostgreSQL server. Container build and real dump/restore verification remain
deployment checks; do not run backups through a transaction-pooling endpoint if the
provider requires a direct PostgreSQL connection.

Decrypt locally without importing or executing SQL:

```text
uv run --locked python -m app.decrypt_backup giftly.sql.enc giftly.sql
```

The command prompts for the password and refuses to overwrite an existing output.
Format version 1 is `GIFTLYSQL1` (10 bytes), scrypt salt (16 bytes), GCM nonce
(12 bytes), ciphertext and GCM tag (16 bytes). AES-256-GCM authenticates the header
and SQL; scrypt uses N=32768, r=8, p=1 and a 32-byte key. Keep the password separately.
Admin import is deferred; restoring SQL can replace data and requires a reviewed
restore procedure and disposable-database verification first.

The dashboard is `/v1/admin/admin`. Generic table pages support authenticated authorized
CRUD, relationship selectors, allowlisted filters/sorting and 25/50/100-row pagination.
Datetime fields use compatible pickers with UTC+3 input. Arabic is the default language;
language and black/charcoal dark theme persist, with theme changes applied in place.
Generated assets are content-versioned to avoid old CSS/JavaScript after deployments.

Activity uses one page with SYSTEM (default), ADMIN and user tabs. Other tabs load on
demand. Filters include actor/action/entity/activity and UTC+3 date bounds; labels show
understandable record names where available. PostgreSQL triggers audit committed row
changes with actor category, changed field names and entity identifiers, without storing
raw HTTP logs, field values, OTPs or secrets. Rolled-back writes do not create committed
activity. Reads, failed requests, login/logout and job start/completion are excluded from
the database action history; operational errors still use application logging.

**User-approved exception:** privileged admins retain CRUD over audit and financial
records. Maintenance therefore bypasses some normal immutability rules. These are
documented accepted High risks (SEC-16/SEC-17), not resolved security guarantees.
Independent backups/audit export and reconciliation are recommended but not proven here.

## 5. Media and real-time behavior

General order/proof images use signed, owner/purpose-scoped upload grants and explicit
confirmation. Chat has signed uploads for IMAGE, VIDEO and VOICE, then a send operation.
Voice notes are microphone recordings in the client product flow; a server cannot prove
that a valid audio file originated from a microphone. Camera input uses the same image/
video contract. Clients read current limits from `/api/chat/media-limits`.

| Kind | Default maximum | Accepted media |
| --- | --- | --- |
| IMAGE | 10 MiB | JPEG, PNG. |
| VIDEO | 120 MiB, 120 seconds | MP4, WebM; constrained decoded dimensions. |
| VOICE | 10 MiB, 120 seconds | Supported audio containers/MIME values in OpenAPI. |

Private objects use create-only upload headers, verified metadata/decoded content,
ownership checks and short-lived signed playback links. S3 waits are outside production
request write transactions; prepared metadata is rechecked against a fresh locked grant
after reauthentication. Cleanup and claim races must be verified on PostgreSQL.

Image decoding is limited to 20 million pixels. Recording validation needs FFmpeg/
ffprobe, with allowlisted protocols/demuxers, bounded output, process deadlines and Redis
admission. Missing validators fail closed. Validations materialize bounded content in
memory and use temporary files for recordings. Cancellation currently has an image-thread
admission flaw (PERF-10); existing limits do not prove sustained safe capacity.

REST chat history is paginated and remains available for past orders to eligible
participants. Live chat uses `/api/ws/conversations/{conversation_id}`. Order status
uses `/api/ws/orders/{order_id}`, with committed snapshots, Pub/Sub hints and periodic
reconciliation. Reconnect through REST; Redis Pub/Sub is not durable event storage.
Committed chat sends retain their successful response when notification delivery fails.
There is no durable client-send idempotency/outbox guarantee yet.

Use secure transport. Prefer supported Authorization/subprotocol authentication for the
order stream. The chat stream still uses a token query parameter; ingress/query logging
redaction remains deployment work. Never log live socket URLs containing credentials.

## 6. Configuration and secrets

[`.env.example`](../.env.example) is the grouped source for supported variable names,
short descriptions and public testing defaults. Every entry is consumed by Settings,
the process entrypoint or Gunicorn/Uvicorn. `IDENTITY_FINGERPRINT_PEPPER` is no longer
used or required. With RSA JWTs and no JWT HMAC secret, a separate `OTP_HMAC_KEY` is
required; failure to provide it prevents startup.

| Group | Main controls |
| --- | --- |
| Process/data | ENVIRONMENT, DEBUG, LOG_LEVEL, DATABASE_URL, REDIS_URL. |
| Pools/timeouts | DB_POOL_SIZE, DB_MAX_OVERFLOW, DB_POOL_TIMEOUT_SECONDS, INTEGRATION_HTTP_TIMEOUT_SECONDS. |
| Authentication | JWT_*, OTP_*, ADMIN_* and REFRESH_TOKEN_RETENTION_DAYS. |
| Encryption | FIELD_ENCRYPTION_KEYS version map and FIELD_ENCRYPTION_KEY_VERSION. |
| Private storage | AWS_*, S3_BUCKET_NAME, CLOUDFRONT_*. |
| Communication | SNDR_*, SMS_PROVIDER_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY. |
| Boundaries | CORS_ALLOWED_ORIGINS, RATE_LIMIT_*, MAX_REQUEST_BODY_BYTES, WS_*, CHAT_*. |
| Business rules | Service fee, commission, invoice/item, topup, withdrawal and expiry controls. |
| Server deployment | WEB_CONCURRENCY, GUNICORN_TIMEOUT, FORWARDED_ALLOW_IPS. |

Pool defaults are 5 persistent + 10 overflow per process, with a 30-second acquisition
timeout. Three API workers can therefore consume 45 connections before workers, replicas
and maintenance are counted. Provider HTTP timeout defaults to 10 seconds (maximum 15).
The setting does not change S3/Redis deadlines or establish a total operation deadline.
Process settings are cached: redeploy/restart consumers after changing environment values.

Use ignored `.env` only for local development. Published testing keys must be replaced
before production or storing real data. `SecretStr` hides representations; it does not
encrypt storage. Inject real credentials at runtime from a managed secret store with
access control and rotation. Verify Cranl's capabilities; they are UNCONFIRMED. Do not
commit keys, bake them into images, show them in logs or copy them into this document.

Version encryption keys and retain old versions while old ciphertext exists. Rotate
with a backed-up, reviewed data plan. Rotate JWT/admin/OTP signing independently and
account for session invalidation. Protect storage/CDN, sending-domain and provider keys
with the same operational controls. No active Dhamen configuration is advertised until
a verified adapter exists. `SNDR_INVOICE_PAID_TEMPLATE_KEY` is a logical adapter label;
the sndr.sh adapter currently composes its own email rather than selecting a vendor template.

## 7. Deployment, migrations and maintenance

Provision the PostgreSQL database and Redis service first. Alembic creates/upgrades
tables inside an existing database; it does not create a PostgreSQL server, database,
role, network or password. The application does not call `create_all` at worker startup.

The default container entrypoint runs `app.bootstrap_db` once in the parent process,
then replaces itself with the configured server command. Failure prevents the server
from starting. PostgreSQL advisory locking serializes migration runners across replicas.
Compose instead has a dedicated `migrate` service; API/worker/scheduler bypass their
inherited entrypoint and wait for successful completion. A custom PaaS start command
must preserve the migration gate or use an equivalent separate release phase.

Initial migrations seed system wallets and 20 active Saudi cities on a fresh catalog.
The idempotent `app.seed_cities` command fills missing defaults and refreshes Arabic
names without reactivating intentionally inactive existing rows.

| Command from backend root | Purpose |
| --- | --- |
| `uv sync --locked --dev` | Install the checked-in dependency graph. |
| `uv run --locked python -m app.bootstrap_db` | Apply pending schema migrations. |
| `uv run --locked python -m app.seed_cities` | Repair missing/default city catalog entries. |
| `uv run --locked python -m app.seed` | Idempotent system-wallet and city seed. |
| `uv run --locked taskiq worker app.workers.broker:broker` | Process queued jobs. |
| `uv run --locked taskiq scheduler app.workers.scheduler:scheduler` | Enqueue registered scheduled jobs. |

Maintain one scheduler per deployment. Receipts sweep every five minutes; new-order
notifications every minute; payment/invoice expiry every ten minutes; auto-approval
every fifteen minutes; media/token cleanup hourly; reconciliation daily. Overdue NEW
orders are cancelled at Riyadh midnight using bounded locked batches. Worker logs,
queue lag, retry state and reconciliation differences need operational monitoring.

### Current migration rollout and rollback

Back up data and stop incompatible writers before applying upgrades. Deploy the current
image, migrate to `0018_remove_identity_fingerprint`, then restart API/worker/scheduler.

| Revision | Change | Rollback considerations |
| --- | --- | --- |
| 0015 | Private chat media type/duration constraints | Old code cannot consume VOICE; downgrade refuses incompatible attachments. |
| 0016 | Durable notification recipients and audit trigger | Incomplete legacy sweeps restart; rollout/rollback can repeat one previously sent page. |
| 0017 | Gender, DELETED and reason metadata | Downgrade refuses populated gender/reasons; export and review before clearing. Deleted timestamps remain. DELETED enum label is retained unused. |
| 0018 | Remove derived fingerprints/index | Encrypted numbers remain. Downgrade creates nullable fingerprints; historical duplicate detection is not restored automatically. |

Historical migrations retain names needed to upgrade existing databases. Prefer forward
correction or backup restoration to destructive downgrade. Offline SQL generation is
verified; migration application, locking and rollback on PostgreSQL remain UNCONFIRMED
locally. SQL/constraint changes must be exercised on a disposable deployment database.

### Invoice PDFs and paid receipts

Order participants can download `GET /api/invoices/{invoice_id}/pdf`; authenticated
admins have an invoice-detail download. PDFs use stored values, English labels and no
remote assets. Receipt sends attach the PDF using sndr.sh with stable invoice idempotency
keys and durable claims. A verified sender/domain and real vendor acceptance are required.
Neither provider idempotency retention nor inbox delivery is proven by a fake.

Courier-entered unit prices are final, including any supplier VAT. No separate tax
rate or amount is accepted, calculated, returned or printed. Service/courier fees,
promo discounts, commission and decimal-string amounts retain their existing rules.
The obsolete VAT repair command has been removed.

Migration `0024_remove_invoice_vat` removes separate tax columns, the unused tax
wallet and tax transaction enum values. Earlier migrations are unchanged. It acquires
table locks with a five-second lock timeout and refuses any tax-bearing financial
records rather than rewriting payments or ledger history. Apply to a clean disposable
pre-production database first; back up any existing development data before migration.
If the guard fails, explicitly reconcile or reset disposable data in a separately
approved operation. No live database cleanup is performed by this release.
Downgrade restores zero-valued tax fields, enum options and the empty system tax wallet
without inventing charges. Old mobile clients must
stop sending `tax_rate` before invoice creation against the new strict schema.

## 8. Verification and scalability work

Run Ruff lint/format checks, strict mypy, both pre-commit stages, full pytest and API drift
tests. CI supplies disposable PostgreSQL/Redis, coverage checks, dependency auditing,
schema generation and image/decoder checks. Local checks do not prove a new remote CI run.
See the current review for actual command results and unverified boundaries.

Measure query counts/plans, database pool waits, transaction durations, Redis/queue
latency, socket population, temporary storage and media memory/CPU under representative
load. Avoid N+1 reads, unbounded fanout and hidden lazy loading. Recipient snapshots
provide correctness but can write many recipient/audit rows in one claim; capacity must
be measured. Scaling replicas requires shared claims/leases, durable idempotency and
bounded work, not simply increasing pool sizes or worker counts.

Five Markdown references are maintained in `docs/`; OpenAPI JSON and the dashboard
concept image are supporting artifacts. UI-agent prompts are delivered in chat only.


## Mobile capability proposal — 2026-10-07 — awaiting approval

This section is a design proposal, not an available API contract for every feature.
The first core read batch implements invoice lists, dated wallet statements and order
media reads; the remaining capabilities below are pending. Before work began, the
remote `master` baseline was verified as
`c826d0005ae39644f39ae59ba2e38fcf48ccc338` using `git ls-remote`. Actual PaaS deployment
is **UNCONFIRMED**. The committed mobile specification at that revision is the pushed
baseline. Other sections and workspace specifications contain uncommitted payment
changes and must not be treated as released contracts. Mobile screen names below
come from the requested screen inventory; the mobile repository was not changed.

### Inventory and reuse

| Capability / screen | Pushed operations to reuse | Remaining work |
| --- | --- | --- |
| Courier home / earnings reports | Own wallet and transaction history | Earnings aggregates, charts, comparison, targets |
| Courier report settings | Own profile read/update | Target and supported notification preferences |
| Wallet statement | `GET /api/wallets/me/transactions` | Date filtering, period totals, safe links/descriptions; document export optional |
| Notifications | Device registration; best-effort push; new-order delivery outbox | Owned persistent inbox, unread counts and read actions |
| Customer occasions | `/api/occasions` CRUD and inclusive date filters | Actual reminder delivery; recurrence optional |
| Courier calendar | `/api/orders` assigned-order date/status filters | Independent appointments, only if approved |
| Wallet invoices | Invoice detail, active order invoice, invoice PDF | Account-wide list; no pushed order invoice-history list |
| Courier avatar / bank | Profile contains nullable avatar URL; encrypted IBAN in withdrawals | Avatar always returns null; no avatar mutation or saved bank profile |
| Change phone | Login OTP and Saudi phone normalization | Separate authenticated change challenge; do not repurpose login verification |
| Delete account / terminate contract | Internal DELETED account fields | No mobile submission/status operation or approved business process |
| Customer order search | `/api/orders` date/status filters and cursor | Text search; no defined order number (UUID is the order identifier) |
| Order timeline / photos | Order details, live order WebSocket; media upload/confirm; chat attachment reads | Historical status timeline and authorized order-media reads |
| Top-up recovery | `POST /api/wallets/topup`; own wallet/history | Pushed creation has no idempotency header or recovery operation |

The 2026-10-08 release implements owned order/top-up recovery, session reads,
provider refresh and confirmed cancellation; see the API reference. Creation responses
add status/reuse/intent metadata and an optional strict wallet toggle. Deployment and
live-provider behavior require separate verification; production payments stay disabled.
The earlier proposal inventory is historical; later released-operation sections and
generated OpenAPI supersede its remaining-work labels.

### Common proposed contract

- Every new operation requires a verified Bearer access token. Actor and wallet IDs
  come from authentication. No request accepts an owner, role, balance or calculated total.
- Default eligibility: active non-deleted customers; active verified couriers. Existing
  profile/verification routes keep their current rules. Access to historical financial
  records for suspended/rejected couriers needs a separate product decision; do not
  silently weaken the existing wallet eligibility boundary.
- `UUID` means a UUID string; `timestamp` means ISO-8601 with UTC offset; `date` means
  strict Gregorian `YYYY-MM-DD`; `money` means a finite decimal string with exactly
  two fractional digits on output. Currency is `SAR`. No JSON floating-point money.
- New write schemas reject unknown fields. User text remains text: escape at rendering,
  never execute it or strip arbitrary code. Validate lengths and control characters
  where needed. Clients render descriptions/titles without HTML interpretation.
- New lists use `limit: integer 1..100`, default 25, and an opaque signed cursor or
  existing UUID cursor where compatible. Use a matching compound indexed keyset,
  fetch at most limit+1, and return `next_cursor: string|null`. New opaque cursors bind
  account, filters, ordering and snapshot. Reset a cursor when filters change.
- Existing list limits/order/cursor semantics remain unchanged when filters are omitted.
  Do not change all existing endpoints to the new default. Dates on orders/occasions
  remain direct DATE comparisons without timezone conversion.
- Financial timestamp ranges use proposed fixed `Asia/Riyadh`: inclusive local dates
  converted to `[from_date 00:00, day-after-to_date 00:00)` UTC predicates. For example
  October 1 means UTC September 30 at 21:00 through October 1 at 21:00 exclusive.
  Responses state timezone and bounds; persisted instants and timestamp output remain UTC.
- Errors retain `{"error":{"code":"NOT_FOUND","message":"…","request_id":"…"}}`.
  Missing/foreign objects return 404; invalid authentication 401; ineligible role/account
  403; conflicting state/version/idempotency 409; malformed input/reversed dates 422.
  Schema 422 may retain FastAPI `{"detail":[...]}`. 429 includes `Retry-After` seconds:
  wait that duration, do not loop or retry a financial write with a new key. Dependency
  failures return safe 503; production payments retain `PAYMENTS_DISABLED`.
- Load screens with a skeleton, show empty lists/charts explicitly, preserve input on
  validation failures, refresh after a stale cursor/version, and offer deliberate retry
  after dependency failures. Reads must never turn permission failures into empty success.
- Start with bounded indexed SQL and **no new Redis response cache**. Financial/inbox
  reads use `Cache-Control: private, no-store`; eligibility is checked on every call.
  Only add account/filter/version-scoped short-lived cache after measuring benefit.
  Mutation invalidation must cover services, workers and authorized admin CRUD. Existing
  unrestricted financial/audit admin mutations limit report immutability guarantees.

### 1. Courier earnings: home and reports

**New proposed:** `GET /api/couriers/me/earnings?from_date=2026-10-04&to_date=2026-10-10&granularity=day`.
Courier-only. Both dates required; granularity `day|week|month`, maximum 366 days and
366 buckets. Reject reversed ranges/unsupported granularity with 422. No pagination.
Suggested weeks are Sunday–Saturday in Asia/Riyadh; months are calendar months.
Zero-fill buckets, clip first/last buckets to requested bounds. Previous comparison
uses the immediately preceding equally long date range; UI sends a full calendar
month when it wants a monthly selection (previous comparison is equal days, not
automatically the previous calendar month). A calendar-month comparison selector
would require an explicitly approved different policy.

Proposed response example (all fields required unless shown null):

```json
{"currency":"SAR","timezone":"Asia/Riyadh","from_date":"2026-10-04","to_date":"2026-10-10","basis":"SETTLED_COURIER_PROCEEDS","settled_total":"700.00","pending_total":null,"reversed_total":"0.00","previous":{"from_date":"2026-09-27","to_date":"2026-10-03","settled_total":"500.00","difference":"200.00","percentage":"40.00","comparison":"INCREASE"},"granularity":"day","buckets":[{"from_date":"2026-10-04","to_date":"2026-10-04","amount":"100.00"}],"target":{"enabled":true,"period":"WEEK","amount":"1000.00","period_start":"2026-10-04","period_end":"2026-10-10","settled_total":"700.00","progress_percent":"70.00"}}
```

Example buckets are abbreviated; actual output contains every bucket. Percentage is
a decimal string, null when the previous total is zero: comparison `NO_BASELINE` when
current is nonzero, `UNCHANGED` when both are zero. Otherwise `INCREASE|DECREASE|UNCHANGED`.
Progress uses the complete current target period, not an arbitrary report subset;
return null for disabled target, retain earned amounts exceeding the target and allow
progress over 100%. Label it proceeds unless profit accounting is approved.

Evidence: `MoneyService.release_escrow_on_completion` and `split_escrow` credit owned
`ESCROW_RELEASE` entries. `core/pricing.py` computes payout from item net plus courier
fee less commission; item reimbursement is included. Wallet balance also includes
top-ups/withdrawals and is not earnings. Do not recompute historical payout using
today's commission. Pending amounts are null until an authoritative accrued-proceeds
source exists; do not equate paid customer escrow with courier earnings.

Approval required for proceeds versus fee-only profit and correction classification.
Define net signed settlement entries plus explicit linked compensating adjustments;
exclude top-ups, withdrawals and unrelated rewards. PENDING/REVERSED entries never
contribute to settled total. Add a true `settled_at` if settlement can follow creation;
`updated_at` is not an immutable settlement date. Do not fabricate historical times.
SQL aggregates/grouping, no loading full ledger; existing wallet/created index is useful,
but a settlement-time index/backfill needs measured plans. Test fee/dispute accounting,
pending/reversed exclusion, corrections, previous zero, timezone boundaries and isolation.
Possible 15-second private cache only after measurement, invalidated after settlement,
correction and target updates. Home maps total/comparison/target; report maps buckets.

### 2. Courier report settings

**New proposed:** `GET` and `PATCH /api/couriers/me/report-settings`, eligible courier-only.
PATCH example (required optimistic version; omitted preferences stay unchanged):

```json
{"version":1,"target_enabled":true,"target_amount":"1000.00","target_period":"WEEK","financial_notifications":{"earnings_settled":true,"withdrawal_status_changed":true}}
```

GET/PATCH output:

```json
{"version":2,"updated_at":"2026-10-07T09:00:00Z","target_enabled":true,"target_amount":"1000.00","target_period":"WEEK","financial_notifications":{"earnings_settled":true,"withdrawal_status_changed":true},"supported_financial_events":["EARNINGS_SETTLED","WITHDRAWAL_STATUS_CHANGED"]}
```

Types: version positive integer; enabled/preferences booleans; amount positive money
when enabled, maximum proposed `1000000.00`; period `WEEK|MONTH`; disabled initial
target amount/period may be null. Version conflict 409; invalid/null enabled target 422.
Notifications are offered only when a durable event/inbox delivery implementation is
available. Until then omit functional switches and supported events, not a fake success.
Propose in-app delivery always, optional push where registered; approve events first.
New owned settings row with version; no date filtering/pagination/cache initially.
Tests cover lost updates, ownership, eligibility, target math and real notification dispatch.

### 3. Wallet statement

**Extend** `GET /api/wallets/me/transactions` with optional from_date/to_date, retaining
its default limit 20 and UUID cursor. **New:** `GET /api/wallets/me/statement` with
required date bounds, maximum 366 days, cursor and limit default 25. Eligible customers
and couriers, own wallet only. No initial type/status filters: avoid totals ambiguities.

```json
{"currency":"SAR","timezone":"Asia/Riyadh","from_date":"2026-10-01","to_date":"2026-10-31","as_of":"2026-10-07T09:00:00Z","totals":{"settled_credits":"700.00","settled_debits":"100.00","settled_net":"600.00","pending_credits":"0.00","pending_debits":"0.00","reversed_credits":"0.00","reversed_debits":"0.00"},"items":[{"id":"11111111-1111-4111-8111-111111111111","amount":"700.00","type":"ESCROW_RELEASE","status":"SETTLED","balance_after":"700.00","created_at":"2026-10-06T09:00:00Z","description":"Order settlement","order_id":"22222222-2222-4222-8222-222222222222","invoice_id":null,"payment_intent_id":null}],"next_cursor":null}
```

Totals are whole-range SQL sums, not current-page sums; credits/debits are nonnegative
money strings, net signed. Existing enum transaction types remain authoritative.
Links UUID|null; description string|null with safe server-authored presentation.
Workspace already adds links: review/reuse rather than duplicate. Order by created_at
DESC,id DESC; ensure anchors belong to same wallet/date scope. Consistent totals/pages
need a documented `as_of` cutoff plus ledger change version: reject/restart a snapshot
when pending statuses or corrections change. READ COMMITTED alone does not freeze
multiple HTTP pages. No promised opening/closing balance: historical held balances are
not reconstructed by this schema. No PDF/export until approved. Existing wallet/time
index; evaluate plans for aggregation. Tests: exact signs/status sums, same-day UTC+3,
unchanged multipage dataset, snapshot changes, foreign anchors and no full-memory scan.

### 4. In-app notifications

**New:** `GET /api/notifications?unread_only=true&limit=25&cursor=…`,
`GET /api/notifications/unread-count`, `POST /api/notifications/{id}/read`,
`POST /api/notifications/read-all`. Active customers/eligible couriers; actor-owned rows.
No POST body for read actions. List newest created_at/id first, opaque scoped cursor.

```json
{"items":[{"id":"11111111-1111-4111-8111-111111111111","type":"ORDER_STATUS_CHANGED","title_key":"notification.order_status_changed.title","body_key":"notification.order_status_changed.body","parameters":{"status":"ASSIGNED"},"created_at":"2026-10-07T09:00:00Z","read_at":null,"navigation":{"kind":"ORDER","order_id":"22222222-2222-4222-8222-222222222222"}}],"next_cursor":null,"unread_count":1}
```

Unread-count response `{"unread_count":1}`; read-one returns the item with UTC read_at;
read-all `{"marked_count":1,"read_at":"2026-10-07T09:01:00Z"}`. Counts nonnegative ints;
parameters are typed per allowlisted event, not arbitrary HTML/provider payloads.
Navigation allowlist `ORDER|OCCASION|WALLET`, only relevant typed IDs; destination read
must reauthorize. No PII/chat text in push. New owned inbox with deduplication key,
owned-time and unread indexes; read-all uses one bounded-condition SQL update, not a
per-item loop. New arrivals after its cutoff remain unread. Outbox records/in-app insertion
share the originating transaction; push failure does not lose inbox data. Define retention
before production. Tests: foreign read, duplicate event, idempotent reads, concurrent
read-all/new insert, unread paging and revoked destination. No Redis cache initially.

### 5. Occasion reminders and recurrence

**Reuse** existing POST/GET `/api/occasions`, GET/PATCH/DELETE `/api/occasions/{id}`.
Customer-only. Current fields: title string 1..120, occasion_date date,
reminder_days_before integer 0..365; response adds UUID and UTC created/updated_at.
Lists use date ASC,id ASC, default limit 20; inclusive from_date/to_date already pushed.
Example creation `{"title":"Anniversary","occasion_date":"2027-02-01","reminder_days_before":7}`.

Proposed additive request fields `reminder_enabled: boolean` and `recurrence: NONE|ANNUAL`;
output also `timezone: "Asia/Riyadh"`, `next_occurrence_date: date|null`,
`next_reminder_at: timestamp|null`. Example additions:
`{"reminder_enabled":true,"recurrence":"NONE","timezone":"Asia/Riyadh","next_occurrence_date":"2027-02-01","next_reminder_at":"2027-01-25T06:00:00Z"}`.
Do not activate existing stored reminders automatically without an approved rollout.
Recommend opt-in in-app plus registered-device push at 09:00 Riyadh, not SMS/email.
Permission denied/no device still yields in-app notification. Bounded worker batches,
unique occasion/occurrence/channel deduplication, leased retries with backoff and finite
cutoff; delivery cannot guarantee exactly-once external push after uncertain timeouts.

Annual recurrence and February 29 (`FEBRUARY_28|MARCH_1|LEAP_YEARS_ONLY`) need approval.
Keep saved-occasion list semantics stable; if recurrence approved, add explicit
`GET /api/occasions/occurrences?from_date=…&to_date=…` (max 366 days) with bounded
expanded rows `occasion_id, occurrence_date, title` and occurrence-date/id cursor.
Do not silently change saved occasion IDs/dates into virtual records. Reminder schedule
and dedup/outbox migration needed; test leap dates, edits/cancellation, retries, date
filters and ownership. No response cache initially; any later range cache invalidates
on actual occasion/recurrence changes, not unrelated new orders.

### 6. Independent courier appointments — optional decision

If approved, **new** POST/GET `/api/couriers/me/appointments`, GET/PATCH/DELETE
`/api/couriers/me/appointments/{id}`. Eligible courier-only. Proposed date-only entries
match current delivery calendar; no invented duration/status/reminder.
POST `{"title":"Supplier visit","appointment_date":"2026-10-10","notes":null}`;
PATCH accepts supplied title/date/notes, at least one; notes nullable max 2000,
nonblank title 1..120. Response same fields plus UUID, UTC created_at/updated_at.
DELETE 204. List `{items:[appointment],next_cursor:null}`, inclusive optional from/to,
default 25; appointment_date ASC,id ASC. UI uses distinct appointment and order IDs.
Owned appointments table/index `(courier_id,appointment_date,id)`; no cache initially.
Test all CRUD ownership, equal-date pages, bounds and courier eligibility. If timed
appointments are desired instead, approve timestamp/duration/overlap semantics first.

### 7. Account-wide invoice list

**New:** `GET /api/invoices?status=PAID&from_date=2026-10-01&to_date=2026-10-31&include_historical=false&limit=25`.
Eligible customer/courier, join orders scoped to their own participation. Dates mean
issued_at in Riyadh, not delivery_date. Optional dates/status; status uses existing
invoice enum `DRAFT|ISSUED|PAID|CANCELLED|EXPIRED|REFUNDED`.
Default current invoice per order; historical flag explicitly includes replaced/cancelled
revisions. Return `is_current` to explain duplicate-looking orders; no invented number.

```json
{"items":[{"id":"11111111-1111-4111-8111-111111111111","order_id":"22222222-2222-4222-8222-222222222222","status":"PAID","currency":"SAR","total_amount":"125.00","issued_at":"2026-10-06T09:00:00Z","expires_at":"2026-10-08T09:00:00Z","is_current":true}],"next_cursor":null}
```

Issued/expiry timestamps nullable for unissued historical records; define drafts visibility
as courier-only if approved. Deterministic created_at DESC,id DESC initially, explicit
issued_at date predicate; use existing detail/PDF paths on tap. Batched joined reads,
no per-row invoice items. Inspect order ownership/invoice time indexes before adding
an index. Tests: revision replacement, cancelled histories, draft visibility, foreign
participant, status/date/cursor intersections. No initial cache; later 15-second cache
invalidates on issue/promo replacement/cancellation/payment and admin corrections.

### 8. Courier avatar and optional bank profile

**Avatar proposal:** extend existing `/api/media/upload-urls` purpose with
`COURIER_AVATAR` (courier-only), retain secure direct PUT and `/api/media/confirm`.
Then PUT `/api/users/me/courier-avatar` body `{"storage_key":"<issued-owned-key>"}`;
output `{"avatar_url":"https://<signed-private-asset>","expires_at":"2026-10-07T09:05:00Z","version":1}`.
DELETE same path 204 removes reference, background cleanup deletes unreferenced object.
JPEG/PNG only, existing 10 MiB cap, decoder/pixel limits and immutable confirmed object;
never accept arbitrary URLs or another user's key. Existing profile/participant URL fields
can be populated after their existing authorization checks. Signed URL expires within
five minutes. Customer upload is excluded. Migration extends media-purpose constraint
and stores courier avatar reference/version. Test forged/claimed/purpose-mismatched keys,
non-image/oversize/decompression cases, replacement and authorized participant reads.
No public caching of API/signed access; asset versioning must preserve privacy rules.

**Bank profile only if approved:** GET/PUT/DELETE `/api/couriers/me/bank-profile`.
PUT `{"version":1,"beneficiary_name":"Example Courier","iban":"SA<22 digits>"}`;
return `{"beneficiary_name":"Example Courier","iban_last4":"1234","verification_status":"UNVERIFIED","version":2,"updated_at":"2026-10-07T09:00:00Z"}`.
Beneficiary max120, Saudi normalization/checksum, encryption at rest, optimistic version;
never return plaintext IBAN, log body or claim verified without verification integration.
Initial GET when absent returns 404; DELETE204. No automatic change to already-submitted
withdrawals. Confirm required beneficiary/legal fields and verification process first.
Owned bank table migration; tests redaction/encryption, foreign access, stale version.

### 9. Phone change

**New proposed:** POST `/api/users/me/phone-change/request` with
`{"new_phone":"+9665XXXXXXXX","reauthentication_token":"<short-lived-current-phone-proof>"}`;
response202 `{"challenge_id":"11111111-1111-4111-8111-111111111111","expires_in":60,"resend_after":60}`.
POST `/api/users/me/phone-change/confirm` with `{"challenge_id":"…","otp":"12345"}`;
response `{"phone":"+9665XXXXXXXX","reauthentication_required":true}` then login again.
New scoped reauth challenge operations POST `/api/users/me/reauthentication/request`
(no body) and `/confirm` (`{"challenge_id":"…","otp":"12345"}`) return respectively
challenge metadata and `{"reauthentication_token":"<opaque>","expires_in":300}`.
All tokens/challenges purpose- and actor-bound, single-use. These operations are proposals,
not existing login endpoints. Proof may also serve deletion approval under separate scope.

Recommend current-phone OTP proof plus new-phone verification, canonical Saudi formats
using existing normalizer, uniqueness checked under transaction/unique constraint,
all access/refresh/admin sessions revoked as applicable, device registrations removed.
No tokens silently reused after change. Generic safe failures avoid phone enumeration;
specific conflict only after valid owned proof. OTP HMAC in Redis with 60-second expiry,
same existing send/block policy plus dedicated per-account/new-number/IP abuse quotas
to approve; do not cache raw OTP or profile auth decisions. Tests races, challenge replay,
expiry, wrong purpose, rate-limit/provider outage and post-change token rejection.
Redis scoped proofs need no phone column migration; token-version revocation reuses
existing mechanism. Lost-current-phone recovery requires an approved support process.

### 10. Deletion / courier termination — business policy blocked

**Proposed after policy approval:** POST and GET `/api/users/me/account-request`.
POST `{"kind":"DELETE_ACCOUNT","confirmation":"DELETE","reauthentication_token":"<scoped-proof>","reason":null}`;
courier kind `TERMINATE_CONTRACT`, confirmation `TERMINATE`.
Response202/GET `{"id":"11111111-1111-4111-8111-111111111111","kind":"DELETE_ACCOUNT","status":"PENDING_REVIEW","submitted_at":"2026-10-07T09:00:00Z","updated_at":"2026-10-07T09:00:00Z"}`.
Reason nullable max500; status proposal `PENDING_REVIEW|APPROVED|REJECTED|COMPLETED`;
one active request per account, repeat submission reuses it. Missing own request404.
No implementation before deciding customer self-service versus review and courier
contract process, outstanding-order/dispute/payment/withdrawal/balance handling and
legal retention/anonymization periods. Do not invent forfeiture or promise immediate
erasure. Proposed guard rejects completion while unresolved obligations exist; provide
safe own-account blocker codes if approved. Lock account and relevant obligations when
completing; revoke sessions/push, scrub approved PII, retain required financial records.
New request table/audited transitions; no cache. Tests reauth/replay/foreign IDs,
concurrent obligations, idempotent requests and session revocation. Completion polling
after revocation requires an approved access policy, not a bypass of deleted-account auth.

### 11. Customer order search

**Extend** GET `/api/orders` with optional `q: string 1..100` (trimmed, nonblank).
Customer-only search; courier supplying q receives403. Combine existing delivery-date
bounds/status before pagination; keep existing OrderSummary, created_at DESC,id DESC,
default limit20, UUID cursor. Example `/api/orders?q=flowers&from_date=2026-10-01&to_date=2026-10-31`.
Response remains `{"items":[<existing OrderSummary>],"next_cursor":null}`.
Recommended search only own description plus exact full UUID; no new public order
number without approval. Literal matching escapes SQL LIKE wildcard characters.
Substring search needs measured pg_trgm GIN index cost/availability; otherwise approve
token/prefix semantics with appropriate indexed text search. Do not ship an unbounded
ILIKE history scan or application-memory filter. Need Arabic/English search semantics
approved; no contact/phone/private chat search. Tests literal `%/_`, Unicode, injection
strings, role restriction, ownership and unchanged pages combining filters. No cache initially.

### 12. Order timeline and order-media reads

**New:** GET `/api/orders/{order_id}/timeline` and `/media`, current authorized
participants only. Timeline created_at ASC,id ASC, limit25, scoped cursor.
Output `{"items":[{"id":"11111111-1111-4111-8111-111111111111","event_type":"STATUS_CHANGED","status":"ASSIGNED","created_at":"2026-10-07T09:00:00Z","actor":{"role":"COURIER","display_name":"Example Courier"}}],"next_cursor":null}`.
Actor nullable for system; no emails/identity fields/internal audit metadata.
Current WebSocket `/api/ws/orders/{order_id}` sends snapshots, not durable history.
Create dedicated events atomically with transitions including workers/admin corrections;
append deduplicated real changes only. Existing audit logs are not automatically a safe,
complete mobile timeline. Do not synthesize old transitions: historical data available
only where verified, and return `history_complete: false` for pre-event orders.

Media list optional purpose `ORDER_REQUEST|DELIVERY_PROOF`, created_at ASC,id ASC;
response `{"items":[{"id":"11111111-1111-4111-8111-111111111111","purpose":"ORDER_REQUEST","content_type":"image/jpeg","byte_size":12345,"created_at":"2026-10-07T09:00:00Z","access_url":"https://<signed-private-object>","expires_at":"2026-10-07T09:05:00Z"}],"next_cursor":null}`.
Reuse existing confirmed upload/order-media links and storage adapter; persist trusted
metadata if absent. Renew URLs by re-reading authorized list, never expose storage keys
as URLs. Chat attachment endpoint does not authorize unrelated order_media objects.
Events owned-order/time index; media existing order/type index inspected then augment
only if plans justify. Tests foreign order/media, removed participation, expiry, duplicate
transitions, rollback and bounded queries. No initial cache; private conditional event
reads possible after accounting for authorized admin edits; signed media no-store.

### 13. Top-up reliability — preserve pushed contract

Pushed POST `/api/wallets/topup`, body `{"amount":"100.00"}`, response201
`{"payment_intent_id":"11111111-1111-4111-8111-111111111111","amount":"100.00","payment_url":null}`.
Eligible customers/couriers; amount follows MIN_TOPUP_AMOUNT/MAX_TOPUP_AMOUNT,
defaults100.00/20000.00, not a client-defined amount or payment success flag.
Development currently settles directly; production blocks external payments. Test
provider responses do not prove real funded settlement.

**Proposal:** optional `Idempotency-Key` header length1..128 to preserve old callers;
mobile MUST send/persist a unique random key before its first submission. Same actor/key
and normalized amount replays the same intent and three-field response; different amount
409. Concurrent retry creates no second checkout/credit. Keep durable financial replay
mapping; define retention before deleting keys. Provider unknown result remains unresolved
and must not trigger a replacement checkout. Unique DB actor/key constraint and lock.

Reuse unreleased owned session operations from the inventory after review/approval.
Their existing workspace response is:

```json
{"payment_intent_id":"11111111-1111-4111-8111-111111111111","provider":"dhamen","status":"PENDING","checkout_state":"ACTIVE","order_id":null,"invoice_id":null,"currency":"SAR","amount_from_wallet":"0.00","amount_from_gateway":"100.00","expires_at":"2026-10-07T10:00:00Z","payment_url":"https://<checkout>","invoice":null,"purpose":"WALLET_TOPUP","title":"Top up","description":null,"use_wallet":false}
```

IDs/URLs/descriptions/invoice are nullable as shown; invoice otherwise uses existing
InvoiceResponse. Other scalar fields are strings except use_wallet boolean. Status maps
NEW to PENDING, otherwise PAID/FAILED/EXPIRED/CANCELLED; checkout_state/provider are
currently unconstrained strings and need allowlisted public definitions before release.
GETs have no body. Refresh/cancel POSTs have no body and may call the provider, with
bounded timeout/rate limits; they are not safe cached status reads.
For a minimal recovery addition, proposed GET `/api/wallets/me/topup-attempts/by-key/{key}`
returns `{"payment_intent_id":"…","amount":"100.00","currency":"SAR","status":"PENDING","payment_url":"https://<checkout>","expires_at":"2026-10-07T10:00:00Z"}`;
status `PENDING|PAID|FAILED|CANCELLED|EXPIRED`, URL nullable. This by-key lookup is needed
when the initial intent ID is lost; choose it OR equivalent header-based lookup on the
existing session operation, not both. Read/cancel/refresh is payer-only, foreign404.
Persist transaction reference_intent_id; wallet balance change alone cannot confirm a
specific top-up. Reuse workspace linked transaction metadata and tests. Refresh verifies
provider amount/currency/reference/status, not browser redirect. Expiry/cancel releases
only after authoritative unpaid closure; paid-during-cancel settles once. Current
production safeguard remains until provider authentication is independently verified.
No caching/automatic new-key retries. Tests two concurrent same-key requests, conflicting
amounts, lost response, provider timeout, signed duplicate/out-of-order callbacks,
foreign recovery, late payment and settlement correlation. Reuse pending workspace
migrations only after inspecting actual deployed migration state.

### Approval decisions and implementation sequence

1. Approve proceeds (includes item reimbursement) or commission-adjusted fee profit;
   correction/settlement timestamp semantics; Sunday weeks, Riyadh reporting and equal-day
   comparison; target limit/periods. Existing schema cannot honestly report profit yet.
2. Approve in-app inbox plus optional push events (order changes, earnings settled,
   withdrawal status, occasion reminder), retention and reminder09:00/retry cutoff.
   Approve whether annual recurrence is needed and its February29 policy.
3. Explicitly approve or defer independent date-only appointments, saved bank profiles,
   statement PDF, and a public order number. Avatar remains courier-only.
4. Approve both-number phone proof and forced new login; define lost-number support.
   Supply deletion/contract policy, retention and obligations handling; define access
   to financial history for suspended/terminated accounts.
5. Approve search fields/Arabic matching and minimal top-up idempotency/recovery contract;
   decide whether to release existing broader payment-session workspace work now.

Recommended order: (a) reconcile release baseline and approved decisions; (b) top-up
reliability; (c) invoice list, statement dates/totals, media reads and customer search;
(d) durable order timeline/inbox; (e) ledger earnings and target settings;
(f) reminders/approved optional calendar/profile features; (g) phone change;
(h) account lifecycle only after retention/business rules. Each step is separately
reviewable and preserves existing callers. No new endpoints are added before approval.

Migrations must be additive, reviewed and tested on disposable PostgreSQL. Backfills
must not invent historical events/settlement timestamps. Rollback disables new routes/
jobs first and keeps recorded ledger/history; destructive downgrades need a reviewed
data plan. Check EXPLAIN plans and index/write cost on representative disposable data;
local database plans, real provider behavior and deployment remain UNCONFIRMED.

After approved implementation: regenerate and validate mobile-openapi.json, update
docs/api.md (maintained integration reference; MOBILE-API-INTEGRATION.md was explicitly
excluded earlier), exclude admin/provider/dev/simulation routes from mobile features,
and provide UI handoff in chat only. Run focused authorization/concurrency/date/money
tests, full pytest, Ruff, format, mypy and repository hooks; report unavailable database
integration checks. This proposal inspected source, schemas, tests and pushed contracts;
the proposal stage did not execute the test suite or modify API schemas/routes/migrations.

### Read-batch delivery and account-policy update

2026-10-07: implemented GET /api/invoices, GET /api/wallets/me/statement and
GET /api/orders/{order_id}/media, plus optional financial date filters on wallet history.
No migration or response cache. The existing wallet/time, invoice/order and order-media
indexes support initial bounded reads; actual PostgreSQL plans remain unverified.
New reads require an active customer or active verified courier. Latest visible invoice
revision is current; customer drafts are excluded. Statement totals are live per HTTP
response with one SQL snapshot for totals/page, rather than a frozen multipage document.
Private URLs expire in five minutes; stored instants and output timestamps remain UTC.

The user specified a 14-day recoverable account-deletion window, a detailed email and
support-call coordination for returning funds/data. Account deletion/restore and that
email are not yet implemented. Confirmation of anonymization/financial-record retention
and deferral while obligations remain is pending; do not promise automated erasure,
automatic fund return or functioning recovery before the lifecycle APIs exist.

Read-batch verification: 740 tests passed, 204 skipped; both hook suites passed.
Four existing Starlette/httpx deprecation warnings; no dependency change for this batch.

## Courier read and delivery performance — 2026-10-08

Mobile GET/HEAD sessions are explicitly read-only. Within each transaction, repeated
user and courier-profile reads reuse the same row; mutations, admin sessions and
later transactions always perform their own checks. Read requests avoid database
audit-context writes. Courier order reads skip customer-only rating eligibility
queries. Live order snapshots use one participant-scoped SQL projection and retain
fresh token revocation, account status/version and courier verification checks.

Invoice creation and promo replacement insert their bounded item batch
with one flush instead of one flush per item. Migration0021 extends actor/revision
keyset indexes with deterministic ID ordering and adds a partial city/NEW-order
radar index. Five existing indexes are replaced and one added: this costs index
storage/write maintenance and can block writes during transactional builds. Use a
maintenance window on larger databases; downgrade restores the prior indexes.
Representative PostgreSQL plans and load benefits remain unverified locally.

Chat messages commit a durable chat_notifications push intent in the same transaction.
Migration0022 creates this table and its audit trigger; downgrade drops pending push
intents, not messages. Run the existing Taskiq worker AND scheduler. The scheduler
checks once per minute; each sweep handles at most20device pages of500 tokens,
uses a60-second fenced lease, a20-second page timeout and at most8attempts per
page. Exhausted rows consume the sweep budget without blocking healthy rows.
Providers are called outside database transactions. Delivery is at least once:
a crash after provider acceptance can repeat a push. Failed intents remain visible
for operational investigation. Chat Redis publication still has its five-second
bound; push-provider waits no longer hold the HTTP/WebSocket acknowledgement.

CloudFront signing runs off the event loop and reuses its signer; chat encryption
reuses one cipher/key-version snapshot per service. Media URLs retain ownership
checks and their existing expiry. PDF downloads authorize and reread content first,
then may reuse identical output for one hour in private Redis. Changes to displayed
invoice/items or template source invalidate reuse by content fingerprint. The cache
caps128entries,256KiB each and four render threads per process; cancellation does
not release capacity until rendering finishes. Redis reads/writes each have a100ms
budget and fail softly. HTTP PDF responses remain private,no-store.

Orders, available balances, claim eligibility, ledger totals and live authorization
are not cached as authority. Statement totals still use one SQL aggregate each page;
rating aggregates retain their existing indexed SQL. No measured evidence justified
additional shared response caches yet. All collection processing is bounded or batched;
these changes introduce no per-row database queries or quadratic collection scans.

Requests taking at least500 ms emit operational timing diagnostics: route pattern,
method, request ID, elapsed time and successful SQL count/time. SQL text, parameters
and user identifiers are not logged. Pool checkout waits, failed SQL and response
streaming are outside the SQL measurement. These diagnostics do not create database
HTTP audit entries. Compare endpoint p50/p95, SQL/pool time and worker lag after
deployment; same-region hosting alone does not eliminate repeated round trips.


## Hosted payment operations — 2026-10-08

Migrations0019/0020 add checkout claims/snapshots, pending ledger coordination and
indexes;0023 merges them with already published0021/0022. Applied history is unchanged.
The one-time deployment migrator upgrades the single0023 head before API workers start.

Upgrade backfills order references and refuses duplicate open order attempts. Partial
unique indexes enforce one NEW attempt per order and one non-simulated NEW top-up per
payer. An intent transaction index supports bounded settlement. These add write/storage
cost for concurrency integrity. Downgrade refuses unresolved attempts and pending ledger
groups. Disable new checkouts, reconcile financial attempts, back up and verify rollback
on disposable PostgreSQL before a coordinated code/schema downgrade.

Durable creation/cancellation checkpoints release locks before provider HTTP. Ambiguous
timeouts keep claims, holds and uniqueness. Verified settlement checks reference, payer,
currency and exact amount; pending balanced rows settle once. Callback batches prelock
all invoices/orders/intents, then ledger rows and the complete wallet union in globally
deterministic order. Money changes/receipts are atomic. A verified late payment after
closure persists a REVIEW quarantine before any batch settlement; it blocks replacement
and requires financial review. Callback bodies and browser redirects cannot prove payment.

Taskiq testing-provider reconciliation runs every five minutes, with20 attempts maximum,
a shared scheduler lock and five-minute deadline. Run worker and scheduler for recovery.
Production still rejects Dhamen until vendor authentication/merchant contracts are verified.

HTTP download and paid-email attachment use the same English Giftly purple renderer.
GMT+3 affects display only. One-hour bounded Redis reuse fingerprints status, dates,
priced items/totals and template after current ownership/content reads. Changes regenerate
immediately. Static PDFs escape markup and fetch no remote assets.

## Redis subscription isolation and chat attachment batches — 2026-10-08

Each API worker owns separate Redis pools: 100 short-operation HTTP/security
connections and 80 long-lived WebSocket subscription connections. Three workers
have a maximum 540 API Redis connections, plus separately budgeted workers/schedulers.
Connections are created on demand. Budget Redis maxclients and deployment replicas
accordingly before increasing socket capacity. Pool exhaustion closes a new socket
with 1013; clients should reconnect with bounded exponential backoff and jitter.
Per-account 8 and deployment-wide 10,000 leases still enforce shared abuse limits;
these ceilings do not promise 10,000 simultaneously available subscription slots.
Publishers, revocation checks, OTP, throttles and locks retain the normal pool.
Both owned pools close at shutdown. Connection/handshake deadlines remain 3 seconds;
subscription setup is bounded 5 seconds and cleanup 3 seconds per operation.

Chat media preparation reads 1..5 grants together. Sending uses three attachment
persistence queries: sorted fresh row locks, conditional confirmation/claim with
complete returned-key checking, and one sender/participant-scoped insert. Message
and eligibility queries are unchanged. Any partial batch raises before commit and
the enclosing request transaction rolls back. Sequential size/MIME/decoder checks
and global decoding slots remain; larger media memory optimizations are outstanding.
No schema migration or mobile contract change is required. Rollback uses the prior
application image; no data conversion is needed. Real Redis/PostgreSQL load and
query-plan measurements remain pending; local tests cannot establish production speed.

## Measured read optimizations — 2026-10-08

Recordings stream in64KiB chunks into private temporary files before full decoding. Chat monitoring uses one fresh joined authorization query; cursor anchors use scalar projections. Identical invoice PDF misses share bounded per-worker rendering. Candidate migration0025 is retained locally but not released; prepared/generic query plans and write costs need further validation. Exact ratings and statement totals remain uncached. See codebase_review.md for measurements, tradeoffs and verification limits.

### Usage-limit checkpoint — 2026-10-08

At91% five-hour usage, the approved checkpoint publishes AP-P03/AP-P04/AP-P05/AP-P10 only. AP-P06/AP-P07 retain exact queries after measurement. AP-P08/AP-P09 candidate indexes are NOT published: generic prepared plans may lose partial-index and expression-order benefits; approved-only write costs need validation. Candidate source and raw plans are preserved in the attached worktree's ignored workflow folder. No new migration is required for this checkpoint. Full unit suite passed; broad service suites remain incomplete (PostgreSQL run interrupted on existing date/Redis failures, non-service integration checks also too slow for the checkpoint). Deployment and full PostgreSQL/Redis/native decoder checks remain unconfirmed.

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
through pricing and settlement, not trusted client totals. VAT applies only to discounted
item prices, not courier or service fees. The current review identifies rounding and
reservation-concurrency defects that need follow-up; these invariants are not a claim
that every edge case has been proven.

User creation receives a wallet through the database-backed creation path, including
administrative creation. System wallets represent escrow obligations, platform revenue,
gateway clearing and VAT payable; they are internal accounting buckets, not user roles.

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
| Business rules | VAT, service fee, commission, invoice/item, topup, withdrawal and expiry controls. |
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

### Invoice PDFs, paid receipts and VAT repair

Order participants can download `GET /api/invoices/{invoice_id}/pdf`; authenticated
admins have an invoice-detail download. PDFs use stored values, English labels and no
remote assets. Receipt sends attach the PDF using sndr.sh with stable invoice idempotency
keys and durable claims. A verified sender/domain and real vendor acceptance are required.
Neither provider idempotency retention nor inbox delivery is proven by a fake.

For a reviewed unpaid-invoice correction, run `uv run --locked python -m
app.repair_invoice_vat` for a dry-run page (default 200). Review IDs/totals and a backup
before running the same page with `--apply`; continue using `--after <UUID>` when reported.
Only eligible active/unexpired unpaid invoices are revised; original revisions are
retained and paid records/ledger entries are not rewritten. Each invoice commits
separately. Production repair was not executed in this task. Roll back through reviewed
forward revisions from retained originals, not by overwriting paid history.

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

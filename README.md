# Giftly Backend

Giftly is a two-sided mobile marketplace for **custom** gifting: a customer posts a
gift request tied to a city and a delivery date, a verified courier claims it and
builds an itemised invoice, the customer pays into platform escrow, and funds release
to the courier only after photo-proven delivery is approved. This repository
is the backend, admin dashboard, and documentation — there is no mobile/web client here.

> Payment status: **Dhamen is the selected provider; production payments are disabled**
> until its integration and callback authentication are implemented and verified.
> Development/test simulations remain available. CI checks lint, types, tests,
> coverage, security, generated OpenAPI, and Docker packaging.

## Architecture

```
                 +----------------------------------------------+
   Mobile app -->|  routers/ (HTTP + WS)      admin/ (Jinja)     |
                 |        |                          |            |
                 |        v                          v            |
                 |             services/ (all business logic)     |
                 |        |                          |            |
                 |        v                          v            |
                 |   repositories/ (all DB access)   integrations/|--> email / SMS /
                 |        |                                        |    Push / S3
                 |        v                                        |
                 |   models/ (SQLAlchemy)  <--  core/ (config,     |
                 |                              money, pricing,    |
                 |                              crypto, security)  |
                 +----------------------------------------------+
   PostgreSQL 16  .  Redis 7  .  private S3 + CloudFront
```

Dependency rule (strict): `routers/admin -> services -> repositories -> models`, never
the reverse. A module may import another module's **service interface** only.

## Tech stack

Python 3.13 in Docker (project minimum: 3.11), FastAPI + Uvicorn/Gunicorn, Pydantic v2,
SQLAlchemy 2 async + asyncpg, Alembic, Redis, TaskIQ, PostgreSQL 16,
`cryptography`, PyJWT, Jinja2 (admin), ruff, mypy, pytest. Package management is **`uv`
only** — `pip`, `poetry`, `pipenv`, `virtualenv`, and `conda` are forbidden everywhere.

## Prerequisites

Chat supports private voice notes, images and videos. Deployment images include FFmpeg/
ffprobe for recording validation; rebuild the image and migrate through `0015_chat_media`
before enabling these features. The native decoder must also be available for a non-Docker
deployment. No decoder means recording validation fails closed. Limits are configured
with `CHAT_AUDIO_MAX_DURATION_SECONDS`, `CHAT_VIDEO_MAX_DURATION_SECONDS` (120 seconds),
`CHAT_IMAGE_MAX_UPLOAD_BYTES`, `CHAT_AUDIO_MAX_UPLOAD_BYTES` (10 MiB), and
`CHAT_VIDEO_MAX_UPLOAD_BYTES` (120 MiB). Clients fetch `/api/chat/media-limits`.
See [the chat media handoff](docs/UI-AGENT-CHAT-MEDIA-PROMPT.md) for private storage policy,
upload headers, request/response schemas and development-fake limitations.

- [uv](https://docs.astral.sh/uv/) (Astral)
- Docker + Docker Compose (for the local Postgres/Redis stack)

## Quickstart

```bash
uv sync                                   # install from the committed uv.lock
cp .env.example .env                      # then fill in the blanks (see the table below)
docker compose up -d db redis             # data services only; run the API below
uv run --locked python -m app.bootstrap_db # create fresh schema and seed wallets/cities
uv run --locked uvicorn app.main:create_app --factory --reload --port 3000
```

Schema initialization seeds 20 active Saudi city choices and system wallets;
`app.seed_cities` remains an idempotent repair command. `GET /api/cities` lists active choices
without authentication. Orders and courier profiles store UUID foreign keys to `cities.id`.
New clients can submit the selected `id` as `delivery_city_id`, `city_id`, or
`courier_city_id`; the former city-name request fields remain accepted for existing clients.
Responses include the city ID, English `name`, and Arabic `name_ar`. Run
`uv run --locked python -m app.seed_cities` to fill missing default cities and refresh their
Arabic names on an existing database. API services reject inactive cities. The
`shortcut` values are application labels, not official municipality codes. The
initial city selection follows the [Saudi National Debt Management Center map](https://ndmc.gov.sa/IssuancePrograms/Documents/KSA%20Ijarah%20Sukuk%20Establishment%202025.pdf).

Health check: `curl localhost:3000/api/health`. In development the OpenAPI docs are at
`/docs`; they are disabled in test and production by design.

### Scheduled maintenance

Compose starts one `scheduler` service alongside the `worker`. The scheduler reads
the cron labels registered by `app.workers.broker` and enqueues expiry,
auto-approval, receipts, order notifications, abandoned-upload cleanup, ledger
reconciliation, and refresh-token cleanup. Keep one scheduler instance per deployment
to avoid duplicate enqueues. Jobs use their own database claims or Redis locks to
coordinate concurrent workers. To run both processes outside Compose after
starting PostgreSQL and Redis, use two terminals:

```bash
uv run --locked taskiq worker app.workers.broker:broker
uv run --locked taskiq scheduler app.workers.scheduler:scheduler
```

The same commands work in PowerShell. Schedule changes require restarting the
scheduler and workers. Check their logs for startup errors and job failures.

## Environment variables

HTTP throttling defaults to 60 requests per 60 seconds per verified bearer-token identity,
or 30 requests per 3,600 seconds per unauthenticated client IP. Configure these with
`RATE_LIMIT_MAX_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`,
`RATE_LIMIT_ANONYMOUS_MAX_REQUESTS`, and `RATE_LIMIT_ANONYMOUS_WINDOW_SECONDS`.
Admin dashboard and `/api/admin/` routes instead use a separate 100-request/60-second
bucket, configured with `RATE_LIMIT_ADMIN_MAX_REQUESTS` and
`RATE_LIMIT_ADMIN_WINDOW_SECONDS`. Cookie-only dashboard requests are counted per IP;
bearer-authenticated admin API requests are counted per token user identity.
Update existing deployment overrides to use the new values. Health probes and CORS
preflight are exempt. The dashboard's separate login throttle remains in place.
Redis failures currently allow
ordinary HTTP requests through, while authentication retains its own security checks.

Every secret is an environment variable — there is no secrets manager. Secrets are
wrapped in `SecretStr`, never logged, and never baked into the image; they are injected
at runtime (task definition / compose override / systemd `EnvironmentFile`). See
`.env.example` for grouped settings with descriptions and safe example values.
Blank secret fields must be supplied before starting the application.

| Name | Required in | Description | Example (never a real value) |
| --- | --- | --- | --- |
| `ENVIRONMENT` | all | `development` \| `test` \| `production`; no default. Development returns a five-digit numeric `otp_dev` and allows browser requests from any origin. | `development` |
| `DATABASE_URL` | all | async Postgres DSN | `postgresql+asyncpg://user:pass@host/db` |
| `REDIS_URL` | all | Redis DSN | `redis://:pass@host:6379/0` |
| `JWT_SECRET` | all (HS256) | >= 32-byte signing secret | `<32+ random bytes>` |
| `JWT_ALGORITHM` | all | `HS256` or `RS256` (pinned, never from token) | `HS256` |
| `FIELD_ENCRYPTION_KEYS` | all | JSON version->base64 32-byte key map | `{"1":"<base64 32B>"}` |
| `FIELD_ENCRYPTION_KEY_VERSION` | all | active key version in the map | `1` |
| `IDENTITY_FINGERPRINT_PEPPER` | all | >= 32 bytes, distinct from every enc key | `<32+ random bytes>` |
| `CORS_ALLOWED_ORIGINS` | production | exact origins; wildcard banned. Ignored in development, where CORS allows any origin without credentials. | `https://app.example.com` |
| `SNDR_*` | production | email api key/base url/from/template | — |
| `AWS_*`, `S3_BUCKET_NAME`, `CLOUDFRONT_*` | production | storage + signed CDN | — |
| `ADMIN_SESSION_SECRET` | if dashboard on | >= 32 bytes | `<32+ random bytes>` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | if dashboard on | environment-backed login; use a strong, unique production password | `admin` / `admin` (development only) |
| `ADMIN_TOTP_SECRET` | production, if dashboard on | Base32 secret of at least 20 random bytes for an authenticator app; keep it private | `<Base32 secret>` |

The application **refuses to boot** if any production-safety rule is violated (DEBUG on,
docs enabled, empty/wildcard CORS, missing required storage/messaging config, a bad encryption key, or
a pepper equal to an encryption key). This is the first of four layers of the production
safety interlock (SPEC SECTION 5.2).

### How secrets reach the container in deployment

Never via the image. Inject at runtime with your orchestrator's mechanism — an ECS task
definition's `secrets`, a Kubernetes `Secret` mounted as env, a systemd
`EnvironmentFile`, or an uncommitted compose `override`. `docker history` must reveal
nothing.

### Running behind a proxy or load balancer (required for correct client IPs)

The app reads the peer address (`request.client.host`) for the per-IP rate-limit bucket
for unauthenticated requests and admin audit logging. Behind a reverse proxy or load
balancer this peer is the **proxy**, not the real client, unless you make the ASGI
server trust the forwarded header from that proxy:

```bash
# Trust X-Forwarded-For ONLY from your known proxy CIDRs — never "*".
gunicorn 'app.main:create_app()' \
  --worker-class uvicorn.workers.UvicornWorker \
  --forwarded-allow-ips="10.0.0.0/8"
```

Without this, all unauthenticated traffic collapses into a single rate-limit bucket. Do
not parse `X-Forwarded-For` in application code — trusting a
client-supplied header is a spoofing footgun; let the server strip it against a trusted
proxy list.

## Running tests, lint, and types

```bash
uv run --locked pytest                 # full suite; requires test services
uv run --locked pytest tests/unit -q   # unit tests
uv run --locked ruff check .           # lint, imports, security rules
uv run --locked ruff format --check .  # verify formatting
uv run --locked mypy app               # strict types
uv run --locked ruff check --fix .     # apply intended lint/import fixes
uv run --locked ruff format .          # apply formatting
```

### Git hooks (Windows, Linux, and macOS)

Run these commands from the backend root after each clone, with `uv` on PATH:

```bash
uv sync --locked --dev
uv run --locked pre-commit install
uv run --locked pre-commit run --all-files
uv run --locked pre-commit run --all-files --hook-stage pre-push
```

Installation enables both **pre-commit** and **pre-push** hooks. Pre-commit checks
staged Python lint/formatting, YAML/TOML/JSON syntax, merge conflicts, and private
keys. It does not rewrite files: apply the fix commands above, review, and stage
the result. Pre-push checks lint, formatting, and strict types across the project.
All hook tools use the versions in `uv.lock`, matching CI. See
[the pre-commit documentation](https://pre-commit.com/) for hook operation.

The full test suite remains a separate CI gate because it needs disposable
PostgreSQL and Redis services. Hooks do not replace CI or branch protection.
Follow [AGENTS.md](AGENTS.md) for backend development rules. Push to `master` when
authorized. On Windows, use `Copy-Item .env.example .env` in place of `cp` in setup.

## Migrations

The PostgreSQL database must be provisioned by the hosting platform before
deployment; Alembic creates and upgrades tables inside that database, but does
not create the database itself. The container entrypoint runs
`alembic upgrade head` before starting the configured API, worker, or scheduler
command. Docker Compose and CI run the same migration command through
`uv run --locked python -m app.bootstrap_db`. FastAPI startup does not create
schema objects.

```bash
uv run alembic revision --autogenerate -m "describe change"   # DRAFT — read every line
uv run alembic upgrade head                                   # apply
uv run alembic downgrade -1                                   # roll back one revision
```

Every revision has a descriptive slug, a docstring, and a working `downgrade()`. An
autogenerated migration is a draft: read it before committing (SPEC SECTION 4.8).

## Project layout

```
app/
  core/          config, security, crypto, money, pricing, logging, exceptions, db
  models/        SQLAlchemy ORM + enums + base mixins
  integrations/  payments/ email/ sms/ push/ storage/ — interfaces and test fakes
  routers/       HTTP + WS (health, dev)              services/ repositories/ (per phase)
  admin/         server-rendered dashboard            workers/  background tasks
  migrations/    Alembic
tests/           mirrors the source tree
```

## Payments and Dhamen

Dhamen is the selected payment provider. Its database scaffolding and reserved
`.env.example` entries are preparation only: there is no live Dhamen client or
verified Dhamen callback endpoint yet. Do not enter live credentials to enable it;
there is no enable flag.

In production, wallet top-ups, invoice payments (including wallet-only payments),
and direct webhook service calls fail with HTTP `503`, code `PAYMENTS_DISABLED`,
before creating intents, changing balances, or contacting a gateway. The simulation
webhook and development routes are not registered in production.

With `ENVIRONMENT=development`, wallet top-ups and invoice remainders settle locally
through the normal ledger/escrow flows and return `payment_url: null`. Tests can use
`POST /api/webhooks/simulation` with the local fake signature. Development also has
`POST /api/dev/simulation/simulate` for pending simulated checkouts. The payload and
signature are a test harness, **not a Dhamen protocol**.

The initial Alembic revision creates the schema and seeds the default cities and
system wallets on an empty database. Later deployments apply only pending revisions.
Migration testing must use a disposable database. Production payments require a
separately reviewed Dhamen implementation before activation.

## Admin dashboard

Server-rendered (Jinja2), mounted at `/v1/admin/admin`, gated by `ADMIN_DASHBOARD_ENABLED`. It
authenticates with environment-backed username/password into server-side sessions and
calls backend services — it never queries the DB directly.

In production, login also requires a six-digit authenticator code from the Base32
`ADMIN_TOTP_SECRET` configured at deployment. Add that same secret to your authenticator
app as a 30-second SHA-1 TOTP entry. Codes can be used only once, including across
application workers. Development and test logins do not require this code.

`/v1/admin/admin/tables` provides paginated views and add/edit/delete forms for all mapped application
tables in every environment, including production. Every write requires an active admin
session and CSRF verification. Each successful operation
records the actor, table, record, and changed field names without logging field values.
Database triggers also record committed changes to application tables, including direct
SQL writes, in the same transaction. The log contains actor category, record ID, and
operation, never row values. Explicit service audit events emit metadata-only
`giftly.audit` application log entries; trigger-generated rows are not forwarded by
that mechanism. Database administrators can still alter local audit history, so an
independent retention destination remains necessary.
Deletion requires a confirmation checkbox and follows database cascade rules.

The dashboard opens in Arabic with a right-to-left layout. Language and light/dark
appearance controls are available on the login page and in the dashboard header.
Theme changes apply immediately without requests or page reloads. The browser saves
the choice in local storage, restores it before rendering, and synchronizes open tabs.
All table lists and the existing collection pages share date/ID sorting (ascending or
descending), Riyadh date/time range filters, and an exact-match filter selected from that
table's visible scalar fields. Boolean filters accept `true` or `false`; status/role
filters use the stored enum value. Masked secrets and unsupported field types cannot
be filtered. Lists default to the latest 25 rows, offer 50 or 100, and preserve filters
through Next/Previous cursor pagination. Record details and CRUD actions remain available.
Dashboard HTML is served with `Cache-Control: no-store`. Its stylesheet URL includes
the SHA-256 content version in the path so browsers and proxies fetch updated layout
and theme rules after a deployment, even when they ignore query-string versions.

Activity uses one dashboard page with System, Admin, and User tabs; System opens first.
Only the selected tab is queried. User activity combines customer and courier changes.
Activity actors show their current full name, optional email, role, and profile link;
Raw actor/object UUIDs are replaced with current readable names, emails, or safe record
descriptions; deleted/unavailable objects are labeled accordingly. Object IDs remain
in stored audit records and links. Names/emails are batched per target table rather
than queried per row, and are not copied into audit metadata.
The dashboard states that storage uses UTC while display, pickers, and date/time
filters use Riyadh time (UTC+3), independently of the browser timezone. Date-only
values are unchanged. Public API timestamp serialization remains ISO-8601 with an
explicit offset, normally UTC (`+00:00`); clients should convert instants for display.
Filters include Riyadh date/time range, activity ID, activity name (action-name substring),
actor ID, exact action, and entity type. Sort by newest or oldest, select 25 (default),
50, or 100 rows per page, and use Next for stable cursor pagination.
Activity name, action, and entity filters use dropdowns populated from the selected
tab's stored log values (up to 200 distinct choices per field). Riyadh date/time
bounds are explicitly normalized to UTC before database comparisons.
Reads, HTTP requests, WebSocket lifecycle events,
login/logout, and scheduled-job start/completion are not database audit actions.
Migration `0009_action_only_audit` removes existing `HTTP_*` audit rows; restore a
database backup if those historical rows are needed. Other old operational rows are
hidden from the action pages. An audit write failure rolls back its data change. Plan
retention and measure audit-table growth before production use.

Foreign-key inputs search related records in pages of 25 instead of asking for IDs.
One-to-one choices exclude already-used records and preserve the current edit selection.
Secrets and encrypted values remain masked; replacement encrypted text is encrypted by
the service. Blank edit inputs preserve stored values; **Clear** on edit explicitly sets an
optional field to NULL. Generated identifiers and creation, update, and deletion timestamps
are shown read-only on edit with both date and time. Concurrent
edits return a conflict and show the latest record instead of overwriting it.
Editable timestamp fields, including promo start and end, use a date-and-time picker in
the admin's local timezone; date-only fields use a calendar picker.

Run `uv run --locked python -m app.bootstrap_db` before using the table editors on a fresh
database. Bootstrap installs a transaction-local maintenance guard tied to an active admin
session and immutable-row triggers for ledger entries, invoice items, and messages. The guard
is enabled only around audited maintenance operations; foreign keys, uniqueness, and CHECK
constraints remain enforced. Invalid writes roll back with a safe error message.

These are direct administrative data corrections: editing financial or lifecycle fields
does not run payment settlement, create balancing ledger entries, or notify customers.
Admins must keep related balances and business state consistent. Production payment
processing remains disabled. Take a database backup before administrative data corrections;
reversing a correction requires a reviewed data-restoration plan.

Courier identity edits derive their duplicate-detection fingerprint from the final
national ID, falling back to the passport. An explicitly entered fingerprint remains an
administrator maintenance override; use it only for deliberate identity-index corrections.

`POST /api/auth/logout` signs out **all devices**: access tokens, refresh-token families,
and dashboard sessions for that account are revoked together. Clients should clear both
local tokens after the 204 response. Other devices must authenticate again; open chat
sockets reject revoked credentials on their next authorization check (at most the normal
five-second polling interval plus the dependency deadline).

## API documentation

Development serves the interactive schema at `/docs`. CI exports the current schema
as an `openapi` artifact. Generate a local copy with
`uv run --locked python -m app.export_openapi` (writes `docs/openapi.json`).
The previous static documentation archive has been removed; refer to the current
schema and source for the implemented contract.

For order request photos and delivery proof, the actor who requested the upload URL
must confirm the uploaded key before attaching it. Each confirmed key can be attached
once, for its requested purpose; rejected order or delivery transactions leave the key
available for retry.
Each account may have at most 20 unattached uploads and 50 MiB of reserved upload bytes.
Unattached grants and their objects are eligible for hourly cleanup after one day;
attached order photos and delivery proof are retained. Cleanup handles at most 100
grants per run and retries storage failures on a later run.
The PUT to the returned S3 URL must include the issued `Content-Type` and
`Content-Length` plus `If-None-Match: *`. The signed condition makes the upload
create-only; another PUT to the same key fails with HTTP 412. Browser clients also
need `If-None-Match` allowed by the S3 bucket's CORS configuration.

The [2026-09-17 backend review](docs/2026-09-17-backend-review.md) records open
security, reliability, performance, and maintainability findings, proposed fixes,
and verification limits. It is not a declaration of production readiness.

## Troubleshooting

Only the delivery location URL is removed. City, delivery date, description, delivery
photos, and admin address notes remain supported. Stop submitting the removed URL field.
Migration `0010_remove_delivery_location` initially also dropped address notes;
`0011_restore_address_note` corrects their schema. Deployment applies both before startup.
Existing note values dropped by revision 0010 require a pre-migration backup to recover;
back up notes before upgrading a database that has not run that revision. No database
was modified locally. Applied migration history is retained for safe upgrades.

- **App refuses to boot naming a variable** — that is the interlock working; fix that
  variable in `.env`.
- **TLS/proxy errors** — see the environment's proxy notes; never disable TLS
  verification.

### Invoice PDFs, VAT and paid receipts

VAT is calculated only on discounted invoice items. Participant downloads use GET /api/invoices/{invoice_id}/pdf; authenticated dashboard admins can download from invoice details. Paid receipts include an English PDF via sndr.sh, using a verified sending domain and SNDR_BASE_URL=https://api.sndr.sh. The receipt worker runs every five minutes. The overdue unaccepted-order task runs at Saudi midnight (UTC+3). Deploy migration 0014 before starting workers. For reviewed dry-run VAT repair of unpaid invoices, see [repair procedure](docs/2026-10-03-invoice-vat-delivery.md).

# SAFE-GIFT Backend

SAFE-GIFT is a two-sided mobile marketplace for **custom** gifting: a customer posts a
gift request tied to a city and a delivery date, a verified courier claims it and
builds an itemised invoice, the customer pays into platform escrow, and funds release
to the courier only after geofenced, photo-proven delivery is approved. This repository
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
   PostgreSQL 16 + PostGIS + pgcrypto  .  Redis 7  .  private S3 + CloudFront
```

Dependency rule (strict): `routers/admin -> services -> repositories -> models`, never
the reverse. A module may import another module's **service interface** only.

## Tech stack

Python 3.13 in Docker (project minimum: 3.11), FastAPI + Uvicorn/Gunicorn, Pydantic v2,
SQLAlchemy 2 async + asyncpg, Alembic, Redis, TaskIQ, PostgreSQL 16 + PostGIS,
`cryptography`, PyJWT, Jinja2 (admin), ruff, mypy, pytest. Package management is **`uv`
only** — `pip`, `poetry`, `pipenv`, `virtualenv`, and `conda` are forbidden everywhere.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Astral)
- Docker + Docker Compose (for the local Postgres/Redis stack)

## Quickstart

```bash
uv sync                                   # install from the committed uv.lock
cp .env.example .env                      # then fill in the blanks (see the table below)
docker compose up -d db redis             # data services only; run the API below
uv run alembic upgrade head               # apply the schema (creates system wallets)
uv run python -m app.seed                 # idempotent safety-net seed
uv run uvicorn app.main:create_app --factory --reload   # http://localhost:8000
```

Health check: `curl localhost:8000/api/health`. In development the OpenAPI docs are at
`/docs`; they are disabled in test and production by design.

### Scheduled maintenance

Compose starts one `scheduler` service alongside the `worker`. The scheduler reads
the cron labels registered by `app.workers.broker` and enqueues expiry,
auto-approval, receipt, ledger reconciliation, and refresh-token cleanup jobs. Keep
one scheduler instance per deployment to avoid duplicate enqueues; the jobs retain
their Redis locks and idempotency checks. To run both processes outside Compose after
starting PostgreSQL and Redis, use two terminals:

```bash
uv run --locked taskiq worker app.workers.broker:broker
uv run --locked taskiq scheduler app.workers.scheduler:scheduler
```

The same commands work in PowerShell. Schedule changes require restarting the
scheduler and workers. Check their logs for startup errors and job failures.

## Environment variables

Every secret is an environment variable — there is no secrets manager. Secrets are
wrapped in `SecretStr`, never logged, and never baked into the image; they are injected
at runtime (task definition / compose override / systemd `EnvironmentFile`). See
`.env.example` for the full, always-blank template.

| Name | Required in | Description | Example (never a real value) |
| --- | --- | --- | --- |
| `ENVIRONMENT` | all | `development` \| `test` \| `production`; no default | `development` |
| `DATABASE_URL` | all | async Postgres DSN | `postgresql+asyncpg://user:pass@host/db` |
| `REDIS_URL` | all | Redis DSN | `redis://:pass@host:6379/0` |
| `JWT_SECRET` | all (HS256) | >= 32-byte signing secret | `<32+ random bytes>` |
| `JWT_ALGORITHM` | all | `HS256` or `RS256` (pinned, never from token) | `HS256` |
| `FIELD_ENCRYPTION_KEYS` | all | JSON version->base64 32-byte key map | `{"1":"<base64 32B>"}` |
| `FIELD_ENCRYPTION_KEY_VERSION` | all | active key version in the map | `1` |
| `IDENTITY_FINGERPRINT_PEPPER` | all | >= 32 bytes, distinct from every enc key | `<32+ random bytes>` |
| `CORS_ALLOWED_ORIGINS` | production | exact origins; wildcard banned | `https://app.example.com` |
| `DHAMEN_*` | reserved only | Future vendor configuration; not loaded or used by an active client | — |
| `SNDR_*` | production | email api key/base url/from/template | — |
| `AWS_*`, `S3_BUCKET_NAME`, `CLOUDFRONT_*` | production | storage + signed CDN | — |
| `ADMIN_SESSION_SECRET` | if dashboard on | >= 32 bytes | `<32+ random bytes>` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | if dashboard on | environment-backed login; production password >= 12 chars | `admin` / `admin` (development only) |

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
PostgreSQL/PostGIS and Redis services. Hooks do not replace CI or branch protection.
Follow [AGENTS.md](AGENTS.md) for backend development rules. Push to `master` when
authorized. On Windows, use `Copy-Item .env.example .env` in place of `cp` in setup.

## Migrations

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

The migration history was cleaned for fresh databases with the owner's confirmation
that no existing database needs the previous history. Create a fresh database and run
`uv run --locked alembic upgrade head`; do not apply this rewritten history to an
older deployment. Downgrade testing must use a disposable database. Production
payments require a separately reviewed Dhamen implementation before activation.

## Admin dashboard

Server-rendered (Jinja2), mounted at `/admin`, gated by `ADMIN_DASHBOARD_ENABLED`. It
authenticates with environment-backed username/password into server-side sessions and
calls backend services — it never queries the DB directly.

`/admin/tables` provides paginated views and add/edit/delete forms for all 29 application
tables in every environment, including production. Every write requires an active admin
session, CSRF verification, and recent password confirmation. Each successful operation
records the actor, table, record, and changed field names without logging field values.
Deletion requires a confirmation checkbox and follows database cascade rules.

Foreign-key inputs search related records in pages of 25 instead of asking for IDs.
One-to-one choices exclude already-used records and preserve the current edit selection.
Secrets and encrypted values remain masked; replacement encrypted text is encrypted by
the service. Blank edit inputs preserve stored values; **Clear** explicitly sets an
optional field to NULL. Generated identifiers and timestamps are not editable. Concurrent
edits return a conflict and show the latest record instead of overwriting it.

Apply `uv run --locked alembic upgrade head` before using the table editors. Migration
`d4e5f6a7b8c9` adds a transaction-local maintenance override tied to an active admin session
for ledger, invoice-item, and message immutability triggers. It is enabled only around the
audited maintenance operation and cleared afterward; foreign keys, uniqueness, and CHECK
constraints remain enforced. Invalid writes roll back with a safe error message.

These are direct administrative data corrections: editing financial or lifecycle fields
does not run payment settlement, create balancing ledger entries, or notify customers.
Admins must keep related balances and business state consistent. Production payment
processing remains disabled. For an editor-only rollback, deploy compatible code that
removes the editor, or use a reviewed forward migration restoring normal immutable-row
triggers while preserving later schema. Do not downgrade to `c9d0e1f2a3b4` as an editor-only
rollback: the linear chain also removes credential versions, media upload grants, and
payment reservation ownership. A full-chain rollback requires a reviewed deployment and
data-restoration plan with backups; it does not undo administrative data corrections.

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
The PUT to the returned S3 URL must include the issued `Content-Type` and
`Content-Length` plus `If-None-Match: *`. The signed condition makes the upload
create-only; another PUT to the same key fails with HTTP 412. Browser clients also
need `If-None-Match` allowed by the S3 bucket's CORS configuration.

The [2026-09-17 backend review](docs/2026-09-17-backend-review.md) records open
security, reliability, performance, and maintainability findings, proposed fixes,
and verification limits. It is not a declaration of production readiness.

## Troubleshooting

- **App refuses to boot naming a variable** — that is the interlock working; fix that
  variable in `.env`.
- **`alembic upgrade` fails on `type "geometry" does not exist`** — use a
  `postgis/postgis` Postgres image; the baseline migration creates the extensions.
- **TLS/proxy errors** — see the environment's proxy notes; never disable TLS
  verification.

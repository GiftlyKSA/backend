# Giftly backend

Python/FastAPI backend, server-rendered administration and background workers for
Giftly's customer/courier gifting marketplace. PostgreSQL stores business state;
Redis supports shared security state, live events and the Taskiq queue. Docker uses
Python 3.13; the project supports Python 3.11+ and uses `uv` exclusively.

Production payments are disabled until the selected Dhamen integration and callback
verification are implemented and tested. Development/test provider fakes are not
production integration proof.

## Documentation

| File | Purpose |
| --- | --- |
| [architecture.md](docs/architecture.md) | High-level system design for product teams and technical advisers. |
| [documentation.md](docs/documentation.md) | Architecture, data, security, configuration, deployment and operations. |
| [api.md](docs/api.md) | Implemented non-admin HTTP/WebSocket contracts and screen coverage. |
| [tasks.md](docs/tasks.md) | Complete review and task register: findings, full evidence, accepted risks, verification limits and implementation status. |

[Full OpenAPI](docs/openapi.json) and [non-admin OpenAPI](docs/mobile-openapi.json)
are machine-readable references. [AGENTS.md](AGENTS.md) defines development rules.
Only these four Markdown references are maintained under `docs/`; UI-agent prompts
are delivered in chat rather than saved as documents.

## Setup

Provision disposable PostgreSQL and Redis services, then work from this repository root:

```bash
uv sync --locked --dev
cp .env.example .env
uv run --locked pre-commit install
```

PowerShell file-copy alternative: `Copy-Item .env.example .env`.
Configure the database/Redis URLs and environment for the intended local services.
The grouped [environment template](.env.example) has public testing defaults and short
comments; replace every testing secret before storing real data or using production.
Do not commit `.env`. Real secrets belong in a protected runtime secret store.

For an already provisioned database:

```bash
uv run --locked python -m app.bootstrap_db
uv run --locked uvicorn app.main:create_app --factory --reload --port 3000
```

Alembic creates/upgrades tables, not the PostgreSQL server/database/credentials.
Startup does not use `create_all`. Initial schema migration seeds system wallets and
20 active Saudi cities. `uv run --locked python -m app.seed_cities` repairs missing
city defaults and refreshes Arabic names without reactivating existing inactive cities.

In development only, API deployment initialization also creates missing verified
couriers `555555555` (Riyadh) and `555555556` (Jeddah), with their wallets.
Phone storage uses `+966` normalization. Existing accounts and moderation edits are
never overwritten. Worker/scheduler entrypoints do not run this account seed.
Run it explicitly after migrations with
`uv run --locked python -m app.seed_development`. Test/production environments skip it.
These fixtures use an encrypted, clearly fictional development identity and no email;
they do not bypass OTP or send provider messages.

Liveness: `/api/health`; dependency readiness: `/api/health/ready`.
Interactive API docs are `/docs` in development and disabled in test/production.
The authenticated admin dashboard is `/v1/admin/admin` when enabled; production
admin login requires TOTP. General admin CRUD includes financial and audit records
by explicit user policy; the associated High risks remain documented in the review.

## Workers and deployment

The default image entrypoint migrates once before spawning the server. Gunicorn
defaults to three workers with an 80-second timeout. Compose uses its dedicated
migration service; API/worker/scheduler wait for it and skip duplicate inherited
migration entrypoints. See the project documentation before changing deployment commands.
Do not run Docker on the user's machine for agent verification.

For separate PaaS applications, select these Dockerfiles with repository-root build context:

| App | Dockerfile | Runtime |
| --- | --- | --- |
| API | Dockerfile | Three Gunicorn workers |
| Background worker | Dockerfile.worker | One consumer, at most two concurrent jobs |
| Scheduler | Dockerfile.scheduler | One scheduler; exactly one replica |

Worker/scheduler expose only /api/health on port3000 and use the shared database/Redis
configuration with the admin dashboard disabled. Keep the same released version across
all three apps. Before starting the separate scheduler, stop any old combined scheduler
or duplicate cron triggers. The API image never switches to a worker based on an environment setting.
PROCESS_ROLE is obsolete and can be removed from existing app settings.
Only the separate worker/scheduler images start background services.

After relevant database commits, the API sends best-effort Taskiq wake-ups for
order pushes, invoice receipts and chat delivery. Workers drain the durable
outboxes; scheduled sweeps recover work if Redis queueing is unavailable.
See docs/documentation.md for connection budgets, configuration and rollback.

Run workers and one scheduler per deployment after migrations:

```bash
uv run --locked taskiq worker app.workers.broker:broker
uv run --locked taskiq scheduler app.workers.scheduler:scheduler
```

Private recording validation requires FFmpeg/ffprobe; the Docker image includes them.
Database migrations, worker schedules, storage policies, backups and
rollback limitations are explained in [operations](docs/documentation.md#7-deployment-migrations-and-maintenance).

## Checks

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app
uv run --locked pre-commit run --all-files
uv run --locked pre-commit run --all-files --hook-stage pre-push
uv run --locked pytest
uv run --locked python -m app.export_openapi
```

Integration tests require disposable PostgreSQL/Redis; unavailable services cause
local skips, not evidence of integration success. CI separately covers database tests,
coverage, generated schema, dependencies and image/decoder checks. Generate OpenAPI
in development mode so the catalog also inventories explicitly development-only routes;
keep `mobile-openapi.json` synchronized and run its drift test.

Current HTTP defaults: authenticated 60 requests/minute; anonymous 30/hour;
admin 100/minute. Redis limiter failures return 503 rather than bypassing controls.
Dates/timestamps, pagination, decimal money, safe literal-text rendering and production
limitations are detailed in the API reference.

Invoice downloads and paid-email attachments share the English Giftly purple PDF
template with GMT+3 dates. One-hour private PDF reuse refreshes immediately when
status, priced items, totals or template change. See docs/api.md for payment sessions.

## Development demo data

The API entrypoint creates only the two development couriers described above.
To explicitly populate the larger synthetic dataset after migrations:

```bash
uv run --locked python -m app.seed_demo
```

In the deployment image, use its existing interpreter: `python -m app.seed_demo`.
This command does nothing unless `ENVIRONMENT=development`. It creates missing
accounts and deterministic orders atomically, preserves existing accounts and
fixture edits, and can be rerun without repeating wallet credits or settlements.
It makes no external provider calls and does not run in API handlers or job loops.
No migration is required. It must not be used against real customer data.

| Local phone | Role | City of sample orders / courier assignment |
| --- | --- | --- |
| 555555551 | Customer | Riyadh |
| 555555552 | Customer | Jeddah |
| 555555553 | Customer | Riyadh |
| 555555555 | Courier | Riyadh |
| 555555556 | Courier | Jeddah |
| 555555557 | Courier | Riyadh |
| 555555558 | Courier | Jeddah |
| 555555559 | Courier | Riyadh |

Phones are stored with the `+966` country prefix. Accounts have no email address;
new couriers use encrypted fictional identity text and are ACTIVE and verified.
Each customer receives ten orders across all lifecycle states in aggregate,
a wallet with one explicitly simulated SAR 2,000 top-up, and priced invoices
with issued, paid, expired and cancelled examples. Completed orders credit
courier earnings through the regular balanced ledger; refunded orders return
escrow to the customer. All amounts are synthetic, not real provider payments.
The sample has 30 orders, 27 invoices (two items each), 27 conversations and
four disputes. Read-only summaries use the existing APIs/admin queries.

Existing phone conflicts or moderated/ineligible accounts are preserved, so a
nonempty development database may receive fewer fixtures. The command reports
the number of newly created orders. Historical demo records are not refreshed
on reruns. Any rollback must target only this seed's deterministic records;
do not reset the database or remove protected financial/audit history.

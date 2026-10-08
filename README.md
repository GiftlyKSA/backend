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
| [codebase_review.md](docs/codebase_review.md) | Current findings, accepted risks and actual verification limits. |
| [tasks.md](docs/tasks.md) | Fresh outstanding work from the current review. |

[Full OpenAPI](docs/openapi.json) and [non-admin OpenAPI](docs/mobile-openapi.json)
are machine-readable references. [AGENTS.md](AGENTS.md) defines development rules.
Only these five Markdown references are maintained under `docs/`; UI-agent prompts
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

Run workers and one scheduler per deployment after migrations:

```bash
uv run --locked taskiq worker app.workers.broker:broker
uv run --locked taskiq scheduler app.workers.scheduler:scheduler
```

Private recording validation requires FFmpeg/ffprobe; the Docker image includes them.
Database migrations, worker schedules, storage policies, backups, VAT repair and
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

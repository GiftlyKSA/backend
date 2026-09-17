# Giftly backend development guidelines

## Plan before implementation

- Read `.agent/CONTINUITY.md` when present, the latest user instructions, [README.md](README.md), affected source and tests before changing code. Source and user instructions take precedence over historical notes.
- Establish the goal, acceptance criteria, scope, affected files, dependencies, security risks, performance impact, and verification steps. A short conversational plan is sufficient for small changes.
- Plan features, fixes, refactors, and dependency changes before implementation. Include migration and rollback steps when persisted data or compatibility changes.
- Investigate root causes and reproduce bugs before making targeted fixes. Prefer the simplest secure, maintainable solution; apply KISS and SOLID pragmatically.
- Make the smallest change that satisfies the requirement without breaking existing behavior. Preserve public contracts and unrelated code; avoid speculative refactors, dependency churn, and broad formatting changes. Use regression checks at affected boundaries.
- Resolve requirements that block dependent work. Wait for approval when the user requests it; do not repeatedly ask permission for already authorized work.

## Stack and architecture

- This repository is the Python backend, server-rendered admin dashboard, workers, and API documentation for Giftly.
- Production Docker images use Python 3.13. `pyproject.toml` currently permits Python 3.11+, and Ruff/mypy target 3.11; preserve compatibility until an upgrade is authorized.
- HTTP and WebSocket APIs use FastAPI, Pydantic v2, and Uvicorn/Gunicorn. The admin dashboard uses Jinja2.
- Persistence uses PostgreSQL 16 with PostGIS and pgcrypto, SQLAlchemy 2 async, asyncpg, GeoAlchemy2, and Alembic migrations.
- Redis 7 supports shared state and Taskiq background processing. Docker and Docker Compose provide the development and deployment container workflow.
- Dhamen is the selected payment provider; do not describe dormant scaffolding as a verified live integration. Other integrations include email, SMS, push, private S3 storage, and CloudFront. Preserve integration interfaces and test fakes.
- Use `uv` exclusively for Python dependencies and execution; retain `uv.lock`. Do not introduce pip, Poetry, conda, or a competing dependency manager.
- Keep dependency direction `routers/admin -> services -> repositories -> models`. Routes and admin views call services; repositories own database access. Cross-domain calls use service interfaces.
- Keep request/response schemas in `app/schemas/`, infrastructure and shared primitives in `app/core/`, provider adapters in `app/integrations/`, jobs in `app/workers/`, and migrations in `app/migrations/`.
- Business rules, ownership checks, permissions, and state transitions belong in services. Do not duplicate them in routes, templates, or workers.

## Accuracy, scope, and environment

- Inspect existing code and exact locked versions before relying on library behavior. Prefer official documentation and upstream release notes; verify current APIs or security advisories when relevant.
- Record dates and sources for time-sensitive conclusions. Mark unknowns `UNCONFIRMED`; never claim a tool or check ran when it did not.
- Begin with read-only investigation. Make small, reviewable patches and preserve unrelated user edits, including deletions.
- Use project-local dependencies and the established Docker workflow. Do not install system packages or change global Git settings as a shortcut.
- Do not start or run Docker on the user's machine. Use non-Docker local checks; leave container/database verification to CI or another explicitly authorized environment and report that limit.
- Never inspect or print secrets for convenience. Keep remote exploration read-only unless writes are authorized; production data changes require explicit scope and a reviewed rollback plan.
- Run commands from this backend repository root. Inspect Compose service definitions before starting services; the default stack includes API, worker, and migration services as well as PostgreSQL and Redis.
- Never delete Docker volumes, reset a database, or rewrite applied migrations to fix routine development failures.

## Standard Python style

- Follow PEP 8 and the checked-in Ruff configuration: four spaces, 100-character lines, snake_case functions/modules/variables, PascalCase classes, and UPPER_SNAKE_CASE constants.
- Use Ruff as the single Python formatter and import sorter. Use double-quoted strings, trailing commas in multiline structures, and Google-style docstrings where required by Ruff.
- Annotate public interfaces and changed functions with precise types. Strict mypy is configured for `app`; avoid untyped boundaries, broad `Any`, and unexplained `type: ignore` comments.
- Validate untrusted values with explicit schemas at boundaries. Prefer domain types and enums to loosely shaped dictionaries and magic strings.
- Keep functions focused and control flow clear. Add comments for non-obvious invariants, security decisions, or workarounds, rather than repeating the code.
- Keep comments minimal: write them only when they explain a necessary non-obvious reason or the user asks. Keep required docstrings concise; remove stale comments when changing their code.
- Use names that communicate domain meaning, units, and responsibility (`amount`, `expires_at`, `courier_id`). Avoid ambiguous abbreviations, generic `data`/`manager` names, boolean flag overload, and hidden side effects.
- Prefer small cohesive modules, explicit dependencies, early validation, and narrow exception handling. Reuse established helpers; introduce abstractions only for a demonstrated shared responsibility.
- Do not reformat unrelated files, suppress checks broadly, or weaken assertions to obtain a green result.
- Use `uv run --locked ruff check --fix .` and `uv run --locked ruff format .` for intentional cleanup; review their diff before staging.
- Follow `.editorconfig` for UTF-8, LF endings, indentation, and final newlines.

## API contracts and security

- Preserve `/api` routes, response schemas, error codes, pagination, and documented client contracts unless a change explicitly requires otherwise.
- Validate body size, file type/size, identifiers, pagination limits, and input ranges. Authentication alone does not establish resource ownership.
- Enforce customer, courier, and admin permissions server-side, including WebSockets and background jobs. Admin views must use the same services as the API.
- Preserve production configuration interlocks, restricted CORS, trusted proxy handling, token validation, refresh rotation, rate limiting, and environment-specific integration selection.
- Never log passwords, tokens, OTPs, encryption keys, payment secrets, or sensitive personal data. Keep `.env` files ignored and `.env.example` free of real credentials.
- Verify webhook signatures and handle duplicate/out-of-order events safely. Do not trust client-supplied payment amounts or payment success claims.
- Keep private media private, enforce object ownership, bound uploads, and retain signed access and encryption protections.
- Review dependency security and compatibility when changing packages. Do not disable TLS verification or hide resolver conflicts.

## OWASP security review baseline

- Use the [OWASP Top 10:2025](https://top10.owasp.org/2025/) categories (verified 2026-09-17) as a review baseline, not as a claim of complete security coverage. Apply the following controls to every relevant backend change.
- **A01 Access control:** deny by default; check object ownership, roles, tenant scope, and field permissions on every read/write and WebSocket action. Prevent mass assignment, path traversal, and SSRF with explicit allowlists and outbound network boundaries.
- **A02 Misconfiguration:** fail closed on invalid production settings, use least-privilege database/storage identities, secure cookie attributes and headers, restrict CORS/proxy trust, and disable debug tools and simulation routes in production.
- **A03 Supply chain:** retain reviewed lockfiles, audit the actual installed/locked dependency graph, review transitive changes, pin build/action dependencies appropriately, and verify artifact provenance. Do not run untrusted install/build scripts with production credentials.
- **A04 Cryptography:** use vetted algorithms and authenticated encryption, separate signing/encryption purposes, rotate versioned keys safely, use secure randomness, verify TLS, and keep secrets out of logs/images/source. Never implement custom cryptographic primitives.
- **A05 Injection:** use parameterized SQL/ORM queries and allowlisted dynamic identifiers, avoid shell execution with untrusted input, escape template output, and validate external data before interpreting it. Never interpolate request values into SQL or commands.
- **A06 Design:** model trust boundaries, abuse cases, money/state invariants, replay, races, and resource exhaustion before implementation. Enforce these with transactions, constraints, idempotency, quotas, and meaningful negative tests.
- **A07 Authentication:** validate token issuer/audience/algorithm/expiry, rotate and revoke refresh tokens, rate-limit OTP/login attempts, avoid account enumeration, and protect administrative sessions and recovery paths. Do not mistake authentication for authorization.
- **A08 Integrity:** authenticate callbacks before settlement, bind amount/currency/reference to the intended transaction, validate signed artifacts, and reject unsafe deserialization. Browser redirects must never establish payment success.
- **A09 Logging/alerting:** record attributable security and financial events with correlation IDs, redact secrets and PII, protect audit records from modification, and define actionable alerts for abuse, permission failures, job failures, and payment anomalies.
- **A10 Exceptional conditions:** fail closed on dependency failure, handle timeouts/cancellation and malformed input explicitly, roll back incomplete transactions, release resources, and return stable safe error envelopes. Never silently convert errors into success or bypass controls during outages.
- Supplement this baseline with API-specific checks: object/property authorization, bounded pagination/uploads/batches, anti-automation controls, inventory of exposed routes, safe outbound calls, and verified third-party responses.

## Performance and scalability

- Prevent N+1 queries: inspect loops and serializers for per-row database calls; fetch related data with explicit joins, eager loading, or batched queries. Add query-count regression checks for growing list endpoints; do not rely on hidden lazy loading.
- Bound lists, batch jobs, attachment sizes, WebSocket messages, and concurrency. Prefer stable indexed cursor pagination for large/changing datasets; avoid unbounded `.all()` reads and expensive counts on hot paths.
- Inspect generated SQL and use `EXPLAIN (ANALYZE, BUFFERS)` on representative disposable data for costly query changes. Account for index write/storage costs and transaction lock duration.
- Keep request handlers nonblocking; reuse appropriately scoped connection/HTTP pools and close them on shutdown. Move durable slow work to workers with explicit retry/backoff limits, idempotency, and failure visibility.
- Scale safely across API/worker instances: avoid process-local authority for sessions, locks, rate limits, or job ownership. Use atomic shared coordination with bounded leases and fencing/renewal where work can outlive a lease.
- Match worker concurrency and database pool sizes to deployment connection budgets. Use backpressure and timeouts rather than allowing queues, tasks, or memory use to grow indefinitely.
- Cache only where justified; define ownership scope, TTL, invalidation, and consistency requirements. Never let stale cached authorization or balances authorize a write.
- Measure latency, query counts, queue lag, failure rates, and resource use before claiming improvement. Document load assumptions; tests on a laptop do not establish production capacity.

## Maintainability and review records

- Keep business rules in services, persistence in repositories, and transport/provider details at boundaries. Prefer explicit typed interfaces and dependency injection over global mutable state.
- Preserve existing behavior and data contracts with minimal edits. Keep migrations, compatibility, rollback, operational visibility, and tests part of the same change when they are affected.
- Review security, performance, scalability/reliability, maintainability, readability, and naming/style separately. Report concrete evidence; do not manufacture findings to fill categories.
- Record requested full reviews as dated Markdown files under `docs/`. Use stable finding IDs, category, severity, status, source paths/lines, trigger/evidence, impact if unresolved, proposed minimal fix, expected system impact, and verification steps.
- Order findings by severity and then impact within each category; provide a global priority index. Separate confirmed defects, intentional limitations, and unverified risks. Report what was inspected/tested and what remains unverified.

## Data integrity, migrations, and performance

- Use the existing money primitives and exact `Decimal` arithmetic quantized to two places through `app/core/money.py`; never use binary floating point for financial calculations. Preserve documented money serialization at API boundaries.
- Preserve double-entry ledger invariants, escrow accounting, invoice revisions, idempotency, and order lifecycle safeguards.
- Use explicit transaction boundaries and appropriate locking/constraints for concurrent financial operations, reservations, claims, and state transitions.
- Treat Alembic autogeneration as a draft. Review extension handling, defaults, indexes, constraints, data backfills, locking impact, and downgrade behavior before applying migrations.
- Test migrations against a disposable PostgreSQL/PostGIS database. Never substitute SQLite for PostgreSQL-specific behavior or use a shared/production database for tests.
- Bound query results and batch sizes, avoid N+1 queries, add indexes based on actual query paths, and measure performance-sensitive changes.
- Keep blocking I/O off async request paths. Use timeouts, bounded retries, and cancellation-safe cleanup for external calls.
- Make retried jobs idempotent and account for partial failures. Avoid holding database transactions open across slow network operations.

## Tests, hooks, and proof

- Set up with `uv sync --locked --dev`, then `uv run --locked pre-commit install`. The configuration installs both pre-commit and pre-push hooks; repeat installation in each clone.
- Pre-commit checks staged Python with Ruff and validates YAML/TOML/JSON, merge markers, and private keys. Checks do not silently rewrite staged files; fix and stage changes deliberately.
- Pre-push runs full-project Ruff lint, Ruff formatting checks, and strict mypy. Do not bypass hooks to conceal failures.
- Run `uv run --locked pre-commit run --all-files` and `uv run --locked pre-commit run --all-files --hook-stage pre-push` to reproduce the gates manually.
- Add meaningful tests for business rules, authorization, concurrency, migrations, failures, and regressions. A regression check should detect the original bug.
- Run focused tests during development and `uv run --locked pytest` before declaring application changes complete. Integration tests require disposable PostgreSQL/PostGIS and Redis services; inspect fixtures and CI setup first.
- CI separately enforces the full suite with at least 85% coverage, OpenAPI generation, security auditing, and Docker checks. Local hooks do not replace CI or remote branch protection.
- For documentation/configuration-only work, validate syntax, paths, commands, and actual hook behavior. Report existing failures separately instead of silently expanding scope.
- Use the verification relevant to the change: migrations, API contracts, Docker build/runtime, and external-provider fakes where applicable. Never infer production integration success from mocks alone.

## Documentation, continuity, and delivery

- Read `docs/README.md` when present before changing API documentation. Keep endpoint descriptions, errors, flows, examples, and generated `docs/openapi.json` aligned with implementation.
- Keep README focused on setup and usage, and AGENTS.md focused on development rules. Include Windows alternatives for platform-specific setup commands.
- If a continuity briefing is maintained, keep `.agent/CONTINUITY.md` short, factual, and free of secrets, with `[PLANS]`, `[DECISIONS]`, `[PROGRESS]`, `[DISCOVERIES]`, and `[OUTCOMES]` sections. Date meaningful entries and tag provenance `[USER]`, `[CODE]`, `[TOOL]`, or `[ASSUMPTION]`.
- Review the final diff for scope, security, performance, and accidental edits. Report commands actually executed, results, and remaining limitations.
- Work is done when the requested behavior and relevant checks are verified, affected documentation is updated, and unresolved failures are explicitly reported.
- Commit descriptively when requested. Push only when authorized, always to `master`; do not push to another branch unless the user explicitly changes this instruction.

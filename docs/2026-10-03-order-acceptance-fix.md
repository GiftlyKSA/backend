# Order acceptance delivery-date constraint fix

The reported acceptance failed because `chk_delivery_date_window` used
`CURRENT_DATE`. PostgreSQL rechecked it when changing the order status, so an
order valid on creation became impossible to update after its delivery day.

Migration `0013_fixed_delivery_window` replaces the moving window with the stored
UTC creation date plus 180 days. Existing orders, assignments, ownership checks,
city restrictions, acceptance locks and WebSocket notifications are preserved.
The creation service rejects dates outside today's UTC date through 180 days
with the existing validation error envelope.

Deploy with the existing single migration bootstrap before starting API workers.
Changing Python code alone cannot repair the deployed database constraint.
The migration validates existing rows; inconsistent historical data must be
reviewed rather than silently rewritten. No data is deleted or backfilled.

Rollback restores the old moving constraint as NOT VALID so historical rows do
not prevent rollback; future updates again encounter the old defect. Prefer a
forward repair. The replacement briefly requires an ALTER TABLE lock and scans
orders to validate the constraint.

Regression coverage checks stable model constraints and creation-date rejection.
The existing PostgreSQL order-flow integration test now ages an order before
accepting it. Database execution remains dependent on disposable PostgreSQL and
Redis availability; Docker is not run locally.

Verification: 540 unit tests passed; the three order integration tests skipped
because the local database was unavailable. Full-project Ruff lint, Ruff format
checks and strict mypy passed. Alembic generated upgrade and rollback SQL
successfully with one migration head. Actual PostgreSQL migration execution and
the aged-order acceptance regression remain unverified locally.

The complete `uv run --locked pytest` run finished with 551 passed, 194 skipped,
and one existing Starlette/httpx deprecation warning. Skipped integration tests
do not establish database or external-provider correctness.

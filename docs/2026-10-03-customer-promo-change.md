# Customer promo change — 2026-10-03

The customer invoice promo flow is implemented. This document records the inspected
scope and remaining validation; it is not a full-codebase security certification.

## Behavior and safeguards

- Codes accept all letter cases and trim surrounding whitespace. Stored/returned
  spelling remains uppercase. Input length, UUIDs and unknown properties are validated.
- Only an active owning customer can apply/remove. Query-scoped invoice ownership
  hides foreign invoices. Payment eligibility is checked under invoice/order locks.
- A pending gateway intent blocks repricing, even if its nominal deadline passed;
  no real payment attempt is cancelled to allow the change.
- Previous issued financial fields/items remain unchanged. The old invoice becomes
  CANCELLED and a new DRAFT is lined, reserved, then ISSUED in the same transaction.
  The database's one-active-invoice index remains authoritative. Expiry is preserved.
- Customer-scoped PostgreSQL transaction advisory locks and unique operation receipts
  serialize retries across workers. Keys bind the target invoice and canonical code.
- Promo replacement locks old/new promo rows in UUID order; a savepoint rolls back
  reservation changes after invalid replacements. Existing global/per-user caps and
  exact Decimal pricing remain authoritative. Settlement consumes the reservation.
- Preview remains read-only and uses the frozen pricing policy. Its own existing
  reserved usage is not counted twice against usage caps.
- Production payment and callback interlocks remain in place. Superseded invoices
  cannot be paid through the existing issued-invoice settlement checks.

## Migration and rollback

Deploy migration `0012_invoice_promo_operations` through the existing single-owner
deployment migration command before serving the new endpoint. It creates a receipt
table, unique customer/key constraint and committed-change audit trigger. It does
not rewrite existing invoice amounts, promo reservations or historical migrations.

Newly priced invoices include a policy snapshot in existing JSON storage. Historical
invoices without a trustworthy snapshot fail closed with 409 for repricing. No
historical VAT/service-fee settings were inferred or backfilled. A user-confirmed
original policy is needed before a separate reviewed compatibility/backfill change.

Rolling application code back leaves new active invoice revisions readable by the
old API. Disable the new endpoint first if rolling back its migration, retain a copy
of operation receipts, and understand that dropping the table loses replay history.
Existing revisions/reservations are retained by the downgrade. Prefer a forward fix.
Actual PostgreSQL upgrade/downgrade needs disposable database verification in CI.

## Attached backlog scope

The wider attachment also proposes chat media, notification history, live Dhamen,
withdrawal history, earnings reports/targets/statements, courier appointments,
order/media/history additions, account management and support tickets. Those are
separate feature areas and are not implemented by this promo change. A scope question
was submitted to the user; no reply has yet established whether to continue them in
this task. Customer occasion CRUD and existing order/chat APIs are preserved.

Live Dhamen additionally needs official provider API/callback authentication and
merchant capability documentation. Do not enable it using guesses or simulated tests.

## Verification boundaries

Regression tests cover canonical codes, role/ownership checks, no-ops, apply/remove,
frozen pricing, preserved expiry, invalid states, preview caps and operation keys.
PostgreSQL tests cover retained history, failed replacements and reservation counts,
plus separate-session apply/apply, apply/payment and apply/cancellation races.
Local full unit suite: 537 passed. Five focused PostgreSQL rollback/race tests
skipped because the disposable local database was unavailable. Ruff lint/format,
strict mypy, both hook stages and OpenAPI drift checks passed. Alembic reported one
head; offline upgrade/downgrade SQL generation passed. No Docker was run.

A locked production dependency audit was attempted with pip-audit 2.10.1 through
uv's isolated tool runtime. It stalled without producing a completed report and
was stopped; dependency security remains unconfirmed. GitHub separately reports
existing repository dependency alerts. This change does not update dependencies.

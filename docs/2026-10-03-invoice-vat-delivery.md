# Invoice VAT, PDF receipts, and overdue-order cancellation

Updated 2026-10-03. User-approved scope: item-only VAT, English PDFs, unpaid-invoice
repair, paid customer email via sndr.sh, daily cancellation at Saudi midnight.

| Task | Status | Urgency / notes |
| --- | --- | --- |
| New invoice VAT on discounted items only | Implemented | High; no courier/service fee VAT |
| Participant and authenticated admin PDF downloads | Implemented | Private, stored amounts, no remote assets |
| Customer email after confirmed payment | Implemented | Existing five-minute receipt sweeper; PDF attached |
| Daily overdue NEW-order cancellation | Implemented | 00:00 UTC+3; Taskiq scheduler and worker required |
| Unpaid VAT repair CLI | Implemented, not run on a database | High; default dry run, bounded pages |
| Paid invoice correction | Excluded by approved scope | Paid records and ledger remain unchanged |
| Real provider acceptance/delivery | UNCONFIRMED | Requires configured sender, key and a controlled test |
| PostgreSQL migration/races | UNCONFIRMED locally | CI requires disposable PostgreSQL/Redis; no Docker run |

## Repair procedure and rollback

Use `uv run --locked python -m app.repair_invoice_vat` for a dry-run page of 200.
Review reported invoice IDs and totals, take a database backup, then use
`uv run --locked python -m app.repair_invoice_vat --apply` for that page.
Use `--after <next_after UUID>` to continue, separately for each dry-run/apply pass.
Each repair commits independently; failures rollback that invoice and stop unexpected
errors. Conflict skips are reported. The script does not automatically scan every page.

Only active, unexpired ISSUED invoices on WAITING_PAYMENT orders qualify.
Paid/cancelled/expired invoices, inconsistent values, and unresolved payments are skipped.
Original items, item VAT rates, discounts, fees and deadline are retained; only fee VAT
is removed. Old invoices become CANCELLED; corrected revisions become active. Promo
reservations transfer through a released/new reservation without changing usage count.
Locks serialize repair with payment, cancellation and promo changes. Dry runs rollback.
No original financial values, ledger entries or paid-invoice records are rewritten.

Rollback should use a reviewed forward revision from the retained originals, never
rewrite paid records or remove historical revisions. Deployments do not automatically
execute this repair script. Migration 0013 must already be applied for aged-order writes.

## Email contract and deployment

Official sources checked 2026-10-03:
[API reference](https://www.sndr.sh/docs/api-reference),
[getting started](https://www.sndr.sh/docs/getting-started).
Use SNDR_BASE_URL=https://api.sndr.sh, SNDR_API_KEY, SNDR_FROM_EMAIL, SNDR_FROM_NAME.
The sending domain must be verified. SNDR_INVOICE_PAID_TEMPLATE_KEY remains an optional
compatibility setting; the adapter sends locally composed plain text through /v1/send.
Bearer authentication, a stable invoice Idempotency-Key, and a base64 PDF attachment
use the documented format. Provider idempotency retention is UNCONFIRMED; this does
not promise perpetual exactly-once delivery or inbox delivery. Durable claims prevent
simultaneous sweeps; rejected/timed-out sends remain pending. Missing email is not
stamped as sent. Production payment integration remains disabled until verified.

The cancellation worker locks at most 200 rows per transaction with SKIP LOCKED,
up to 100 batches per invocation. Overlapping runs and acceptance are safe under
PostgreSQL row locks. It logs a warning at the 20,000-order cap; run the task again
if this operational limit is reached. Committed database triggers attribute the
cancellations and repairs to system activity. Existing invoice/payment expiry keeps
its ten-minute schedule; only overdue unaccepted orders use the midnight schedule.

PDFs show stored financial values and English labels. Non-ASCII item names use
English numbered labels; customer-facing JSON retains the original titles. Rendering
is offloaded from the event loop, uses no external assets, and creates no public file.

## Verification and remaining work

553 unit tests passed. The complete suite (`uv run --locked pytest -n 4`) finished
with 564 passed and 194 skipped; the four warnings are the existing Starlette/httpx
deprecation repeated across test workers. Ruff lint/format and strict mypy passed.
Alembic generated upgrade and downgrade SQL for migration 0014 successfully.
The PDF sample was rendered and visually inspected. The new locked ReportLab 4.5.1
dependency adds Pillow 12.3.0; typing stubs are development-only. No unrelated locked
versions were changed. A fresh dependency advisory scan remains UNCONFIRMED;
existing repository dependency alerts are not resolved by these feature changes.

Remaining deployment work: apply migration 0014, restart worker and scheduler,
configure sndr.sh and verify a controlled paid receipt, run the reviewed repair
dry run/apply pages against the intended database, and let CI exercise PostgreSQL
locks, migrations and external-service boundaries. The repair has not modified
any real database. The index migration builds a small partial index transactionally;
assess its table-lock impact before applying it to a large orders table.

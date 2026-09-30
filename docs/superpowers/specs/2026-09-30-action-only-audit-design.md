# Action-only audit trail design

Date: 2026-09-30

Status: Design approved in chat; written specification awaiting review

## Goal and accepted behavior

The audit trail will describe **successful, committed data changes**, not HTTP traffic. A customer, courier, administrator, or system process that creates, updates, or deletes an application record produces an audit row with the actor category, actor user ID when available, operation, table, record ID, and time. An order status change or financial state change is an update. Reads, rejected requests, login/logout, token rotation, chat socket connections, and scheduled-job start/completion do not produce audit rows. The user confirmed this boundary on 2026-09-30.

Acceptance criteria:

1. No new `HTTP_*`, WebSocket lifecycle, or `SYSTEM_JOB_RUN` rows are created. Normal server error/access logging remains available for operations; it is not the database audit trail.
2. Every committed insert/update/delete to an included application table creates one audit row per changed record, except authentication-only `users.auth_version` updates described below. Rolled-back writes and unchanged updates create none.
3. New rows have `metadata.actor_category` equal to `CUSTOMER`, `COURIER`, `ADMIN`, or `SYSTEM`. Human actions use `actor_user_id`; autonomous jobs use null. No row values, tokens, OTPs, message text, or map links enter audit metadata or application audit logs.
4. The admin Audit page can filter these four categories and shows time, actor, operation, table, and record. It does not present route/status columns or historical HTTP records in its action list.
5. Existing audit rows are preserved. There is no cleanup or destructive backfill in this change.

## Approach and rationale

**Chosen:** PostgreSQL row triggers for included application tables, with transaction-local actor context set by the backend. The trigger records the change in the same database transaction. This covers SQLAlchemy ORM writes and the admin browser's direct SQL. Service-only audit calls would miss uninstrumented mutations; SQLAlchemy ORM hooks would miss Core SQL and direct database writes. The trigger writes metadata only and does not copy record values.

The trigger covers mapped application tables except `audit_logs`, `refresh_tokens`, `admin_sessions`, and `otp_attempts`. The last three are authentication/session machinery: recording them would recreate login/logout or failed-attempt noise the user excluded. `audit_logs` is excluded to avoid recursion. Device tokens, media grants, orders, messages, invoices, wallets, transactions, notification cursors, and other application tables remain included, including system maintenance changes. All included tables currently have a single UUID primary key; the migration validates this before attaching a trigger and passes the primary-key column name to the trigger function. For `users`, an update that changes only `auth_version` or `updated_at` is excluded so logout/revocation does not reappear as a user edit.

Each row uses `action` = `CREATE`, `UPDATE`, or `DELETE`, `entity_type` = the table name, and `entity_id` = the row's UUID primary key. `metadata` contains only `actor_category`. An absent actor category during a request-originated data write is an error rather than a misleading `SYSTEM` event. An explicitly system-originated worker, migration, seed, or verified provider callback uses `SYSTEM`. This is attribution, not a new authorization mechanism.

## Actor context and data flow

- The API/admin database dependency marks the transaction as request-originated. After bearer or dashboard authentication, it sets transaction-local actor category and user ID from the verified `Actor` or admin session. Customer and courier are distinct; the existing `USER` grouping is retired for new rows.
- Registration sets its requested role before inserting the user and attaches the new user ID as soon as it exists. The user-creation audit row uses the new user's ID, and the wallet/profile rows use the same actor. Authentication-only writes remain excluded.
- A chat WebSocket sets the authenticated participant context in its message transaction. Socket connection and rejection paths stop writing audit rows.
- Workers and trusted provider callbacks set `SYSTEM` for their mutation transaction. Migrations and seed scripts also use `SYSTEM`; a transaction without request origin defaults to that category, while a request without a verified category fails closed on data writes. A shared helper applies transaction-local PostgreSQL settings using `set_config(..., true)` so PgBouncer connection reuse cannot carry actor identity between transactions.
- The `AFTER INSERT/UPDATE/DELETE` trigger inserts into `audit_logs` only when the row actually changed. If that insert fails, the business transaction fails and rolls back; the system does not silently accept an unaudited change.

The current explicit mutation audit calls in admin/user/withdrawal/WS services are removed or narrowed so the same data change is not shown twice. Generic admin edits to excluded auth/audit tables retain one explicit metadata-only action record because their row triggers are intentionally absent. Existing `AuditRepository.list_recent` remains the read boundary. The current post-commit `giftly.audit` mirror only sees ORM audit rows; trigger-created rows require a separate, reviewed forwarding path if independent log retention is required. This change does **not** claim to solve the existing tamper-resistance finding `SEC-17`.

## Admin UI and compatibility

The Audit page tabs become All, Customer, Courier, Admin, and System. Filters remain actor ID, action, and entity/table. List results show committed action records only; historical `HTTP_*`, socket lifecycle, and job-outcome rows remain stored but are omitted from this action view. Other historical action rows remain accessible through the generic admin table browser. Arabic and English labels are updated together. The API's public routes and response schemas do not change.

The README and audit-related review/task-tracker entries will be updated to describe the new behavior accurately. The earlier per-request database load finding `PERF-09` and post-handler audit-gap finding `REL-11` will be marked addressed only after focused tests pass; the cost of one audit row per changed data record and independent retention remain separate operational risks.

## Migration and rollback

Add a new Alembic revision after `0008_audit_trail_indexes`. It creates one metadata-only trigger function and attaches row triggers to the included tables; it neither rewrites older migrations nor deletes old audit data. The migration checks each table's UUID primary key and uses fixed mapped table/column identifiers, not request strings. Deployment runs the migration once before starting the new API and workers. Mixed old/new processes can temporarily produce duplicate semantic rows; a coordinated rollout or brief maintenance window prevents prolonged duplication.

Rollback drops only the new triggers and function and restores the previous application version together with its audit behavior. Audit rows already committed are preserved. A schema-only rollback while new application code is running is not safe because it would leave changes unaudited. Migration failure rolls back and blocks the new release.

## Verification

Write failing behavior tests before implementation. Unit checks cover removal of HTTP/WS/job lifecycle audit, role-to-category mapping, and the admin Audit page filters. Disposable PostgreSQL integration tests cover ORM and Core SQL create/update/delete; customer, courier, admin, and system attribution; registration; unchanged updates; transaction rollback; excluded auth tables; and absence of sensitive values. Verify generic admin CRUD and worker batches continue to work, then run the full unit/integration suite and repository gates. Measure audit-write volume and latency on representative data before claiming a performance improvement. Docker will not be run on the user's machine; unavailable database/deployment verification must be reported explicitly.

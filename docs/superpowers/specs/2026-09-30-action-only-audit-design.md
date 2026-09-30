# Action-only audit trail design

Date: 2026-09-30

Status: Implemented locally; PostgreSQL deployment verification pending

## Goal and accepted behavior

The audit trail will describe **successful, committed data changes**, not HTTP traffic. A customer, courier, administrator, or system process that creates, updates, or deletes an application record produces an audit row with the actor category, actor user ID when available, operation, table, record ID, and time. An order status change or financial state change is an update. Reads, rejected requests, login/logout, token rotation, chat socket connections, and scheduled-job start/completion do not produce audit rows. The user confirmed this boundary on 2026-09-30.

Acceptance criteria:

1. No new `HTTP_*`, WebSocket lifecycle, or `SYSTEM_JOB_RUN` rows are created. Normal server error/access logging remains available for operations; it is not the database audit trail.
2. Every committed insert/update/delete to an included application table creates one audit row per changed record, except authentication-only `users.auth_version` updates described below. Rolled-back writes and unchanged updates create none.
3. New rows have `metadata.actor_category` equal to `CUSTOMER`, `COURIER`, `ADMIN`, or `SYSTEM`. Human actions use `actor_user_id`; autonomous jobs use null. No row values, tokens, OTPs, message text, or map links enter audit metadata or application audit logs.
4. Separate user (customer and courier), admin, and system pages show time, actor, operation, table, and record. They do not present route/status columns or historical HTTP records.
5. Migration `0009_action_only_audit` deletes historical `HTTP_*` rows as requested. Other historical rows remain stored but operational rows are hidden from these action pages.

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

HTTP, WebSocket lifecycle, and scheduled-job outcome audit calls are removed. Explicit semantic mutation events remain in some services; they may appear alongside the row-level event for the same operation. Generic admin edits to excluded auth/audit tables retain an explicit metadata-only action record. `AuditRepository.list_recent` is the read boundary. The current post-commit `giftly.audit` mirror only sees ORM audit rows; trigger-created rows require a separate, reviewed forwarding path if independent log retention is required. This change does **not** solve the tamper-resistance finding `SEC-17`.

## Admin UI and compatibility

The audit area has three separate pages: user (customer and courier), admin, and system. Filters remain actor ID, action, and entity/table. The migration deletes historical `HTTP_*` rows; historical socket lifecycle and job-outcome rows remain stored but are omitted from these action pages. Arabic and English labels are updated together. The API's public routes and response schemas do not change.

The README and task tracker describe the new behavior. `PERF-09` and `REL-11` remain partially addressed until PostgreSQL integration, performance, and forwarding checks run.

## Migration and rollback

Alembic revision `0009_action_only_audit` follows `0008_audit_trail_indexes`. It creates one metadata-only trigger function and attaches row triggers to fixed, reviewed application tables, then deletes historical `HTTP_*` rows. Deployment runs the migration once before starting the new API and workers. Mixed old/new processes can temporarily produce duplicate semantic rows; a coordinated rollout or brief maintenance window prevents prolonged duplication.

Rollback drops the new triggers and function and restores the previous application version together with its audit behavior. Deleted historical HTTP rows require a database backup to recover. A schema-only rollback while new application code is running is not safe because it would leave changes unaudited. Migration failure rolls back and blocks the new release.

## Verification

Unit checks cover removal of HTTP/WS/job lifecycle audit, role-to-category mapping, and the three admin pages. Disposable PostgreSQL checks should cover ORM and Core SQL changes, all actor categories, registration, rollback, and sensitive-value exclusion. Run the repository gates and measure audit-write volume on representative data before claiming a performance improvement. Docker will not be run on the user's machine; unavailable database/deployment verification must be reported explicitly.

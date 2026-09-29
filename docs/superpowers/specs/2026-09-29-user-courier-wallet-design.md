# User, Courier Profile, and Wallet Lifecycle Design

**Status:** Draft for review
**Date:** 2026-09-29
**Scope:** Shared user identity, courier-only profile data, and automatic wallet creation

## Goal

Keep one generic `users` table for customer, courier, and admin accounts; keep courier-only
identity and city assignment in a one-to-one `courier_profiles` table; and ensure every
new user has exactly one wallet regardless of whether creation comes from the API or admin
dashboard. Preserve authentication, courier review, ratings, existing API behavior where
possible, encrypted identity handling, and ledger invariants. Do not add a customer table.

The user approved this design direction on 2026-09-29, including removal of
`identity_fingerprint` and its duplicate-document check, plus wallet creation for every role.
This document makes the implementation details reviewable before planning or code changes.

## Current behavior and constraints

- `User` currently represents all three roles, and there is no `CustomerProfile` table.
- `CourierProfile.user_id` is already a one-to-one primary/foreign key to `users.id` and
  references a `City` row.
- Orders and conversations use user IDs. Services enforce actor roles and ownership; those
  checks must remain authoritative after schema changes.
- User fields currently include `auth_version`, a multi-state `status`, rating summary,
  avatar storage key, payment-provider identifier, and `deleted_at`, in addition to core
  identity fields. Authentication, admin, user, and rating code reads these fields.
- Courier national ID and passport values are encrypted separately using AES-GCM with
  column-specific associated data. Registration and admin maintenance also create an
  HMAC fingerprint and reject a duplicate fingerprint.
- The authentication repository creates customer wallets explicitly, but courier wallets
  are created as part of courier profile setup. Generic admin user creation does not use
  that registration repository, so repository-only wallet creation cannot cover every path.
- Wallet types currently include customer, courier, and system wallets. A partial unique
  index permits only one non-system wallet per user; the wallet constraint does not yet
  support admins.
- `users` records participate in access-token revocation, refresh-token rotation, courier
  approval/rejection, rating summaries, and soft deletion. Moving fields must preserve
  these semantics rather than dropping them.

## Proposed data model

### `users`

Limit the shared row to the requested generic fields:

| Field | Proposed representation | Notes |
|---|---|---|
| `id` | UUID primary key | Existing identifiers and foreign keys remain stable. |
| `phone` | Required, unique string | Existing uniqueness remains. |
| `email` | Nullable string, unique when present | Existing partial unique index remains. |
| `date_of_birth` | Nullable date | Existing API representation remains unless separately approved. |
| `gender` | Nullable typed value | Proposed values: `MALE`, `FEMALE`, `OTHER`, `PREFER_NOT_TO_SAY`; validate at request/admin boundaries. |
| `full_name` | Nullable string | Existing length and response behavior remain. |
| `role` | `CUSTOMER`, `COURIER`, or `ADMIN` | One table for every account type. |
| `is_active` | Required boolean | Account access gate; disabling a user revokes credentials in the same service transaction. |
| `created_at`, `updated_at` | UTC timestamps | Database defaults remain authoritative. |

No customer-specific profile table is introduced. Customer-only information remains in
the existing domain tables that own it.

### Operational data moved out of `users`

The user table is to contain only the fields above. Existing non-core values must remain
available through typed one-to-one extensions, not be lost:

- `user_auth_state`: `auth_version` and the soft-deletion timestamp. Authentication reads
  this state with an explicit join/eager load; logout and account-disable operations update
  it transactionally. `is_active` becomes the shared access gate.
- `user_profile_stats`: rating, rating count, and avatar storage key. Existing rating
  aggregates remain the source-of-truth projection and retain their current update rules.
- `user_payment_profile`: existing gateway customer identifier, retained as dormant
  provider data until its removal or replacement is separately reviewed.

Backfill one row per existing user in each extension. A database `AFTER INSERT` trigger
must create the required authentication-state, profile-stat, and payment-profile rows with
safe defaults, in addition to the wallet. This covers admin generic-table writes and future
insert paths. Any failure rolls back the user insert. Authentication must never issue a
token unless its state row exists.

### `courier_profiles`

Keep exactly one profile per courier, keyed by `user_id` and constrained to a user with
role `COURIER`. Represent identity as:

- `identity_document_type`: `NATIONAL_ID` or `PASSPORT`.
- `identity_document_number_encrypted`: encrypted at rest; never expose stored ciphertext
  or log plaintext.
- `city_id`: required foreign key to `cities.id`; city selection continues to use active
  city records.
- Courier review state and existing verification, payout, and profile fields remain
  courier-only and keep their current behavior. Replace the cross-role `User.status`
  dependency with `verification_status` (`PENDING`, `APPROVED`, `REJECTED`) on this
  profile; courier eligibility requires `APPROVED` and an active user account.

Remove `identity_fingerprint`, its unique index, all generation/lookup code, the pepper
configuration setting, and the admin field. As requested, duplicate identity
documents will no longer be detected or blocked. Identity numbers remain encrypted; the
admin form accepts a write-only replacement and never pre-fills the number.

The current national-ID/passport ciphertext uses different AEAD associated data for each
column. A safe migration to one encrypted column must decrypt and re-encrypt each existing
value with the configured key and the new column AAD. It must abort and roll back on any
decryption/key error, never print plaintext, and preserve the old columns until all rows
are verified. Do not copy ciphertext between columns because that would invalidate AEAD
authentication.

### Wallet lifecycle

Use a PostgreSQL `AFTER INSERT` trigger on `users` to insert a zero-balance user wallet in
the same transaction. Map `CUSTOMER` to `CUSTOMER`, `COURIER` to `COURIER`, and `ADMIN` to
a new `ADMIN` wallet type. Keep the unique per-user wallet index as the final duplicate
guard. Backfill existing users missing a wallet with the same role mapping; preserve all
existing balances and ledger rows. Do not generate system wallets through this trigger.

The database trigger covers API, admin dashboard, scripts, and any future insert path.
Remove manual wallet inserts from registration to avoid duplicate responsibility. Wallet
creation failure must fail the user insert; it must never leave an account without its
wallet or silently return success. An admin wallet starts at zero and does not grant access
to customer/courier financial actions; service authorization remains role-based. Role
changes must keep the wallet type consistent and must not reinterpret a wallet with ledger
history; role-change safeguards are implemented and tested at the service/database boundary.

## Migration and rollout

1. Add the nullable `gender`, `is_active`, extension tables, courier identity type/value,
   and the `ADMIN` wallet enum value using forward Alembic revisions. Do not edit applied
   revisions. If PostgreSQL enum transaction semantics require it, isolate the enum value
   addition in a separate committed revision before any revision uses it.
2. Backfill extension rows and `is_active` from current user rows without changing role,
   identifiers, token versions, ratings, gateway identifiers, or account deletion state.
   Set `is_active` false for banned or soft-deleted users and true otherwise. For couriers,
   map `PENDING_VERIFICATION` to `PENDING`, `REJECTED` to `REJECTED`, and an active,
   verified profile to `APPROVED`; an active but unverified profile maps to `PENDING`.
   This keeps pending/rejected couriers able to authenticate and follow the existing review
   or reapplication flow while preventing courier actions until approved.
3. Backfill identity type and re-encrypted identity values from existing encrypted columns.
   Keep legacy columns until a verification query confirms every profile has exactly one
   supported type and a decryptable new ciphertext. Never log identities or keys.
4. Backfill missing wallets with role-correct types, then install the user-insert trigger.
   Existing wallet balances, transactions, and system wallets must remain unchanged.
5. Deploy code that reads/writes the new model, then remove legacy user columns, legacy
   courier identity columns, fingerprint index/column, and obsolete pepper configuration
   only after no runtime code references them.
6. Downgrade must restore the prior column shape and re-encrypt courier identity values
   with their original column AAD. Rollback must not discard newly created identity values,
   wallet rows, or financial history. If a safe downgrade cannot be guaranteed, stop before
   applying the destructive contract revision and document the recovery procedure.

Migrations only operate inside an already-provisioned PostgreSQL database. Validate them
against a disposable PostgreSQL database in CI or an approved environment; do not run
Docker locally.

## Application behavior and compatibility

- Preserve login, refresh rotation/reuse detection, logout-all-devices, admin sessions,
  account disabling, courier pending/rejected/approved flows, rating responses, and current
  ownership checks.
- During migration, update schemas/services/repositories and admin forms together. Keep
  external request/response contracts stable where possible; if identity input field names
  must change, document the compatibility transition in the API schema and OpenAPI output.
- API and admin user creation use the same role semantics. The database trigger, rather
  than a particular repository, guarantees wallet creation.
- Preserve the existing `delivery_map_url` field and contract unchanged. Do not add a
  delivery-location text field, coordinates, latitude/longitude, or geometry as part of
  this work. Courier city assignment remains the separate foreign-key relation to `cities`.
- Courier-only data must not be accepted for customer/admin users. Courier operations must
  continue to check both `role == COURIER` and review/active state.
- A courier profile is one-to-one with a user; profile operations and courier endpoints
  reject users whose role is not `COURIER`. A missing profile or non-approved review state
  never grants courier privileges.
- An admin must not be able to set encrypted fields directly to arbitrary ciphertext or
  bypass identity encryption by editing generic table fields.
- Remove per-field `Clear ...` checkboxes from the shared admin add/edit form, including
  nullable foreign keys. Prefilled ordinary nullable fields clear to `NULL` when submitted
  blank; on create, blank optional fields continue to use database defaults. Blank
  write-only secret/encrypted inputs preserve the stored value. Keep the separate delete
  confirmation checkbox and existing CSRF, authorization, and password step-up controls.
- Keep wallet and ledger endpoints role-authorized. The new admin wallet type is an
  internal balance container only; it does not permit administrators to spend, top up, or
  withdraw through customer/courier routes.

## Security, performance, and maintainability requirements

- Retain token issuer, audience, algorithm, expiry, revocation, and refresh-family checks;
  move `auth_version` without creating an authorization gap.
- Treat `is_active` as a server-enforced account gate and revoke existing credentials on
  disable. Do not trust client role, city, wallet type, identity type, or review state.
- Use parameterized SQL, database constraints, encrypted identity fields, safe error
  handling, and redacted logs. No plaintext identity, OTP, token, or key material in logs.
- Eager-load or explicitly join auth/profile extensions in user-list and token-validation
  paths. Avoid per-user queries and add query-count regression coverage for list paths.
- Wallet creation must be atomic, indexed, and O(1) per user. Backfill in bounded batches
  with a stable key order and report counts only.
- Keep routes thin, business rules in services, database access in repositories, and
  migration-specific transformation logic isolated and testable.
- Keep comments minimal and explanatory only where invariants or migration safety are not
  clear from code.

## Verification plan

- Unit tests for gender validation, active-state handling, courier identity type validation,
  encryption/decryption AAD, and role/profile rules.
- PostgreSQL integration tests for user inserts from direct SQL, API registration, and
  admin generic CRUD; each role receives exactly one correctly typed wallet in the same
  transaction, and rollback leaves neither a user nor wallet.
- Migration tests from the current head with customer, courier, admin, null/optional fields,
  both identity document types, existing wallets, and financial ledger rows. Verify data
  and ciphertext round trips without printing sensitive values.
- Auth regression tests for login, refresh rotation/reuse, logout, admin session revocation,
  disabled accounts, pending/rejected courier login, and role authorization.
- Query-count tests for representative user lists and authentication lookups to detect N+1
  behavior introduced by extension tables.
- Admin CRUD tests verify no fingerprint field or per-field clear checkbox exists,
  optional blank values clear correctly, blank secret inputs preserve stored values,
  encrypted identity input is write-only, relationships select city records, delete
  confirmation remains required, and admin role does not gain financial endpoint access.
- Run Ruff, strict mypy, relevant pytest suites, OpenAPI export validation, and migration
  checks. Do not run Docker on the user's machine.

## Risks and decisions for spec review

1. **Encrypted identity conversion:** re-encryption requires the active and historical field
   encryption keys. Missing keys or malformed ciphertext must stop migration safely.
2. **Duplicate documents:** removing the HMAC check intentionally permits duplicate IDs.
   This was explicitly approved by the user.
3. **New admin wallet type:** database and UI code must handle `ADMIN` without granting
   payment operations. Historical test fixtures that model admins with customer wallets
   must be corrected.
4. **Account-state mapping:** `is_active` replaces the shared active/banned access gate;
   courier review states remain distinct. Test every status mapping and soft-deleted user
   behavior before dropping legacy `status`/`deleted_at` columns.
5. **Schema shape:** the proposed three typed user extension tables keep the requested
   `users` row minimal but add joins. If minimizing schema objects is preferred, use one
   typed extension table only if its fields remain understandable and queryable.

No implementation has been performed as part of this draft.

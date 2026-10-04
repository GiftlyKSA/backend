# Environment configuration and secrets

Last reviewed: 2026-10-04. Scope: application settings, integration adapters,
entrypoints, workers, migrations, Compose and locked server implementations.
Real `.env` files, credentials and deployment settings were not inspected.

## Template cleanup

The template contains only consumed settings: 80 active entries and two optional
RSA key entries. All 79 Pydantic settings are represented; three additional controls
are read by the deployment entrypoint or Gunicorn/Uvicorn.
Every entry has a description and an example. Blank secrets are intentionally blank;
copying the template alone does not supply the required signing/encryption secrets.

Removed unused placeholders: `DHAMEN_BASE_URL`, `DHAMEN_APP_KEY`, `DHAMEN_APP_ID`,
`DHAMEN_CLIENT_ID`, `DHAMEN_AUTHORITY_PROFILE_ID`, `DHAMEN_RETURN_URL` and the
speculative callback placeholder. Dhamen remains the selected provider, but no active
client reads these variables. Production payments remain disabled.

Removed `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and `REDIS_PASSWORD`
from the template because Compose does not interpolate those variables. It supplies
literal local-development service credentials. Changing application URLs does not
provision a database or change server credentials. Compose still supplies its own
explicit development environment; a copied `.env` does not override all those values.

`FORWARDED_ALLOW_IPS` is retained: the installed Gunicorn/Uvicorn implementations
read it directly. `WEB_CONCURRENCY` and `GUNICORN_TIMEOUT` are existing entrypoint
controls newly documented in the template. Set/export these three in the actual server
process environment; they are not Pydantic settings loaded from the application's `.env`.

`SNDR_INVOICE_PAID_TEMPLATE_KEY` is retained because the receipt service consumes it
and passes it to the email interface and development/test fake. It is a logical receipt
label, not a vendor template selector: the current sndr.sh client constructs its own email.

## New operational controls

| Variable | Default | Accepted range | Effect |
| --- | --- | --- | --- |
| `DB_POOL_SIZE` | 5 | Integer 1-100 | Persistent pool size per engine/process. |
| `DB_MAX_OVERFLOW` | 10 | Integer 0-100 | Temporary connections above the pool size; zero disables overflow. |
| `DB_POOL_TIMEOUT_SECONDS` | 30 | Finite number greater than 0, at most 60 | Wait for a pooled connection; not a statement or connect timeout. |
| `INTEGRATION_HTTP_TIMEOUT_SECONDS` | 10 | Finite number greater than 0, at most 15 | Email/SMS/push HTTP connect, read, write and pool timeouts. |

Defaults preserve previous SQLAlchemy and provider behavior. Existing job deadlines
still apply. The HTTP setting does not affect S3, the Redis client or the blocking
Taskiq queue read. Invalid values reject startup rather than silently disabling bounds.

Connection budgeting must include every API process, worker engine and replica.
For example, three API workers with the defaults allow up to 45 pooled connections
in total, before workers, migrations and any additional processes are counted.
Increasing pools does not increase the database server's connection limit. Measure
pool waits, database load and provider latency before tuning.

Settings are cached for the process lifetime. Change configuration in the deployment
platform and restart/redeploy API, worker and scheduler processes that consume it.
No migration is required for these controls. Roll back by removing the new overrides
or restoring the documented defaults and restarting those processes.

The codebase review also found fixed job leases, cron schedules, media validation
ceilings and order quotas. They were left unchanged: those values are coupled to
concurrency safety, API schemas or business decisions and should not become arbitrary
production knobs without corresponding validation and compatibility review.

## Where to save secrets

Use an ignored local `.env` only for development, with local credentials and restricted
file permissions. `.env.example` is public documentation and must never contain real
keys. Both Git and Docker ignore real `.env` files; do not copy them into images.

For Cranl/PaaS, use its protected secret store if available, with runtime injection
under the existing variable names. Whether Cranl provides encrypted storage, access
controls and rotation is UNCONFIRMED. Verify those platform capabilities rather than
assuming an ordinary configuration text field is a secret manager. If the platform
cannot provide them, use a dedicated managed secrets service and an authenticated
runtime injection/retrieval mechanism. AWS Secrets Manager is one suitable option
for an AWS environment; this repository does not yet retrieve secrets from it directly.

Environment variables are a delivery mechanism, not encrypted secret storage.
They can be exposed by process inspection, debugging or compromised application code.
Limit access to the service and its configuration; avoid dumping environment values,
sharing terminal history or passing secrets through build arguments. Prefer workload
identity/short-lived credentials where supported. The current S3 adapter explicitly
requires static access-key variables; switching to workload identity needs a separate
adapter and validation change, not just deleting those keys.

Use independent random values for JWT signing, OTP HMAC, field encryption, identity
pepper and admin sessions. An authenticator TOTP seed must be a separate Base32 value.
Store recoverable encryption-key versions in the secret store with protected backups.
Do not overwrite/remove old encryption keys until data is re-encrypted using the
existing rotation workflow. Changing identity peppers can invalidate fingerprints;
changing signing/session secrets invalidates dependent credentials. Plan rotation
and keep access to encrypted data during rollback.

Recommendations checked 2026-10-04 against
[OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html),
[OWASP Cryptographic Storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html)
and [AWS Secrets Manager best practices](https://docs.aws.amazon.com/secretsmanager/latest/userguide/best-practices.html).
These describe storage, access control and rotation; they do not establish Cranl capabilities.

## Consumption inventory

Paths below identify representative actual consumers, not only declarations or test
references. Indirect settings are identified explicitly. Source is authoritative.

| Variable | Consumer |
| --- | --- |
| `ENVIRONMENT` | `app/main.py`, `app/integrations/factory.py`, `app/services/otp_service.py` |
| `DEBUG` | `app/core/db.py` |
| `LOG_LEVEL` | `app/main.py` |
| `WEB_CONCURRENCY` | `app/entrypoint.py`: default Gunicorn worker command |
| `GUNICORN_TIMEOUT` | `app/entrypoint.py`: default Gunicorn timeout command |
| `DATABASE_URL` | `app/core/db.py`, `app/migrations/env.py` |
| `REDIS_URL` | `app/core/redis.py`, `app/workers/broker.py` |
| `DB_POOL_SIZE` | `app/core/db.py` |
| `DB_MAX_OVERFLOW` | `app/core/db.py` |
| `DB_POOL_TIMEOUT_SECONDS` | `app/core/db.py` |
| `INTEGRATION_HTTP_TIMEOUT_SECONDS` | `app/integrations/factory.py` |
| `JWT_ALGORITHM` | `app/core/jwt.py` |
| `JWT_SECRET` | `app/core/jwt.py`, `app/services/otp_service.py` |
| `JWT_ACCESS_TTL_MINUTES` | `app/core/jwt.py`, `app/services/admin_service.py` |
| `JWT_REFRESH_TTL_DAYS` | `app/services/auth_service.py` |
| `JWT_ISSUER` | `app/core/jwt.py` |
| `JWT_AUDIENCE` | `app/core/jwt.py` |
| `REFRESH_TOKEN_RETENTION_DAYS` | `app/workers/expiry.py` |
| `FIELD_ENCRYPTION_KEYS` | `app/core/config.py`: `encryption_keys()`; used by `app/core/crypto.py` and key rotation |
| `FIELD_ENCRYPTION_KEY_VERSION` | `app/services/admin_service.py`, `app/services/admin_table_service.py`, `app/services/auth_service.py` |
| `IDENTITY_FINGERPRINT_PEPPER` | `app/services/admin_service.py`, `app/services/admin_table_service.py`, `app/services/auth_service.py` |
| `OTP_HMAC_KEY` | `app/services/otp_service.py` |
| `OTP_TTL_SECONDS` | `app/services/auth_service.py`, `app/services/otp_service.py` |
| `OTP_MAX_PER_WINDOW` | `app/services/otp_service.py` |
| `OTP_WINDOW_SECONDS` | `app/services/otp_service.py` |
| `OTP_BLOCK_SECONDS` | `app/services/otp_service.py` |
| `AWS_REGION` | `app/integrations/factory.py` |
| `AWS_ACCESS_KEY_ID` | `app/integrations/factory.py` |
| `AWS_SECRET_ACCESS_KEY` | `app/integrations/factory.py` |
| `S3_BUCKET_NAME` | `app/integrations/factory.py` |
| `CLOUDFRONT_DOMAIN` | `app/integrations/factory.py` |
| `CLOUDFRONT_KEY_PAIR_ID` | `app/integrations/factory.py` |
| `CLOUDFRONT_PRIVATE_KEY` | `app/integrations/factory.py` |
| `SNDR_API_KEY` | `app/integrations/factory.py` |
| `SNDR_BASE_URL` | `app/integrations/factory.py` |
| `SNDR_FROM_EMAIL` | `app/integrations/factory.py` |
| `SNDR_FROM_NAME` | `app/integrations/factory.py` |
| `SNDR_INVOICE_PAID_TEMPLATE_KEY` | `app/services/receipt_service.py` |
| `SMS_PROVIDER_KEY` | `app/integrations/factory.py` |
| `SUPABASE_URL` | `app/integrations/factory.py` |
| `SUPABASE_SERVICE_KEY` | `app/integrations/factory.py` |
| `ADMIN_DASHBOARD_ENABLED` | `app/main.py` |
| `ADMIN_USERNAME` | `app/services/admin_auth_service.py` |
| `ADMIN_PASSWORD` | `app/services/admin_auth_service.py` |
| `ADMIN_SESSION_SECRET` | `app/admin/deps.py`, `app/services/admin_auth_service.py`, `app/services/admin_table_service.py` |
| `ADMIN_TOTP_SECRET` | `app/services/admin_auth_service.py` |
| `ADMIN_SESSION_TTL_MINUTES` | `app/admin/router.py`, `app/services/admin_auth_service.py` |
| `CORS_ALLOWED_ORIGINS` | `app/core/config.py`: `cors_origins`; used by `app/main.py` and production validation |
| `FORWARDED_ALLOW_IPS` | Gunicorn and Uvicorn server configuration (process environment) |
| `RATE_LIMIT_ENABLED` | `app/main.py` |
| `RATE_LIMIT_MAX_REQUESTS` | `app/main.py` |
| `RATE_LIMIT_WINDOW_SECONDS` | `app/main.py` |
| `RATE_LIMIT_ANONYMOUS_MAX_REQUESTS` | `app/main.py` |
| `RATE_LIMIT_ANONYMOUS_WINDOW_SECONDS` | `app/main.py` |
| `RATE_LIMIT_ADMIN_MAX_REQUESTS` | `app/main.py` |
| `RATE_LIMIT_ADMIN_WINDOW_SECONDS` | `app/main.py` |
| `MAX_REQUEST_BODY_BYTES` | `app/main.py` |
| `WS_RATE_LIMIT_MAX_MESSAGES` | `app/routers/chat.py` |
| `WS_RATE_LIMIT_WINDOW_SECONDS` | `app/routers/chat.py` |
| `WS_MAX_FRAME_BYTES` | `app/routers/chat.py` |
| `DEFAULT_VAT_RATE` | `app/services/invoice_service.py` |
| `SERVICE_FEE_RATE` | `app/services/invoice_service.py` |
| `SERVICE_FEE_MIN_AMOUNT` | `app/services/invoice_service.py` |
| `SERVICE_FEE_MAX_AMOUNT` | `app/services/invoice_service.py` |
| `PLATFORM_COMMISSION_RATE` | `app/services/fulfillment_service.py` |
| `MAX_INVOICE_ITEMS` | `app/services/invoice_service.py` |
| `MAX_INVOICE_AMOUNT` | `app/services/invoice_service.py` |
| `MAX_ITEM_UNIT_PRICE` | `app/services/invoice_service.py` |
| `PAYMENT_EXPIRY_HOURS` | `app/services/invoice_service.py`, `app/services/payment_service.py` |
| `AUTO_APPROVE_HOURS` | `app/services/fulfillment_service.py` |
| `MIN_TOPUP_AMOUNT` | `app/services/payment_service.py` |
| `MAX_TOPUP_AMOUNT` | `app/services/payment_service.py` |
| `MIN_WITHDRAWAL_AMOUNT` | `app/services/withdrawal_service.py` |
| `MAX_WITHDRAWAL_AMOUNT` | `app/services/withdrawal_service.py` |
| `MAX_UPLOAD_BYTES` | `app/services/media_service.py` |
| `CHAT_IMAGE_MAX_UPLOAD_BYTES` | `app/routers/chat.py`, `app/services/chat_media_validation.py` |
| `CHAT_VIDEO_MAX_UPLOAD_BYTES` | `app/routers/chat.py`, `app/services/chat_media_validation.py` |
| `CHAT_AUDIO_MAX_UPLOAD_BYTES` | `app/routers/chat.py`, `app/services/chat_media_validation.py` |
| `CHAT_VIDEO_MAX_DURATION_SECONDS` | `app/routers/chat.py`, `app/services/chat_media_validation.py` |
| `CHAT_AUDIO_MAX_DURATION_SECONDS` | `app/routers/chat.py`, `app/services/chat_media_validation.py` |
| `JWT_PRIVATE_KEY` | `app/core/jwt.py` |
| `JWT_PUBLIC_KEY` | `app/core/jwt.py` |

## Verification and limits

- Static consumption audit: all 80 active template entries and two optional RSA entries
  map to application or server consumers; all application settings are documented.
- Focused settings, database pool and integration tests: 42 passed. New custom-value
  and invalid-value tests failed before implementation and passed after wiring it in.
- Full `uv run --locked pytest -p no:cacheprovider` with synthetic test credentials:
  613 passed, 196 skipped, one temporary-directory permission error during setup.
- Retry of the complete unit suite with a fresh writable `--basetemp`: 603 passed,
  one skipped, including the OpenAPI export test affected by that permission error.
- Pre-commit and pre-push gates: lint, formatting, type checks, structured-file validation,
  merge-marker and private-key checks passed.

PostgreSQL/Redis integration and native-decoder availability skips remain environment
limitations. Docker was not started, and no production database, secrets or provider
credentials were accessed. No live production capacity or provider success is inferred
from these tests. CI with disposable data services must cover the skipped integration tests.

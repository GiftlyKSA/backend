# Giftly high-level architecture

**Updated:** 2026-10-04 · **Audience:** product teams, investors and technical advisers.

Giftly connects customers ordering gifts with verified couriers in their city. This
document describes the current backend at a high level. It contains no credentials
or sensitive deployment details. See [project documentation](documentation.md) for
operations and [the current review](codebase_review.md) for known limitations.

## System overview

```text
Customer / courier application                 Admin browser
              |                                     |
       HTTPS + WebSocket                         HTTPS
              |                                     |
              +--------------+----------------------+
                             |
                     Giftly FastAPI backend
                    Routes -> domain services
                             |
                  +----------+-----------+
                  |                      |
             PostgreSQL                Redis
          Durable business data   Shared state, live events,
                                  background job queue
                                         |
                                Taskiq workers + scheduler

Backend / workers -> private S3 storage + signed media access
Backend / workers -> SMS, email and push provider adapters
Selected payment provider: Dhamen; production payments disabled
```

The mobile/web client is a separate application. The backend exposes HTTP APIs for
requests and history, plus WebSockets for live chat and order updates. Administration
is a server-rendered dashboard within the same backend.

## Main components

| Component | Purpose |
| --- | --- |
| Python / FastAPI | Validates requests and runs application services. Docker uses Python 3.13. |
| Domain services | Enforce roles, ownership, order transitions and financial rules. |
| PostgreSQL / SQLAlchemy | Store users, orders, invoices, wallets, messages and activity. |
| Redis | Shares OTP verification state, rate limits, revocation, coordination and live events; also backs the job queue. |
| Taskiq workers / scheduler | Run receipt delivery, notifications, expiry, cleanup and reconciliation. |
| Private S3 / CloudFront adapter | Store media privately and issue temporary authorized access links. |
| Jinja2 admin dashboard | Provides authorized record management and operational views. |
| Alembic / Docker / uv | Manage schema versions, deployment packaging and locked dependencies. |

## Application and data structure

The backend is a **modular monolith**: one application organized into business modules,
with separate API and background-worker processes. This keeps deployment and shared
transactions straightforward while preserving boundaries for future growth.

Routes and admin views call services; services call repositories; repositories access
database models. Provider adapters isolate storage and communication services.

```text
User (customer / courier / admin)
  +-- Wallet
  +-- Courier profile -> City
  +-- Customer occasions

Order -> Customer + Courier + City
  +-- Invoice -> Items + Promo applications
  +-- Conversation -> Messages -> Private attachments
  +-- Rating

Wallets <-> Ledger transactions / payment and withdrawal records
Committed data changes -> Activity records (system / admin / user)
```

PostgreSQL is the durable source of truth. Redis live events are delivery hints rather
than permanent history. Timestamps are stored in UTC; the admin displays UTC+3 and
clients localize API timestamps. Financial amounts use exact decimal arithmetic.

## Main flows

1. **Sign in:** the user requests a phone OTP, verifies it, and receives the appropriate
   registration or session response. The backend checks role and ownership on later requests.
2. **Order:** a customer creates an order. An eligible courier in the same city accepts
   it through an atomic claim. Committed changes reach the customer through the order stream.
3. **Chat and media:** participants retrieve paginated history or exchange live messages.
   Media uses scoped upload grants, validation and temporary private access links.
4. **Billing:** final courier-entered item prices and fees determine totals; no additional VAT is charged.
   Ledger and settlement services manage balances. Production payment processing is
   disabled until Dhamen integration and callback verification are validated.
5. **Background work:** scheduled jobs process maintenance and retryable notifications.
   Paid-invoice receipt delivery uses an email adapter and durable claims.

## Deployment and reliability

The deployment model uses an API service, worker processes, one scheduler, PostgreSQL
and Redis. The container API defaults to three server workers and an 80-second timeout.
Alembic upgrades tables in an already provisioned database before services start;
individual API workers do not create tables. A shared migration lock coordinates runners.
Compose uses a dedicated migration service followed by the dependent services.

API and worker replicas share database state, queues and coordination. Growth requires
connection budgets, bounded jobs, indexed queries and measured memory/CPU use. WebSocket
clients recover persisted state through HTTP after reconnecting. Pub/Sub alone does not
guarantee message delivery; chat delivery and other reliability follow-ups remain in
the [task tracker](tasks.md).

## Security boundaries and current limits

All client input is untrusted. Typed validation, server-side role/ownership checks,
parameterized database queries, rate limits and bounded uploads protect the application
boundary. JWT sessions, refresh rotation and production admin TOTP protect authentication.
Sensitive courier identity fields and messages use application encryption. Media remains
private. Production secrets must be injected through a protected runtime secret store.

Activity records track successful committed data changes rather than raw HTTP traffic.
Privileged admin CRUD over financial and audit records remains an explicitly accepted
risk. Existing controls do not establish that the system is free of vulnerabilities.

Live payment/vendor acceptance, deployed firewall settings, backup restoration and
production load capacity are **UNCONFIRMED** here. These require deployment evidence
and testing. The [current review](codebase_review.md) distinguishes confirmed defects,
accepted risks and checks still required before broader production use.

# Giftly backend — audit and task register

## Thirty-minute hosted checkout alignment — 2026-10-24

User specification: order -> invoice -> pay -> webhook-triggered authenticated status
verification -> atomic paid settlement. Reuse an active session younger than30 minutes;
after30 minutes close the unpaid provider checkout before replacement. Receipt mailing
and invoice/order due-date maintenance remain background jobs. User authorized commit,
push to master and CI verification. This supersedes the earlier45-minute request.

Implementation plan: (1) add failing provider-payload, session-age, reuse and worker
tests; (2) cap new hosted deadlines at30 minutes and legacy deadlines at creation+30,
apply that same deadline to API reads/reuse/reconciliation, set provider expiry30 and
send the customer name; (3) verify pending/paid/unknown cancellation paths, invoice
due-date independence, receipt jobs, static gates and full pytest; (4) independent
review, scoped commit/push and inspect CI. Keep existing public contracts, provider
references, wallet holds, production interlocks and idempotent settlement. No migration
or credential change. Rollback: revert the scoped source commit; never delete payment
history or blindly recreate attempts. Old provider references remain valid for recovery.

Verification: seven new cases first failed on the old30-minute behavior (provider
payload, legacy response deadline, local hosted lifetime and expired reuse). The
corrected source passed87 payment/webhook/worker/receipt cases; independent review
found no important defects and requested explicit29-minute reuse and expired-provider
failure boundaries. Those were added. The full local run completed1313 passed,
355 skipped,14 failures and2 setup errors, all in the same integration modules as
the previous run without disposable PostgreSQL/Redis. Seven failures and both errors
hit Windows Unix-socket NotImplementedError; four failures involve unavailable Redis
and cleanup; three OTP fixture KeyErrors follow failed PostgreSQL-backed registration.
No live/shared database was used for automated tests. CI supplies disposable services.
Both all-files hook stages passed before the final boundary-test additions; final
focused run passes89 checks, including both unknown-provider boundary cases. Final
hook results and remote CI status are recorded during delivery.

The CI workflow also contained literal escaped quotes in four python-version values;
these now use valid YAML string values3.15.1. Existing schedules remain unchanged:
reconciliation/receipt retries every5 minutes, expiry every10, and overdue unaccepted
orders at Saudi midnight. No exact worker completion latency is promised under backlog
or provider outage; API reads hide expired links immediately, and pay safely closes
an old checkout before replacement. Numeric12-digit Dhamen customer-ID mapping remains
a separate guide-alignment limitation; original UUID bindings were preserved rather
than silently changing existing payment identities. Production remains disabled.

## Dhamen deployed verification failure — 2026-10-24

| ID | Category / severity | Status | Evidence and impact | Minimal correction / verification |
| --- | --- | --- | --- | --- |
| PAY-STATUS-SHAPE | Integration correctness / high | Confirmed; fixed locally, NOT deployed | Deployed refresh returns503 PAYMENT_PROVIDER_UNAVAILABLE although Dhamen returns HTTP200 with a top-level one-element array. Adapter expects the guide v1.5 page30 customerPayments envelope and rejects the schema. Original reference/customer/amount match; status0, HTTPS checkout host allowed. | Normalize the array into the existing exactly-one-row envelope, keeping financial identity/amount/integer-status/URL checks. 78 focused checks pass against current Python3.15 source. No public contract or environment changes. |

Saved API/worker settings: ENVIRONMENT=development, PAYMENT_PROVIDER=dhamen,
PAYMENT_CHECKOUTS_ENABLED=true, DHAMEN_ENVIRONMENT=testing,
DHAMEN_TEST_BASE_URL=https://dhamendemo.elm.sa. Credentials match the supplied
sandbox keys in memory; no values recorded. Headers remain App-id, App-key, ClientId,
api-version=2. Scheduler does not make payment API requests. No supplier configuration
is needed for customer checkout without supplierId; subsequent payouts remain separate.
PAYMENTS_DISABLED is a different503 code caused by configuration interlocks, not this
schema error. CranL's app name “backend production” does not determine ENVIRONMENT.

Filtered one-day ingress initially showed35 refresh failures for one existing
attempt; worker warnings correlate that same attempt with outbound HTTP200. Owned
session GETs returned200; filtered invoice-pay/top-up/recovery queries showed no
other5xx paths in that window. Historical request IDs were absent from retained access
logs (UNCONFIRMED). Initial inspected deployment VTH5Zlhs7AECFjzB2gg4m was titled “Make
admin table actions visible”; actual running Git SHA remains UNCONFIRMED. Remote
execution probes failed to return output. Live checkout and worker HTTP200 prove
provider reachability for those requests, not comprehensive network/ingress safety.

The first PowerShell probe unwrapped the array and appeared flat; raw Python parsing
established the actual top-level array. Red/green array tests and live corrected-
adapter checks supersede that initial interpretation. Final correction accepts the
observed array and documented envelope only. No raw response, full checkout URL,
credentials, tokens or private customer details were retained in this record.

User explicitly approved one temporary customer, unpaid SAR100 sandbox checkout,
provider cancellation and customer deletion. Automatic review rejected configured-
admin login twice; no rejected command ran. The user signed in manually, then normal
API registration/testing and browser-admin cleanup used that approved session.

| Controlled request / check | Actual result |
| --- | --- |
| POST /api/wallets/topup | 201; same-key replay201, same intent; database confirms one intent |
| GET /api/payment-sessions/{intent_id} | 200 |
| GET /api/wallets/me/topup-session | 200 |
| POST /api/payment-sessions/{intent_id}/refresh | 503 PAYMENT_PROVIDER_UNAVAILABLE; request_id dhamen-status-probe-a166c51bcff44475815b0d753d8fc28c |
| Corrected local adapter | Original and temporary sandbox attempts verified PENDING; exact bindings match; temporary checkout cancellation confirmed |
| Ledger / wallet | Two balanced PENDING rows; wallet credit, holds and reservation remain zero |
| Temporary customer cleanup | Normal admin soft-delete. Database confirms DELETED, deletion timestamp, incremented auth_version, no active refresh tokens and USER_DELETE audit event |

No funds collected. Original customer's attempt only read. Financial history remains,
including local NEW/PENDING test intent/ledger because deployed code is unchanged;
no manual financial-state rewrite. Proof: ignored `.agent/dhamen-test-customer-deleted.png`.
Paid settlement, live concurrent writes and post-deployment API recovery UNCONFIRMED.
Existing focused tests cover active reuse, duplicate settlement, unknown creation,
timeout recovery, held reservations after failed cancellation and payment/cancel races.
The earlier45-minute payload request was tested during this investigation; the later
user-approved30-minute workflow above supersedes it.
No commit, push, deployment, Docker or mobile changes were performed for this fix.
During final checks a separate deployment Z_w9Eeb2oXlF2lk9HhROP (“Upgrade Python3.15
and dependencies”) appeared as succeeded. Its runtime-log call returned not_ready,
while the subsequent public GET /api/health returned200. These observations do not
establish the running revision or payment recovery; the payment patch is still local.
Concurrent external checkout restoration and Python-upgrade commit9865056 changed the
baseline during checks; final minimal patch was reapplied and verified on that source,
preserving its safe diagnostic logging. Earlier102-test counts refer to the prior
workspace state and do not substitute for the current78 focused results.

Latest verification (2026-10-24, Python3.15.1, UV_PROJECT_ENVIRONMENT selecting the
existing isolated test environment; uv0.12.16 invoked through uv tool run): the
current five focused payment modules pass71 checks, and backup export passes12.
Earlier78-check selection remains historical evidence, not this latest selection.
Both all-files hook stages pass, including full-project Ruff lint/format and strict
mypy. Diff whitespace checks pass. Logs are ignored
`.agent/payment-release-{focused,full,precommit,prepush}.txt`.

The initial40 collection errors were resolved with minimal Python-upgrade compatibility
corrections: parenthesized exception tuples with `as`, bounded generic annotations,
forward-reference/generator annotations and Ruff formatting. Backup send-failure and
timeout test scopes now explicitly select ASGI2.4, avoiding an invalid mock receive
loop under the upgraded Starlette. Backup authorization/export behavior is unchanged.
Concurrent lockfile, project configuration and courier-test edits were preserved;
the existing isolated environment was synchronized to that shared lockfile.

Concurrent dependency work subsequently committed8c71822, incorporating the upgrade
compatibility/backup-fixture changes. Remaining local source changes are the Dhamen
correction and chat/media exception parentheses; payment changes remain uncommitted.

Full pytest now completes:1302 passed,355 skipped,14 failed,2 errors. Failures/errors
are confined to integration modules: unavailable disposable PostgreSQL/Redis,
Windows Unix-socket NotImplementedError and dependent phone-proof fixture KeyErrors.
No database/Redis service or Docker was started. These results do not establish
integration success; rerun against authorized disposable services before release.
Current patch and prerequisite compatibility corrections were independently reviewed
with no critical/important findings. No commit/push/deployment was performed.
Post-release payment API recovery and paid settlement remain UNCONFIRMED.

## Courier cap follow-up review — 2026-10-21

Rechecked published `7347323` against the user's requirement to remove the fixed courier assignment cap. Its service and repository changes correctly remove the count query and cap-specific actor lock while retaining current courier eligibility, locked city checks, Redis/database order locks, conversation creation and post-commit broadcasts. No fixed courier assignment cap remains in that source; the separate customer creation limit is unchanged. The older primary checkout contains unrelated unpublished edits and was preserved.

Strengthened the existing regression for 0, 2, 3 and 8 prior assignments, exercising the real Redis-lock helper and checking eligibility, assignment identity/time and lock release. Added an existing-file PostgreSQL/Redis integration test accepting five orders for one verified courier. The cap implementation passed the complete 1,640-test suite against a fresh local database; after reconciling the independently published commit, 36 focused service/security/integration/OpenAPI checks passed again. The executable service/repository code is identical to the full-suite candidate; the service docstring differs only in wording. Ruff lint/format, strict mypy and both hook stages passed. Tests used supported Python 3.12 because the old temporary Python 3.13 installation is no longer usable; no new Python 3.13 claim is made. One existing Starlette/httpx deprecation warning remains.

Delivery limitation observed during this review: the public acceptance description still matches the older runtime, and deployment `_IPDV4nd994VIAaJp54D0` failed with a platform remote-command exit 1 and no build-log excerpt. Published code is not proof of deployment. No production data, account eligibility or payment settings were changed, and no authenticated live acceptance was attempted. The running backend must receive the published release before the removed cap can affect mobile users.

## Current review — 2026-10-16

**Reviewed source:** published `master` **`864530186b305fbc9035b4fb44265d498c21b00a`**.
**Scope:** backend HTTP/WebSocket authentication and authorization, media, financial
state transitions, worker delivery/retries, PostgreSQL/ORM queries, Redis caches,
resource bounds, shutdown and operational maintainability. This was a review, not
an implementation release. No application code, API contract or migration changed.

The earlier AUD-01–13 records, complete evidence and implementation history below
are retained without shortening. They remain closed. **AUD-14–29 are new findings
and proposals**; they do not reverse those fixes. A subsequent chat deployment
check added **AUD-30**, an upstream logging mitigation, with evidence below. The five postponed backend items
and previously accepted/excluded risks remain unchanged.

The authoritative reviewed checkout is
`C:/Users/PC/.codex/worktrees/remaining-api-performance/backend`. The primary workspace
contains older, unrelated unpublished application edits, which were preserved.
Source locations in this review refer to the authoritative commit above, rather
than claiming the old primary source has identical line numbers.

### Current priority index

Severity and urgency are separate engineering estimates out of 10, **not CVSS**.
Severity considers prerequisites and demonstrated impact; an application-level
gap does not establish a live exploit through the deployed ingress. Rows are
ordered by severity, then urgency/impact. Each ID opens its full task record.

| Finding | Task / purpose | Type | Severity /10 | Urgency /10 | Evidence / status |
| --- | --- | --- | --- | --- | --- |
| [AUD-14](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:55) | Align phone-enabled ADMIN API authentication with the MFA policy | security | **7** | **7** | Token issuance reproduced; policy decision required |
| [AUD-15](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:73) | Bound incomplete request-body reads and narrow throttle exemptions | security / scalability | **6** | **7** | Stalled-body retention reproduced; ingress exposure unverified |
| [AUD-30](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:332) | Stop upstream capture of legacy chat token queries | security / logging | **6** | **7** | Live non-secret canary retained in CranL request path; provider/configuration blocker |
| [AUD-16](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:90) | Rate-limit WebSocket handshakes before repeated database authorization | security / perf | **5** | **5** | Rejected-handshake query amplification reproduced |
| [AUD-17](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:126) | Prevent scheduled delivery drainers from consuming all worker capacity | scalability / reliability | **5** | **4** | Source-derived outage/backlog risk; no production load claim |
| [AUD-19](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:143) | Add bounded SQL execution and lock-wait budgets | security / perf | **4** | **5** | Local driver/server defaults inspected; deployed limits unknown |
| [AUD-18](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:107) | Fence reminder delivery against current ownership and role | security / correctness | **4** | **4** | Committed owner-transfer race reproduced on PostgreSQL |
| [AUD-20](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:160) | Prevent permanently failing receipts from starving newer receipts | reliability | **4** | **4** | 101-invoice, two-sweep starvation reproduced |
| [AUD-21](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:177) | Release admin invoice PDF connections before rendering | perf / scalability | **4** | **4** | Single-connection pool exhaustion reproduced |
| [AUD-22](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:194) | Correct prepared-cache assumptions for the actual PostgreSQL pooler | reliability / opt | **4** | **3** | Cache mismatch confirmed; pooler failure conditional |
| [AUD-24](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:247) | Separate cache expiry from capacity-eviction order | perf / opt | **3** | **4** | Mixed-TTL immediate self-eviction reproduced in Redis |
| [AUD-23](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:211) | Settle valid zero-payout invoices without a zero ledger leg | correctness | **3** | **3** | Exact-Decimal zero-leg rejection reproduced |
| [AUD-25](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:264) | Bound the two participant branches of the inbox query before merging | SQL / perf | **3** | **3** | 100k-row synthetic EXPLAIN comparison |
| [AUD-26](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:281) | Avoid scanning closed order history for the customer active-order quota | SQL / opt | **3** | **3** | 100k-row synthetic EXPLAIN; generic-plan caveat reproduced; courier assignment cap removed 2026-10-21 |
| [AUD-27](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:228) | Close every shared resource even when an earlier close fails | reliability / quality | **3** | **3** | Injected first-close failure skips later cleanup |
| [AUD-28](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:299) | Add bounded cache and worker-pressure telemetry | scalability / opt | **2** | **2** | Optional implementation proposal, not a confirmed vulnerability |
| [AUD-29](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:315) | Make the field-key rotation inventory and CLI claims accurate | quality | **2** | **2** | Inventory/documentation inconsistency; existing runbook mitigation |
| [AUD-31](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:381) | Translate registration uniqueness violations to controlled conflicts | quality / reliability | **2** | **2** | Source-confirmed error-contract defect; reproduction pending |

### Security and authorization

<a id="aud-14-current"></a>

#### AUD-14 — Phone-enabled ADMIN accounts can obtain public OTP API tokens without dashboard MFA

| Field | Full detail |
| --- | --- |
| Purpose / why important | Make privileged API authentication consistent with the intended production admin MFA boundary. The dashboard password/TOTP checks do not automatically protect role-based admin HTTP APIs. |
| Type / severity / urgency | Security; **7/10** severity; **7/10** urgency. Confirmed authentication behavior with an explicit prerequisite; whether public SMS-only admin API login is intentional requires a policy decision. |
| Status / created | Open — policy decision required; 2026-10-16. No authentication behavior changed during this audit. |
| Code locations | `app/services/auth_service.py:111–129`; `app/services/admin_auth_service.py:100–101`; `app/routers/admin_api.py:41,82,114,130,148`; `app/services/admin_service.py:405–430`; `app/repositories/user_repository.py:190–212`. |
| Current workflow | Public OTP verification authenticates the phone, loads the existing user role and issues access/refresh credentials. The role may be ADMIN. Admin API dependencies accept an eligible ADMIN JWT. Dashboard password/TOTP verification runs through a separate session-login service. |
| Trigger / prerequisites | An ACTIVE ADMIN must have a routable Saudi phone and the caller must obtain its valid SMS OTP. The default dashboard identity uses a reserved `admin:` phone, so it is not itself reachable through this phone flow; authorized CRUD can create/change a phone-enabled ADMIN. This is not an unauthenticated role-escalation claim. |
| Evidence | An in-memory service probe issued an ADMIN-role access token and persisted one refresh token after successful OTP verification, without invoking password/TOTP verification. The source permits phone-enabled ADMIN records. No production account or SMS provider was used. |
| Impact if unresolved | A phone-enabled administrator has a second privileged authentication path protected by SMS possession alone, bypassing the dashboard's password/TOTP policy for API operations. Compromise of that factor has greater impact than a customer/courier login. It does not create a dashboard session cookie. |
| Proposed minimal solution | Decide whether public mobile authentication is customer/courier only. If so, deny ADMIN token issuance and refresh on that path, invalidate existing ADMIN access credentials and refresh families and preserve dashboard CRUD. If ADMIN API access is required, require a separately defined MFA-bound authentication policy instead of assuming dashboard MFA applies to every JWT. |
| Expected system impact | Removes an unintended privileged entry path while preserving ordinary mobile authentication. Existing phone-based admin API clients would require an explicit migration; do not silently deploy a compatibility break. |
| Verification / rollback | Test ADMIN OTP and refresh rejection, normal customer/courier flows, inactive-account handling and existing dashboard TOTP. If an approved admin API flow is retained, test its MFA binding and revocation. No schema migration is inherently needed; token revocation/rollout must be deliberate and documented. |

<a id="aud-15-current"></a>

#### AUD-15 — Incomplete HTTP bodies can retain memory and tasks indefinitely

| Field | Full detail |
| --- | --- |
| Purpose / why important | Bound the lifetime of untrusted uploads before route validation; a byte-size limit alone does not bound how long the bytes occupy memory. |
| Type / severity / urgency | Security/resource exhaustion and scalability; **6/10** severity; **7/10** urgency. Application behavior confirmed; effective production exposure depends on ingress buffering/timeouts, which were not verified. |
| Status / created | Open; 2026-10-16. |
| Code locations | `app/core/middleware.py:105–117`; `app/main.py:305–306`; installed Uvicorn worker heartbeat/keep-alive behavior was inspected against the locked runtime. |
| Current workflow | Body-size middleware buffers incoming chunks up to the configured ceiling and repeatedly awaits `receive()` until the body finishes. There is no idle/total body deadline. HTTP throttling exempts every OPTIONS request and any path starting with `/api/health`, including non-health routes with that prefix. |
| Trigger / evidence | A synthetic ASGI request supplied 1,000,000 bytes with `more_body=True` and then stopped. The request task remained pending, retained the bounded buffer and never reached its handler. An attacker can repeat this; exempt requests avoid the normal request-count limit. No real network flood was performed. |
| Impact if unresolved | Many incomplete requests can consume memory, tasks and connections even though each request remains below the size ceiling. Worker timeout 80 seconds is a process-health setting for the async worker, not proof that each body read is cancelled after 80 seconds. Keep-alive timeout applies to a different phase. |
| Proposed minimal solution | Add explicit idle and total body-read budgets with cancellation-safe cleanup and a stable timeout response. Exempt only the exact intended health method/path pairs; handle ordinary CORS preflight without buffering arbitrary bodies. Consider a bounded request-admission limit coordinated with verified ingress limits. |
| Expected system impact | Stalled clients release resources predictably. Configure a reasonable deadline for normal mobile API request bodies rather than imposing an unexplained low limit; preserve existing body-size responses and valid requests. |
| Verification / rollback | Test no-body, fragmented, oversized, stalled and continuous-trickle bodies; disconnect/cancellation cleanup; exact health routes and preflight. Assert the route is not called after timeout. Deployment ingress limits remain a separate unverified dependency. No database migration is needed. |

<a id="aud-16-current"></a>

#### AUD-16 — Repeated rejected WebSocket handshakes perform fresh database work without a handshake budget

| Field | Full detail |
| --- | --- |
| Purpose / why important | Protect database capacity before admitting or rejecting live connections, while retaining fresh account and conversation authorization. |
| Type / severity / urgency | Security/resource exhaustion and performance; **5/10** severity; **5/10** urgency. |
| Status / created | Open; 2026-10-16. |
| Code locations | `app/routers/chat.py:388–413,507–516`; `app/routers/order_events.py:49–52`; HTTP-only request middleware and existing shared WebSocket connection leases. |
| Current workflow | Chat handshakes validate credentials, then read account/conversation state before acquiring the active-connection lease. Order-event handshakes similarly read the authorized snapshot before the connection cap. Frame throttling applies only after acceptance; HTTP rate limiting does not cover the WebSocket scope. |
| Trigger / evidence | Twenty synthetic handshakes against an already-full connection lease caused twenty mocked account-repository calls and twenty mocked conversation-authorization service calls before all twenty were rejected. The actual SQL authorization path was inspected in source; this probe did not measure forty executed SQL statements. The per-account/global active-connection ceilings bound admitted concurrent sockets, not repeated rejected attempts or reconnect churn. |
| Impact if unresolved | A credentialed client can repeatedly consume authorization queries without holding an accepted socket. Nonmember attempts also reach database checks. This can increase pool contention for ordinary HTTP traffic; no production throughput threshold was established. |
| Proposed minimal solution | Add a bounded shared handshake budget by trusted client address and, after bounded token validation, account identity before repeated SQL work. Preserve token validity, membership, account restrictions and the existing active-connection caps. Apply an initial-auth deadline to every chat authentication transport, including the legacy flow. |
| Expected system impact | Limits reconnect/denial abuse across API instances without caching authorization or weakening conversation privacy. Define deterministic limits and rejection/close behavior compatible with existing clients. |
| Verification / rollback | Test distributed accepted/rejected attempts, invalid/expired/revoked tokens, nonmembers, reconnect after token refresh, budget recovery and dependency failures. Assert throttled attempts stop before SQL. Preserve current payloads and subprotocol selection; no message-retry or mobile flag changes belong in this task. No migration expected. |

<a id="aud-18-current"></a>

#### AUD-18 — A claimed reminder can still select the former owner's device after ownership changes

| Field | Full detail |
| --- | --- |
| Purpose / why important | Recheck both the current owner and eligible role at the final delivery boundary, not only the occasion's schedule version. |
| Type / severity / urgency | Security/privacy and correctness; **4/10** severity; **4/10** urgency. |
| Status / created | Open — committed PostgreSQL reproduction; 2026-10-16. |
| Code locations | `app/repositories/notification_reminder_repository.py:156–227`; `app/migrations/versions/0033_annual_occasions.py:38–40`; `app/repositories/device_token_repository.py` token-page lookup. |
| Current workflow | A claim captures the current user ID, schedule version and lease. Delivery rechecks the live schedule/lease and current user's ACTIVE status, but does not require `Occasion.user_id == claim.user_id` or recheck CUSTOMER role. Device selection then uses the captured owner ID. The schedule-version trigger does not advance solely for a user-ID change. |
| Trigger / evidence | In a disposable database, customer A's due reminder was claimed and committed; a separate transaction transferred the occasion to customer B; `token_page(claim)` still returned A's device and the claim remained valid. All synthetic fixtures were removed; no push was sent. A role change between claim and send is a related missing final predicate. |
| Impact if unresolved | The former owner can receive a neutral reminder containing an occasion reference they no longer own, while the new owner's delivery may be suppressed/completed under the stale claim. REST ownership checks still protect opening the destination. The race requires an authorized ownership/role change; it is not a public transfer endpoint or cross-account read bypass. |
| Proposed minimal solution | Bind the final delivery query to the captured/current owner and current CUSTOMER eligibility. Add a forward migration that treats owner changes as reminder-version changes and resets the owned schedule/outbox consistently, preserving existing lease fencing. Do not remove unrestricted admin CRUD. |
| Expected system impact | Stale claims become ineligible instead of selecting the old device. A new owner gets a newly defined current schedule rather than inheriting a completed old-owner claim. |
| Verification / rollback | Test transfer, role/status change, schedule edit, disable/delete, stale lease and retry interleavings. Confirm only the new valid claim may progress; retain duplicate-safe event keys. Review trigger backfill/locking and downgrade behavior on disposable PostgreSQL; do not reset delivered schedules indiscriminately. |

### Reliability, financial correctness and resource lifetimes

<a id="aud-17-current"></a>

#### AUD-17 — Scheduled delivery drainers can overrun their interval and occupy both worker slots

| Field | Full detail |
| --- | --- |
| Purpose / why important | Keep notification/email outages and delivery backlogs from starving expiry, settlement and other maintenance on the shared worker queue. |
| Type / severity / urgency | Scalability and reliability; **5/10** severity; **4/10** urgency. Source-derived capacity risk under backlog/slow-provider conditions; production queue growth was not measured. |
| Status / created | Open; 2026-10-16. |
| Code locations | `app/workers/runtime.py:25–46`; `app/workers/receipts.py:28,51–75`; `app/services/receipt_service.py:22,75–78`; `app/services/order_notification_service.py:23,102`; `app/services/chat_notification_service.py:23,89`; `app/workers/chat_notifications.py:7`; `app/workers/occasion_reminders.py:7`; `app/workers/broker.py`. |
| Current workflow | The worker permits two asynchronous tasks and two prefetched messages. Scheduler jobs enqueue drainers repeatedly. Receipt draining processes up to 100 network sends sequentially every five minutes. Push drainers process bounded claims, but their cumulative per-send deadlines can exceed their one-minute schedule. A durable per-record lease prevents some duplicate sends; it does not coalesce repeated drainer jobs in the queue. |
| Trigger / evidence | With 100 pending receipts and the default email client's 10-second timeout, one sweep can spend about 1,000 seconds on sends alone versus a 300-second interval; the outer per-send allowance is 15 seconds. A 20-claim push drainer with a 20-second bound can likewise exceed a minute. These are source-derived worst-case service-time estimates, not measured ordinary latency. |
| Impact if unresolved | Under sustained backlog/outage, scheduled work can arrive faster than two consumers can finish it. Queue lag and memory/storage use can grow; financial/order maintenance waits behind delivery drainers. This is separate from AUD-20's selection fairness problem. |
| Proposed minimal solution | Give each drainer a short overall wall-clock budget and resumable durable cursor, coalesce concurrent/queued instances with bounded shared ownership and retry backoff, and preserve per-record fencing. If measurements justify it, isolate critical maintenance from delivery queues with explicit connection/CPU budgets. Do not simply raise concurrency or create unbounded per-item tasks. |
| Expected system impact | Predictable worker occupancy and better fairness during provider failures; reliable delivery remains retryable. Shared leases must expire/renew safely and cannot become permanent locks after crashes. |
| Verification / rollback | Use accelerated stalled-provider fakes and two worker slots to test repeated scheduler ticks, queue coalescing, crash recovery, deadlines, duplicate handling and progress of critical maintenance. No real notifications needed. Deployment queue topology changes require a drain/rollback plan; a database migration depends on the selected durable checkpoint fields. |

<a id="aud-19-current"></a>

#### AUD-19 — SQL execution and lock waits have no application-owned deadline

| Field | Full detail |
| --- | --- |
| Purpose / why important | Bound the time a slow query or row-lock wait can occupy the database pool and request/task resources. |
| Type / severity / urgency | Security/resource exhaustion and performance; **4/10** severity; **5/10** urgency. Application configuration gap confirmed; deployed role/server defaults are UNCONFIRMED. |
| Status / created | Open — deployment-dependent protection gap; 2026-10-16. |
| Code locations | `app/core/db.py:48–65`; request/session transaction scopes; `app/core/config.py` database pool settings. |
| Current workflow | Engine creation sets pool checkout timeout and asyncpg statement-cache behavior. It supplies no driver command timeout or statement/lock execution budget. Pool timeout limits waiting for a connection, not SQL after obtaining it. Async worker process-health timeouts are not per-query deadlines. |
| Evidence | On the owned native PostgreSQL database using the actual engine builder: `statement_timeout=0`, `lock_timeout=0`, driver `command_timeout=None`. Thus the local default execution/lock waits are unbounded. This does not prove the production database role has the same settings. |
| Impact if unresolved | A blocked row, costly query or unhealthy dependency can retain connections until external cancellation. Enough blocked calls exhaust the pool and amplify latency across unrelated endpoints. Financial operations must fail/rollback safely rather than be blindly retried after an uncertain timeout. |
| Proposed minimal solution | Define conservative typed budgets for ordinary commands, lock waits and transaction duration, with separately justified maintenance/export budgets. Use transaction-local settings compatible with the actual pooler, cancellation-safe rollback and stable safe error envelopes. Keep existing ownership and locking; do not cache authorization/balances to hide contention. |
| Expected system impact | Overload fails predictably and releases connections; legitimate long operations need explicit treatment. Measure realistic queries before choosing values and avoid imposing a universal tiny deadline. |
| Verification / rollback | Use disposable PostgreSQL blocking-lock and slow-query fixtures; assert bounded cancellation, rollback, reusable connections, no partial ledger writes and appropriate errors. Check deployed role/pooler overrides separately. Usually configuration/code only; document rollout and restoration of prior limits. |

<a id="aud-20-current"></a>

#### AUD-20 — Permanently failing paid-invoice receipts starve newer eligible receipts

| Field | Full detail |
| --- | --- |
| Purpose / why important | Keep receipt retries fair so an invalid address or permanently failing old document cannot block unrelated customers' receipts. |
| Type / severity / urgency | Reliability; **4/10** severity; **4/10** urgency. |
| Status / created | Open — native PostgreSQL reproduction; 2026-10-16. |
| Code locations | `app/repositories/invoice_repository.py:287–307`; `app/workers/receipts.py:28,51–60,75`; `app/services/receipt_service.py:22,47–78`. |
| Current workflow | Every sweep selects the oldest 100 eligible PAID invoices without a sent timestamp. Claims expire after 60 seconds. A failed send is logged, but there is no durable attempt count, next-attempt schedule or exhausted state for receipt selection. Five minutes later those same rows are eligible again. |
| Trigger / evidence | Created 101 synthetic paid invoices in an owned disposable database. An injected email fake permanently failed the oldest 100 and would accept the newest one. Across two sweeps with expired claims, the old rows received **200 failed attempts** and the newest eligible invoice received **zero attempts**. The initial fixture run violated unique email constraints; unique synthetic addresses corrected the harness, not application code. No network email was sent. |
| Impact if unresolved | New paid customers can wait indefinitely for receipts behind a sufficient set of permanently failing old rows, despite the sweeper continuing to run. Logs alone do not restore fairness. Claim/idempotency protections still reduce concurrent duplicate effects. |
| Proposed minimal solution | Persist bounded attempt/backoff scheduling and a visible exhausted/manual-retry state, order fairly among due attempts, and distinguish safe permanent failures from retryable ones. Preserve stable provider idempotency and claim-token completion checks. |
| Expected system impact | New receipts progress while transient failures retry at controlled intervals. Administrators can identify exhausted receipts rather than endlessly consuming capacity. |
| Verification / rollback | Test more than one batch, poison rows, transient recovery, concurrent sweepers, crash after provider acceptance, stale claim completion and manual requeue. A forward migration is likely for retry state/indexes; preserve existing sent timestamps and never mass-resend previously accepted receipts during rollout/downgrade. |

<a id="aud-21-current"></a>

#### AUD-21 — Admin invoice PDF rendering still holds the read transaction and pool connection

| Field | Full detail |
| --- | --- |
| Purpose / why important | Apply the already-proven mobile PDF connection-release pattern to the separate server-rendered admin download path. |
| Type / severity / urgency | Performance and scalability; **4/10** severity; **4/10** urgency. |
| Status / created | Open — native one-connection reproduction; 2026-10-16. This is a different path from closed AUD-04. |
| Code locations | `app/services/admin_service.py:176–182`; `app/admin/deps.py:78–90`; `app/services/admin_auth_service.py:183–187`; existing mobile invoice PDF snapshot/render infrastructure. |
| Current workflow | Admin service reads invoice/items and passes live ORM objects into `to_thread(render_invoice_pdf, ...)`. The dependency commits after the service returns, retaining the database transaction during CPU rendering. If the admin session was renewed in the same request, its update lock may also remain open. |
| Trigger / evidence | With the actual engine configured for one connection, a synthetic authorized read and a blocked render callback prevented a second borrower from obtaining the connection until rendering completed. No production PDF load test or private invoice was used. |
| Impact if unresolved | A few slow concurrent admin PDFs can occupy the shared pool and delay API/admin calls. Sending ORM state across a thread also makes resource ownership less clear. This does not establish an authorization bypass. |
| Proposed minimal solution | Reuse the immutable scalar invoice-PDF snapshot, complete fresh authorization/read/session-renewal work before awaiting cache/render, and reuse bounded rendering/cache admission as appropriate. Do not detach partially loaded ORM graphs or skip fresh authorization. |
| Expected system impact | Frees database capacity during CPU work while preserving PDF contents, privacy and existing URLs. The mobile AUD-04 fix remains valid; no public API change is required. |
| Verification / rollback | Block rendering and prove a second pool borrower succeeds; test session renewal/revocation, foreign invoice rejection, PDF content parity, private headers, cancellation and concurrency limits. No schema migration expected; rollback should restore only this admin path. |

<a id="aud-22-current"></a>

#### AUD-22 — PgBouncer compatibility comments disable a different prepared-statement cache

| Field | Full detail |
| --- | --- |
| Purpose / why important | Ensure the deployed connection-pooling mode matches SQLAlchemy/asyncpg prepared-statement behavior rather than relying on an inaccurate configuration comment. |
| Type / severity / urgency | Reliability and optimization; **4/10** severity; **3/10** urgency. Confirmed configuration mismatch; runtime failure is conditional on pooler version/settings/topology. |
| Status / created | Open — deployment-dependent risk; 2026-10-16. No live PgBouncer failure reproduced. |
| Code locations | `app/core/db.py:1–7,48–65`; locked SQLAlchemy asyncpg dialect connection wrapper and installed driver. |
| Current workflow / evidence | `connect_args={"statement_cache_size": 0}` disables asyncpg's driver cache. The SQLAlchemy adapted connection still has its own prepared-statement cache, observed enabled with capacity **100** in a real native connection. The module/comment describes the setting as sufficient for transaction-mode PgBouncer. It is not the dialect's `prepared_statement_cache_size` option. |
| Impact if unresolved | An incompatible transaction-pooler configuration can produce prepared-statement collisions/missing-statement errors and intermittent request failures. A correctly configured modern pooler may support the current behavior; direct PostgreSQL may benefit from caching. Do not describe a conditional integration risk as a demonstrated production outage. |
| Proposed minimal solution | Establish the actual pooler mode/version/prepared-statement support, configure the correct dialect option where needed, and correct the comments/runbook. Setting cache size to zero alone does not eliminate every `prepare()` call. Evaluate name handling and connection cleanup against the official dialect guidance, not a blanket switch to unpooled connections. |
| Expected system impact | Predictable connection behavior without gratuitously removing useful direct-database pooling/caching. Changing names/cache/pool strategy can affect throughput and connection budgets and needs topology-specific justification. |
| Verification / rollback | Test repeated prepared queries through the actual disposable pooler configuration, reconnections, concurrent clients and migrations. No migration expected. Keep a documented direct-database/pooler rollback configuration; do not alter production experimentally. |
| Primary references | Checked 2026-10-16: [SQLAlchemy prepared-statement cache](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#prepared-statement-cache) and [prepared-statement names/PgBouncer](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#prepared-statement-name). |

<a id="aud-23-current"></a>

#### AUD-23 — Valid rounding or commission settings can create an invalid zero ledger leg

| Field | Full detail |
| --- | --- |
| Purpose / why important | Preserve double-entry settlement for every amount accepted by pricing/configuration, including a courier payout that legitimately rounds to zero. |
| Type / severity / urgency | Financial correctness; **3/10** severity; **3/10** urgency. Requires a non-default commission/rounding edge case. |
| Status / created | Open — pure exact-Decimal reproduction; 2026-10-16. |
| Code locations | `app/core/config.py:421–424`; `app/core/pricing.py:125–150`; `app/services/money_service.py:31–44,419–460`. |
| Current workflow | Pricing permits a commission rate up to 1.00. Settlement unconditionally creates the courier-credit leg even when its quantized payout is zero. The shared ledger validator correctly rejects zero-valued legs. |
| Trigger / evidence | An accepted tiny invoice with item amount `0.01`, courier fee `0.00`, service fee `5.00` and commission rate `0.50` produces commission `0.01`, courier payout `0.00` and revenue `5.01`; settlement raises `LedgerImbalanceError` on the zero leg. A permitted 100% commission produces the same class of issue more broadly. No floats or actual money were used. |
| Impact if unresolved | Customer approval, auto-approval or a courier-favoring dispute settlement can fail for otherwise valid pricing, leaving the order/escrow unresolved. The transaction rolls back; this is not evidence of lost money or an unbalanced committed ledger. |
| Proposed minimal solution | Omit a zero courier leg while preserving the balanced nonzero escrow/revenue legs, following existing split-leg patterns. Keep the ledger's prohibition on zero legs; do not weaken invariant checks or rewrite historical entries. |
| Expected system impact | Supported zero-payout cases settle deterministically without changing normal payout arithmetic or configured fee policy. |
| Verification / rollback | Test tiny rounding, 100% commission, ordinary default amounts, approval/auto-approval/dispute paths, idempotent retries and exact sum-to-zero. No schema migration expected. A policy restriction on commission would be a separate product decision, not a substitute for silently rejecting accepted amounts. |

<a id="aud-27-current"></a>

#### AUD-27 — A single provider-close failure skips every remaining shared-resource close

| Field | Full detail |
| --- | --- |
| Purpose / why important | Make graceful shutdown release independent resources even when one external adapter's cleanup fails. |
| Type / severity / urgency | Reliability and code quality; **3/10** severity; **3/10** urgency. |
| Status / created | Open — injected failure reproduction; 2026-10-16. |
| Code locations | `app/main.py:54–72`, `_close_shared_resources`. |
| Current workflow | Gateway/email/SMS/push/storage clients are closed sequentially, then both Redis pools and the SQL engine. There is no per-resource `finally`/independent cleanup guard; an exception exits the function immediately. |
| Trigger / evidence | A gateway fake raised during `aclose()`. The observed close-call list contained only `gateway`; later provider, subscription Redis, ordinary Redis and engine cleanup were skipped. No real adapter was deliberately failed. |
| Impact if unresolved | Shutdown can omit connection cleanup, leave unfinished provider work until process termination, and obscure later cleanup failures. Process exit ultimately releases OS resources, so this is a graceful-lifecycle defect rather than proof of a permanent production memory leak. |
| Proposed minimal solution | Use independently guarded, bounded cleanup or an appropriately registered async exit stack. Attempt every close, retain useful failure visibility without secrets, and preserve cancellation/original-error semantics rather than swallowing all failures. |
| Expected system impact | More predictable deploy/restart behavior without changing request contracts or normal pool ownership. Bound total shutdown time and ensure resources close once. |
| Verification / rollback | Inject a failure/hang at each resource, check subsequent cleanup, cancellation, aggregate logging and deadlines; test normal shutdown. No migration/dependency change should be necessary. |

### PostgreSQL, cache efficiency and scalability proposals

<a id="aud-24-current"></a>

#### AUD-24 — Mixed cache TTLs cause new short-lived entries to evict themselves

| Field | Full detail |
| --- | --- |
| Purpose / why important | Make the bounded shared cache admit useful order/catalog results when long-lived occasion pages already occupy its capacity. |
| Type / severity / urgency | Performance and optimization; **3/10** severity; **4/10** urgency. Confirmed efficiency bug, not stale authorization or cross-account data exposure. |
| Status / created | Open — Redis reproduction; 2026-10-16. |
| Code locations | `app/services/result_cache.py:23–36,120`, `_STORE` Lua expiry/capacity pruning (`ZPOPMIN`); `tests/integration/test_read_cache_versions.py:49–64`; catalog/occasion/order cache call sites. |
| Current workflow | A shared sorted set is ordered by absolute expiry and is used both to remove expired entries and to evict excess capacity. Long occasion pages last 3,600 seconds; order pages last 30 seconds and catalogs 300 seconds. Invalidated old-version pages remain until expiry. |
| Trigger / evidence | If the 256-entry budget is full of occasion pages with more than 30 seconds left, a new order page has the earliest expiry and is the item removed immediately after its insertion. A real Redis reproduction of the identical Lua logic at capacity two retained both long-lived entries and removed the new short-lived entry; count/TTL remained bounded, explaining why existing capacity assertions pass. Catalog entries can suffer similarly. |
| Impact if unresolved | Orders/catalogs repeatedly miss under mixed workloads even after a successful store, increasing SQL, serialization and Redis coordination work. Longer-lived invalidated pages can crowd out useful current pages. It does not return incorrect responses or authorize writes from stale data. |
| Proposed minimal solution | Separate expiry pruning from a bounded insertion/eviction order, for example atomic expiry and FIFO indexes with consistent removal. Keep the shared payload count/byte limits and bound both indexes; use Redis time and expire metadata. Alternatively use explicitly budgeted domain quotas whose combined memory remains bounded. Merely pre-evicting once does not solve long-TTL dominance. |
| Expected system impact | New short-lived entries survive admission and can produce hits. Additional metadata/write cost must remain small; do not introduce unbounded LRU lists or per-hit writes without measurement. |
| Verification / rollback | Test actual capacity 256, long→short and short→long inserts, hits after store, expiry, invalidation/version churn, concurrent Lua operations, corrupt entries and Redis outage fallback. Preserve fresh auth, all filters, DTO validation and existing invalidation. No database migration expected; coordinate or migrate Redis bookkeeping for rollout. If namespaces are versioned, explicitly retire the old namespace and account for mixed-version replicas so aggregate payload/byte budgets are not silently doubled. |

<a id="aud-25-current"></a>

#### AUD-25 — Inbox participant OR filtering scans/sorts growing history before returning a page

| Field | Full detail |
| --- | --- |
| Purpose / why important | Use the existing participant/activity indexes to bound inbox work by page size while preserving every authorized historical membership. |
| Type / severity / urgency | SQL/performance; **3/10** severity; **3/10** urgency. Measured synthetic optimization, not a claim that every small inbox is slow. |
| Status / created | Proposed measured query improvement; 2026-10-16. |
| Code locations | `app/repositories/chat_repository.py:125–143`; existing customer/activity and courier/activity indexes; inbox cursor construction and response assembly. |
| Current workflow | The query combines `customer_id = owner OR courier_id = owner`, then orders by activity descending and ID descending and limits the final result. Separate useful ordered indexes exist, but the OR form can choose a scan plus sort over all matching history. A final LIMIT alone does not bound rows visited. |
| Evidence / assumptions | PostgreSQL 16 temporary tables cloned with the deployed indexes, 100,000 conversations and one synthetic owner on 40,000 rows (20,000 per participant branch), full row projection and 100-character previews: four-run local median **14.737 ms** for the original versus **0.023 ms** for a bounded ordered UNION alternative. Original plan scanned 100k, matched 40k and sorted top-N; alternative merged ordered branches reading about 25+1 rows. Local/temp/warm-cache SQL timing excludes network, auth, decryption and API serialization. |
| Impact if unresolved | Heavy-history inbox reads do work proportional to matching history and occupy CPU/buffers longer than necessary. Ordinary small accounts may see little benefit. No unbounded Python N² loop was demonstrated here. |
| Proposed minimal solution | Apply identical cursor predicates and bounded order/limit inside each participant branch, UNION ALL, then apply the final deterministic order/limit. Exclude duplicate self-membership from one branch. Do not filter solely by the current role and accidentally hide older legitimate membership after a role change. Existing indexes appear sufficient. |
| Expected system impact | Smaller row visitation/sort work for heavy histories without caching authorization or changing message/inbox payloads. No new index write/storage cost is currently justified. |
| Verification / rollback | Compare result equality and multiple cursor pages, activity ties, self-membership, role history, foreign accounts and empty pages; inspect literal/prepared/generic plans on representative data. Query-only rollback; no migration proposed at present. |

<a id="aud-26-current"></a>

#### AUD-26 — Customer active-order quota count scans closed order history

| Field | Full detail |
| --- | --- |
| Purpose / why important | Keep the customer's authoritative five-active-order creation check efficient as closed history grows. Courier acceptance no longer has an active-assignment quota. Never replace the customer write check with stale cached eligibility. |
| Type / severity / urgency | SQL/optimization; **3/10** severity; **3/10** urgency. |
| Status / created | Open measured query/index improvement for the customer order-creation quota; 2026-10-16. The three-active-assignment courier cap and its count query were removed on 2026-10-21 at the user's request. |
| Code locations | `app/repositories/order_repository.py` (`count_customer_active`); `app/services/order_service.py` (`create_order`, customer limit); existing customer order indexes. Courier acceptance no longer counts assigned orders. |
| Current workflow | Customer order creation counts non-terminal orders while holding the customer quota lock and rejects creation at five. The count can scan closed history through the customer order access path. Courier acceptance relies on current account/profile eligibility, same-city authorization, the per-order Redis lock, and the database order row lock; it has no active-assignment count query. |
| Evidence / assumptions | PostgreSQL 16 temporary clone with 100k orders; one customer had 50k history rows, only two active. Original median **4.4455 ms**, filtering 49,998 history rows and about 905 local buffer hits. A partial active-owner index with trusted fixed-status predicates gave **0.0115 ms**, two local hits and a 16,384-byte index in this fixture. These are synthetic local query timings, not end-to-end production measurements. |
| Critical plan caveat | A forced generic plan with status parameters did **not** use the partial index (**7.458 ms**). A tested owner/status composite index used 729,088 bytes and did not improve that generic plan (**7.4215 ms**). Keeping owner ID parameterized while expressing only the fixed internal status set as trusted SQL literals enabled the partial path under generic planning (**0.0135 ms**). A migration alone is therefore not a demonstrated fix. |
| Impact if unresolved | Customer order-creation latency and database buffer work can grow with closed customer history while the quota coordination lock is held. |
| Proposed minimal solution | Optimize only the customer active-order count with a narrow partial index after confirming the actual prepared/generic query plans. Compile only trusted internal enum constants; never interpolate request values. Preserve the authoritative transaction/locking/quota behavior. Do not add a courier index for a quota that no longer exists. |
| Expected system impact | The customer quota query can follow active rows rather than all history. Index storage/write maintenance grows with active customer orders and status entry/exit; measure that cost before adding a migration. Courier acceptance loses one count query and one quota-lock operation. |
| Verification / rollback | Test the customer quota boundary, each included/excluded status, ownership, history growth, prepared-plan reuse, and index write/update overhead before any migration. Courier regression coverage confirms acceptance past three assignments while retaining same-city and single-winner checks. No client payload change. |

<a id="aud-28-current"></a>

#### AUD-28 — Add bounded cache and worker-pressure telemetry before further tuning

| Field | Full detail |
| --- | --- |
| Purpose / why important | Make future TTL/capacity/pool/concurrency decisions observable instead of assuming a cache or higher worker count improves latency. |
| Type / severity / urgency | Scalability/optimization; **2/10** severity; **2/10** urgency. Optional backend implementation proposal, not a confirmed vulnerability or a verification-only task. |
| Status / created | Proposed improvement; 2026-10-16. No metrics service or alert destination selected. |
| Code locations | `app/services/result_cache.py`; `app/core/query_metrics.py`; `app/workers/broker.py`; scheduled delivery entry points and shared database/Redis pools. |
| Current workflow | Cache failure warnings and slow-request/query logging provide partial diagnostics. There is no consolidated low-cardinality view of cache hit/miss/admission eviction, single-flight wait, pool wait, queue oldest age and per-drainer occupancy to judge the new caching and delivery behavior together. |
| Impact if unresolved | Mixed-TTL misses, queue starvation or connection waits may be mistaken for a slow database or cold start. Adding broader caches without evidence can consume memory/Redis work while leaving latency unchanged. This absence alone is not proof of a runtime failure. |
| Proposed minimal solution | Instrument bounded counters/timers for domain-level hit/miss/fallback/admission, cache bytes/entries, single-flight waits, SQL/pool waits, queue lag, retry/exhaustion and drainer duration. Use a small fixed label set; never label by account, query text, token, signed URL or arbitrary filter. Reuse the chosen logging/metrics boundary rather than adding an unsolicited hosted service. |
| Expected system impact | Low-overhead feedback supports explicit capacity budgets and distinguishes DB, cache, provider and queue bottlenecks. Histograms/cardinality/sampling must themselves be bounded. |
| Verification / rollback | Assert no PII/secrets or unbounded labels; test failure paths and counters, benchmark overhead and ensure metrics outages do not block business operations. No schema migration necessarily needed. Removal of instrumentation must not affect functional behavior. |

<a id="aud-29-current"></a>

#### AUD-29 — Key-rotation inventory omits retained operation snapshots while the CLI claims broader coverage

| Field | Full detail |
| --- | --- |
| Purpose / why important | Give operators an accurate inventory of encrypted fields and remaining old-key dependencies before eventual key retirement. |
| Type / severity / urgency | Code/documentation quality; **2/10** severity; **2/10** urgency. This is not a newly demonstrated cryptographic bypass or permission to retire old keys. |
| Status / created | Open — coverage/wording inconsistency; 2026-10-16. |
| Code locations | `app/models/write_operation.py:23`; `app/services/operation_service.py:82,94`; `app/services/key_rotation_service.py:3–9,67`; `app/rotate_keys.py:3–6`; `docs/documentation.md:989–995` at the reviewed baseline. |
| Current workflow / evidence | Active `write_operations.result_encrypted` snapshots are encrypted and later decrypted for recovery. Rotation handles courier identity fields, conversation preview and withdrawal IBAN, but omits these snapshots from both rewriting and its report. CLI/service descriptions claim rotation of every mutable Restricted column more broadly than the implementation. |
| Existing safeguard | The maintained operational documentation explicitly requires retaining the versioned keyring for retained recovery snapshots. Successful rotation therefore does **not** establish that an old key is removable. Expired operation replay/purge semantics are already documented; they are not a separate newly filed defect. Deferred bank-profile fields remain outside implemented scope. |
| Impact if unresolved | Operators can misunderstand a successful rotation report or discover remaining dependencies only during key-retirement work. Removing keys contrary to the existing runbook can make retained recovery data unreadable, but this audit did not establish that the documented workflow authorizes such removal. |
| Proposed minimal solution | Either include eligible mutable snapshots in bounded, concurrency-safe rotation and report untouched historical dependencies, or narrow CLI/service claims and explicitly list this retained category. Preserve AAD, operation replay semantics and old keys while any retained ciphertext requires them. |
| Expected system impact | Accurate operational inventory without unnecessary re-encryption of immutable history or weakening recovery guarantees. No new encryption primitive or broad schema refactor needed. |
| Verification / rollback | Test old/new-version recovery snapshots, concurrent replay/rotation, bounded batches, retained historical fields and accurate dry-run/report coverage. Documentation-only correction needs no migration; any rotation implementation must remain restartable and preserve old keys for rollback. |

<a id="aud-30-current"></a>

#### AUD-30 — Upstream request history retains the legacy WebSocket token query

| Field | Full detail |
| --- | --- |
| Purpose / why important | Prevent credential-bearing legacy chat URLs from becoming reusable credentials in separately collected ingress/request history, while preserving current clients during rollout. |
| Type / severity / urgency | Security/logging; **6/10** severity; **7/10** urgency. Query capture confirmed with a non-secret canary; no real token leak was induced or claimed. |
| Status / created | Open — upstream configuration/provider blocker; 2026-10-16 follow-up. This is a backend deployment mitigation task, not a verification-only backlog row. |
| Evidence / location | Actual backend.giftly.sa TLS endpoint and CranL query_requests request metadata, path field; app/routers/chat.py:615–624 retains legacy token query compatibility; app/core/logging.py protects Python logs but does not control ingress collection. |
| Current workflow | Existing legacy clients put an access token in the WebSocket URL query. The proxy records a request path independently of Python application logging. Preferred header/subprotocol methods keep credentials out of the URL, but legacy compatibility remains required during rollout. |
| Trigger / reproduction | A bounded rejected legacy handshake sent only a synthetic invalid canary, not a valid credential. The exact canary was present in the path field of one CranL request-history record. Five negative test requests were visible; no canary appeared in the sampled application logs. Raw credential values and complete request URLs are deliberately omitted here. |
| Impact if unresolved | A valid legacy token could be retained in upstream request history and exposed to readers of that history until expiry/revocation. This is not a claim that request history is public or that valid tokens were exposed by this test. It defeats the intended credential-redaction boundary for legacy URLs. |
| Proposed minimal solution | Configure the ingress/request collector to omit query strings or redact token and other credential parameters before persistence; suppress Authorization and credential-bearing Sec-WebSocket-Protocol values in raw handshake logging. Limit access/retention and handle any genuinely exposed historical credentials according to incident policy. Preserve the legacy transport until rollout is approved. Do not expect an application formatter to rewrite upstream history. |
| Deployment blocker | No suitable logging-redaction setting was exposed by the inspected CranL operations/documentation. Read-only container execution failed or returned blank output, so runtime source identity and effective raw proxy logging configuration remain unconfirmed. No deployment or provider setting was changed. |
| Expected system impact | Removes credential capture at its source without changing socket events, message retry semantics, client_message_id, mobile flags or authenticated access rules. Redaction must happen before storage, not merely in the dashboard display. |
| Verification / rollback | After a supported provider/configuration change, repeat header/protocol and legacy canaries and inspect both request metadata and raw ingress capture safely; confirm valid authorized clients still connect. Test missing/invalid/expired/revoked/nonmember restrictions and refreshed reconnection on authorized fixtures. Preserve useful non-sensitive path/status/correlation data; rollback must not restore secret logging. No database migration is proposed. |
| Activation decision | **NO: Safe to enable EXPO_PUBLIC_CHAT_HEADER_AUTH=true has not been established.** Live positive member/refresh checks and complete proxy logging checks are unavailable. Query-free authentication already exists; no duplicate endpoint or message-contract change is needed. |

### Chat authentication verification follow-up — 2026-10-16

The explicit chat/proxy verification request resumes the earlier excluded ingress
redaction check only for this endpoint; unrelated external vendor and dependency
alert work remains excluded. AUD-14–29 statuses and the five postponed items are
unchanged. AUD-30 adds a concrete upstream mitigation, not a testing task.

- Public backend.giftly.sa readiness returned 200. Five TLS-verified missing/invalid
  native, browser, conflicting-header and legacy handshakes returned 403. None proves
  successful member authentication or header/subprotocol forwarding.
- Local focused verification: 57 tests passed, zero skips. The expanded real TCP test
  covers both customer/courier transports, invalid/expired/revoked/nonmember rejection,
  first and second rotating refresh plus query-free reconnect, eight concurrent account
  sockets and ninth rejection, lease release, ban/deletion and unverified courier.
  Existing unit tests cover shared-cap rejection and revoked/membership data-delivery
  prevention. Application credentials are synthetic and never included in this record.
- Full required suite after the test extension: **1,621 passed, zero skips, one
  unchanged upstream Starlette/httpx warning, 86.50 seconds**. No new coverage claim
  is made. Independent Sol6.1 xhigh review found no blocking issue; browser-style
  TCP negotiation is not an actual browser/mobile UI run.
- Application source/authentication behavior did not change. Only compatibility tests
  and documentation were extended. Existing messages, REST history, socket events and
  retry semantics remain unchanged. No mobile repository or retry-safe flag was edited.
- Actual deployment positive authentication, live rotating refresh and reconnect remain
  UNCONFIRMED: authorized live credentials were not available and container execution
  did not yield usable output. Build success and metadata still saying deploying do
  not establish the running chat revision. No production fixtures/provider sends occurred.
- Upstream query capture is confirmed as AUD-30. Absence from a sampled application log
  does not establish raw proxy-header redaction. **Mobile header-auth activation: NO.**

<a id="aud-31-current"></a>

#### AUD-31 — Registration uniqueness failures escape as unexpected server errors

| Field | Full detail |
| --- | --- |
| Purpose / why important | Translate expected account-creation conflicts into a stable domain error while retaining database constraints as the authority under races. |
| Type / severity / urgency | Correctness / reliability / quality; **2/10** severity; **2/10** urgency. Source-confirmed missing error translation, not an account-takeover or duplicate-account vulnerability. |
| Status / created | Open; 2026-10-16 deeper review of published b42c522. Review only; no implementation fix. |
| Code locations | app/services/auth_service.py:157–165; app/repositories/auth_repository.py:39–60; app/models/tables.py:137–145; app/routers/auth.py:81–106; app/core/middleware.py:161–185. |
| Current workflow | Registration verifies the signed phone handoff, checks whether that phone already exists and inserts the user. PostgreSQL enforces unique phone and non-null email values. The insert/flush does not translate the relevant IntegrityError into the domain conflict response. |
| Trigger / evidence | Two concurrent requests using the same valid registration handoff can both pass the phone precheck; the losing unique insert then raises. Independently, a valid new-phone registration can provide an email already used by another account; there is no preceding email conflict check. The public registration path does not catch either database uniqueness failure, so the unexpected-error handler returns HTTP500. This conclusion comes from source tracing; the attempted reviewer probe did not execute successfully and no native concurrency reproduction is claimed. |
| Impact if unresolved | A legitimate retry or conflicting profile input appears as an internal server outage. Clients cannot reliably distinguish a resolvable registration conflict from a transient infrastructure failure and may retry unnecessarily. The database rejects the duplicate and rolls back; no duplicate user/wallet or account takeover was demonstrated. |
| Proposed minimal solution | Catch only the named phone/email uniqueness violations within the established transaction/savepoint pattern and return a safe, stable ConflictError/409. Preserve the unique constraints and avoid relying only on prechecks. Do not convert unrelated integrity failures into successful registration or expose another account's details. |
| Expected system impact | Predictable error handling under ordinary conflicts and races; no new endpoint, account-merging behavior or ownership change. Verify compatibility of the documented409 envelope without weakening validation. |
| Verification / rollback | Test existing-email registration and simultaneous same-phone registration for customer and courier, one committed winner, no orphan wallet/profile, and unrelated database failures remaining unexpected failures. Use disposable PostgreSQL; no migration is expected. Roll back only the targeted error translation if needed. |

### Deeper audit continuation — 2026-10-16, source b42c522

The user clarified that continuation means a full audit, not implementation. This
pass therefore changed only this review document. Two independent Sol6.1 xhigh
reviewers examined account lifecycle/permissions and cache/storage concurrency;
the primary review traced financial settlement, hosted-payment recovery, expiry,
withdrawal replay, reporting and template escaping. Existing AUD-14–30 remain open;
closed AUD-01–13 and accepted/postponed decisions remain unchanged.

Additional inspected safeguards, without claiming formal completeness:

- Reauthentication proofs bind purpose, owner, phone, credential version, expiry and
  consumed state, with row locking. Restore checks include deadline, request/account
  state and deletion marker. New-phone uniqueness already has savepoint/conflict
  handling. Owned inbox predicates and atomic admin TOTP replay claims are present.
- DTO cache keys and migrations0037–0039 separate owner/version and invalidate on
  committed changes. Media grants use atomic claims and cleanup fencing. Avatar
  replacement does not release the active file before the replacement attaches.
  S3/private stream paths have existing byte/pool bounds. No additional confirmed
  N+1 or unbounded Python quadratic loop was established in these inspected paths.
- Expired top-up selection already filters payment purpose before SQL LIMIT, so the
  suspected cross-purpose starvation was rejected. Hosted checkout snapshots bind
  amount/currency/owner/reference and ambiguous outcomes remain unresolved rather
  than being treated as success. Ledger arithmetic remains exact Decimal.
- Withdrawal replay returns the original owned request for a repeated key; no new
  financial duplication defect was established. Fee-policy changes affecting future
  settlement need an explicit product policy before being called a confirmed bug.

**UNCONFIRMED lead, not a new task or confirmed vulnerability:** the image decoder
loads the initial PNG frame and explicitly restricts multiple frames only for HEIF.
Whether a corrupt later APNG frame can pass and creates an actual downstream risk
needs a real-byte reproduction and inspection of consumers. The reviewer probe was
blocked by local uv cache permissions; no result or exploit is claimed. Do not
reopen closed image-decoding findings solely from this lead.

No application fixes, mobile edits, Docker, production writes, provider deliveries,
new migrations or deployment changes occurred. This read-only continuation did not
rerun the full suite: the latest executed full suite remains **1,621 passed, zero
skips, 86.50 seconds**, from the immediately preceding chat test change. Failed
reviewer probe attempts are not test evidence. New documentation gates are recorded
by the checkpoint commit; existing runtime/proxy limitations remain explicit.

**Reserve checkpoint:** account usage was88% consumed when checked, near the user's
10% reserve. Save/push the review and stop further exploratory execution. This is a
bounded continuation of the broad audit, not proof that every possible issue has
been found or that unresolved findings were fixed.

### Current cache assessment and limits

| Area | Assessment after this review | Recommended action |
| --- | --- | --- |
| Public city/catalog reads | Five-minute bounded DTO cache and committed version invalidation exist; no need for a duplicate cache. | Fix mixed-TTL admission (AUD-24); measure benefit (AUD-28). |
| Private saved/annual occasion pages | One-hour account/range/cursor-scoped caching with committed epochs exists. Old version keys remain bounded until expiry. | Retain current authorization and invalidation; resolve shared eviction and the separate reminder ownership fence. |
| Customer/courier order summaries | Thirty-second private account/filter/cursor caching exists with order/participant/rating invalidation. | Retain fresh account/authorization checks and committed invalidation; fix admission before increasing TTL. |
| Invoice PDF cache | Content/template fingerprint, current authorized reads, bounded rendering and one-hour Redis caching exist for the mobile flow. | Reuse safe snapshot/connection release in the separate admin flow (AUD-21). |
| Audit filter catalogs | Bounded 30-second label cache already exists. | No new cache proposed; keep category scope, existing bounds and privacy. |
| Inbox and active-order quotas | Bounded returned pages are not proof of bounded rows scanned; quota state authorizes writes. | Prefer measured SQL changes (AUD-25/26). Do not cache balances, quota/claim eligibility or permissions as authority. |
| Payments, unresolved attempts and available balance | Fresh authoritative state remains necessary. | No additional cache proposal; preserve no-store financial recovery and settlement invariants. |
| Additional broad caches | No new unbounded process-local caches, whole-history downloads or generic caching layer justified. | Resolve demonstrated query/resource issues and instrument existing caches first. |

### Audit method, verification and exclusions — 2026-10-16

- Reviewed the authoritative published baseline, AGENTS.md, README, maintained API
  references/OpenAPI, relevant source and tests. Inventory: **312 application Python
  files and 228 test Python files**. Three independent **GPT-6.1 Sol, high** domain
  reviews covered authentication/media, finance/jobs, and SQL/cache paths; the main
  review checked their claims, rejected unsupported candidates and ran targeted probes.
  This is a broad source audit with deeper review of exposed/hot paths, not a claim
  that every possible execution path is secure or formally verified.
- Fresh complete unchanged suite against owned native PostgreSQL 16/Redis, with native
  media tools available: **1,621 passed, zero skips, one existing upstream
  Starlette/httpx deprecation warning, 84.40 seconds**. This invocation did not request
  coverage, so the prior 89.87% figure is historical and is not a new coverage result.
  Passing existing tests did not cover or disprove the newly reproduced cases.
- Locked production requirements exported through `uv`; `pip-audit==2.10.1`
  (`--strict --no-deps --disable-pip`) reported **no known dependency vulnerabilities**
  in that resolved production Python graph on 2026-10-16. This is not an OS/image audit,
  a guarantee against unknown vulnerabilities, or reconciliation of excluded GitHub alerts.
- Native PostgreSQL experiments used newly created owned audit databases, real migrated
  schema through 0039, isolated synthetic fixtures and temporary cloned tables.
  EXPLAIN/ANALYZE evidence includes generic-plan behavior and write/storage caveats;
  temporary benchmark tables were rolled back. Reminder/receipt synthetic records were
  cleaned up by tracked IDs. Redis cache probes used isolated audit keys, not global
  flushes. In-memory probes did not contact SMS/email/push/payment/storage providers.
- Confirmed failure cases: ADMIN-role OTP token issuance; incomplete body retention;
  rejected WebSocket authorization amplification; transferred-owner reminder device
  selection; receipt poison-batch starvation; zero payout leg rejection; held admin PDF
  connection; mixed-TTL cache self-eviction; cleanup short-circuit. Worker saturation is
  a source-derived failure-capacity analysis. Pooler compatibility and deployed SQL/body
  limits remain conditional, clearly marked in the associated records.
- Candidate claims were excluded where no external attack path or unmet documented
  guarantee was established: SQL parameter interpolation/automatic execution of literal
  user text; ciphertext substitution requiring privileged DB writes; operation-key reuse
  after the documented retention guarantee ends; speculative unbounded Python N² loops.
  Existing batched rating/chat-attachment/media reads and current access controls were
  inspected; no new confirmed N+1 defect was found in those already-corrected paths.
- Final documentation gates: both complete pre-commit and pre-push hook stages passed
  (Ruff lint/format, syntax/private-key checks, strict mypy); diff whitespace checks
  passed. All 29 finding destinations and 46 new source-path references were checked.
  Historical content was compared with the prior committed document and preserved
  exactly apart from refreshed line-number navigation. Audit harnesses were moved
  outside the repository after an initial formatting-gate failure; no gate was bypassed.
- No Docker, mobile edits, real notifications, production database writes, bucket
  writes, provider activation, deployment mutation or authenticated production probes
  occurred. A pushed audit document does not deploy fixes. Live ingress/proxy limits,
  provider behavior, production data distributions and real load/capacity are unverified.
  The prior chat-header-auth deployment activation verdict is unchanged by this review.
- Existing accepted unrestricted admin CRUD, published-testing-secret exception,
  postponed provider/storage/policy work and excluded external validation remain
  visible in the historical sections below. No verification-only backlog rows were added.

**Next implementation order:** resolve the privileged-authentication policy and
request/handshake admission gaps first (AUD-14–16), then worker capacity and reminder
ownership (AUD-17/18), SQL budgets and receipt fairness (AUD-19/20), remaining connection/
pooler/settlement defects (AUD-21–23), measured cache/SQL work (AUD-24–26), then cleanup
and optional instrumentation/inventory consistency (AUD-27–29). Obtain approval where
the record explicitly requires policy or deployment decisions; preserve all accepted
contracts and previously closed fixes.

---

## Historical audit and implementation record — retained in full

**Implementation updated:** 2026-10-15 · See the implementation log at the end for fixes after the audit.

**Document organization updated:** 2026-10-15 · **Audit performed:** 2026-10-14

This is the single maintained record for backend findings, task details, full review
evidence, cache assessment, accepted risks, postponed work and verification limits.
The original task fields and full audit content are retained; this reorganization
does not implement fixes or establish a new deployment.

## Contents

| Section | What it contains |
| --- | --- |
| [Priority register](#priority-register) | All 13 findings/proposals, ordered by severity and urgency. |
| [Detailed task records](#detailed-task-records) | Every original task-table field, in readable two-column tables. |
| [Previously postponed backend work](#previously-postponed-backend-work) | All five postponed backend tasks, status and dates. |
| [Full audit evidence and analysis](#full-audit-evidence-and-analysis) | Complete original review, scope, evidence, mitigations and limitations. |
| [Security findings](#security-findings) | Moderation race, image validation and inspected safeguards. |
| [SQL and runtime findings](#sql-query-amplification-and-runtime-findings) | Query amplification, connections, exports, indexes and complexity assessment. |
| [Cache correctness and inventory](#cache-correctness-and-inventory) | Existing caches, eviction defect and conditional cache proposals. |
| [Local-only migration risk](#local-only-migration-risk-aud-02) | Unpublished reward enum migration risk. |
| [Accepted risks and exclusions](#accepted-risks-and-exclusions-retained) | User-approved limitations and deferred external work. |
| [Checks actually performed](#checks-actually-performed) | Historical check results and explicit verification limits. |

---

## Tracker scope and source baseline

**Giftly backend audit tasks**

**Audit date:** 2026-10-14 | **Source baseline:** published master `0ed5622`
**Scope:** backend only. Security, runtime, SQL, query amplification and cache correctness.
Severity and urgency are separate 1-10 engineering estimates, not CVSS. Rows are sorted
by severity, then urgency/impact. Code locations refer to the published source unless
marked PRIMARY ONLY. No fixes were implemented during this audit.

The main workspace is on `e65072d` with unpublished changes; the reviewed published
checkout is `C:/Users/PC/.codex/worktrees/remaining-api-performance/backend`.
Do not mistake old local code or unpublished features for the current release.

<a id="priority-register"></a>

## Priority register

| Finding | Task | Category | Severity /10 | Urgency /10 | Status |
| --- | --- | --- | --- | --- | --- |
| [AUD-01](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:588) | Serialize courier verification resubmission | security | **6** | 7 | Fixed - full suite and security review verified |
| [AUD-02](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:609) | Use a forward migration for local reward enums | opt | **5** | 6 | Fixed - forward migration and rollback verified |
| [AUD-03](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:630) | Fully decode order request and delivery images | security | **5** | 5 | Fixed - bounded full image decoding verified |
| [AUD-04](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:651) | Release invoice PDF read connections before rendering | perf | **5** | 5 | Fixed - immutable PDF snapshot releases DB connection |
| [AUD-05](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:672) | Batch due-reminder inbox inserts | opt | **4** | 4 | Fixed - one bounded conflict-safe inbox insert |
| [AUD-06](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:693) | Bound retained database export downloads | perf | **4** | 4 | Fixed - shared retained-download admission and deadline |
| [AUD-07](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:714) | Cache audit dropdown catalogs safely | opt | **4** | 3 | Fixed - bounded 30-second category label cache |
| [AUD-08](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:735) | Support chronological financial admin browsing | opt | **4** | 3 | Fixed - measured chronological indexes in migration 0036 |
| [AUD-09](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:756) | Use Redis time for PDF cache eviction | perf | **3** | 3 | Fixed - atomic Redis-time pruning and expiry scores |
| [AUD-10](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:777) | Batch order/proof media grant reads and claims | opt | **3** | 3 | Fixed - constant three-statement prepared media flow |
| [AUD-11](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:798) | Consider a small public catalog cache | opt | **2** | 2 | Fixed - bounded five-minute catalog cache; committed invalidation verified |
| [AUD-12](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:819) | Consider private occasion-page caching | opt | **2** | 2 | Fixed - one-hour private calendars; committed invalidation and deadlock regression verified |
| [AUD-13](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:840) | Consider short-lived private order-summary caching | opt | **2** | 2 | Fixed - private 30-second order lists; committed participant/rating invalidation verified |

<a id="detailed-task-records"></a>

### Detailed task records

Every field from the original task table is retained below. Full supporting audit
narratives, reproduced evidence, safeguards and verification limits follow these records.

<a id="aud-01-task-record"></a>

#### AUD-01 task record

**Serialize courier verification resubmission**

| Field | Full detail |
| --- | --- |
| ID | AUD-01 |
| Task name | Serialize courier verification resubmission |
| Purpose | Preserve administrative account restrictions |
| Why important | An unlocked transition can overwrite a newer ban |
| Impact | Moderation state changes from BANNED to PENDING_VERIFICATION; pending login becomes possible, but marketplace access remains restricted |
| Type | security |
| Code location | app/services/user_service.py:116; app/repositories/user_repository.py:149; app/services/admin_service.py:379 |
| Current workflow | Read REJECTED without a lock; admin commits a ban; stale request writes PENDING_VERIFICATION |
| Proposed solution | Lock and refresh User before checking status, then use consistent User-to-Profile lock order or a conditional REJECTED-to-PENDING update |
| Severity /10 | 6 |
| Urgency /10 | 7 |
| Status | Fixed - full suite and security review verified |

<a id="aud-02-task-record"></a>

#### AUD-02 task record

**Use a forward migration for local reward enums**

| Field | Full detail |
| --- | --- |
| ID | AUD-02 |
| Task name | Use a forward migration for local reward enums |
| Purpose | Make pending wallet-reward code upgrade existing databases safely |
| Why important | Editing applied migration 0020 does not rerun it on databases already at 0034 |
| Impact | If the unpublished reward feature is released as-is, enum inserts fail; not a currently released reward endpoint |
| Type | opt |
| Code location | PRIMARY ONLY: app/migrations/versions/0020_pending_payment_ledger.py:46; app/models/enums.py; app/services/money_service.py:324 |
| Current workflow | Local edits add GIVEAWAY/PROMO_CREDIT inside historical 0020; current released schema lacks those labels |
| Proposed solution | Restore published migration history; add a reviewed new migration and guarded rollback before publishing the pending reward feature |
| Severity /10 | 5 |
| Urgency /10 | 6 |
| Status | Fixed - forward migration and rollback verified |

<a id="aud-03-task-record"></a>

#### AUD-03 task record

**Fully decode order request and delivery images**

| Field | Full detail |
| --- | --- |
| ID | AUD-03 |
| Task name | Fully decode order request and delivery images |
| Purpose | Reject malformed or resource-exhausting JPEG/PNG content |
| Why important | Magic bytes alone do not establish a valid image or apply decoding limits |
| Impact | Authenticated owners can confirm unreadable photos/proof; downstream exploitation is unconfirmed |
| Type | security |
| Code location | app/services/media_service.py:266; app/integrations/storage/real.py:170; app/services/image_decoder.py |
| Current workflow | Avatar/HEIF uses full isolated decoding; order JPEG/PNG checks only its prefix |
| Proposed solution | Reuse the bounded isolated decoder for order JPEG/PNG; retain exact size/type, ownership, create-only uploads, deadlines and claim fencing |
| Severity /10 | 5 |
| Urgency /10 | 5 |
| Status | Fixed - bounded full image decoding verified |

<a id="aud-04-task-record"></a>

#### AUD-04 task record

**Release invoice PDF read connections before rendering**

| Field | Full detail |
| --- | --- |
| ID | AUD-04 |
| Task name | Release invoice PDF read connections before rendering |
| Purpose | Keep normal API queries responsive during cold PDF requests |
| Why important | Database connection lifetime currently includes cache waits and render admission |
| Impact | Slow renders can consume the per-process DB pool and delay unrelated requests |
| Type | perf |
| Code location | app/routers/invoices.py:206; app/core/deps.py:60; app/core/db.py:70 |
| Current workflow | Read authorized invoice/items; await cache/render; request dependency commits only when the route returns |
| Proposed solution | Capture an immutable authorized snapshot and release the read transaction before awaiting rendering; preserve fresh ownership/content checks on every cache hit |
| Severity /10 | 5 |
| Urgency /10 | 5 |
| Status | Fixed - immutable PDF snapshot releases DB connection |

<a id="aud-05-task-record"></a>

#### AUD-05 task record

**Batch due-reminder inbox inserts**

| Field | Full detail |
| --- | --- |
| ID | AUD-05 |
| Task name | Batch due-reminder inbox inserts |
| Purpose | Reduce SQL round trips and time holding reminder locks |
| Why important | Up to 100 inserts already run inside one transaction |
| Impact | Avoidable database work delays scheduled reminder processing as due batches grow |
| Type | opt |
| Code location | app/repositories/notification_reminder_repository.py:63-111; app/workers/occasion_reminders.py |
| Current workflow | Lock a bounded reminder page, then perform one INSERT ON CONFLICT RETURNING per reminder |
| Proposed solution | Insert the bounded inbox batch once and return event keys; preserve duplicate handling, expiry decisions, reminder versions and row locks; up to 100 insert statements become one |
| Severity /10 | 4 |
| Urgency /10 | 4 |
| Status | Fixed - one bounded conflict-safe inbox insert |

<a id="aud-06-task-record"></a>

#### AUD-06 task record

**Bound retained database export downloads**

| Field | Full detail |
| --- | --- |
| ID | AUD-06 |
| Task name | Bound retained database export downloads |
| Purpose | Prevent aggregate temporary-disk exhaustion |
| Why important | The creation lock does not cover the lifetime of downloaded snapshots |
| Impact | Slow overlapping admin downloads retain multiple files of up to 512 MiB each |
| Type | perf |
| Code location | app/admin/backup_router.py:33-66; app/services/database_export_service.py:93-120 |
| Current workflow | Serialize dump creation, release its lock, then keep the file until response cleanup |
| Proposed solution | Apply admission and an aggregate byte/file ceiling through response cleanup, plus a bounded download lifetime or renewable token-owned lease; retain disconnect cleanup |
| Severity /10 | 4 |
| Urgency /10 | 4 |
| Status | Fixed - shared retained-download admission and deadline |

<a id="aud-07-task-record"></a>

#### AUD-07 task record

**Cache audit dropdown catalogs safely**

| Field | Full detail |
| --- | --- |
| ID | AUD-07 |
| Task name | Cache audit dropdown catalogs safely |
| Purpose | Avoid rediscovering action/entity choices across the full audit history |
| Why important | Each activity page executes two category-scoped DISTINCT/ORDER queries |
| Impact | Growing history increases recurring admin query work even though output is capped |
| Type | opt |
| Code location | app/repositories/audit_repository.py:58-74; app/admin/router.py:921 |
| Current workflow | Read at most 200 distinct actions and entities from stored history for every page |
| Proposed solution | Use a small category-scoped cache with an explicit short TTL and safe DB fallback; retain exclusion rules and dynamic stored choices; consider narrow supporting indexes only when plans justify write cost |
| Severity /10 | 4 |
| Urgency /10 | 3 |
| Status | Fixed - bounded 30-second category label cache |

<a id="aud-08-task-record"></a>

#### AUD-08 task record

**Support chronological financial admin browsing**

| Field | Full detail |
| --- | --- |
| ID | AUD-08 |
| Task name | Support chronological financial admin browsing |
| Purpose | Allow bounded pages to use ordered index access |
| Why important | Existing indexes lead with owner/order/wallet IDs, not the global browse order |
| Impact | Global invoices, transactions and top-up browsing can scan/sort growing history |
| Type | opt |
| Code location | app/repositories/admin_browse_query.py:113-131; app/models/tables.py:692,966,1229 |
| Current workflow | Generic admin browser orders by created_at and id with a 26/51/101-row cap |
| Proposed solution | Add only justified created_at/id indexes for these active paths through a new migration; retain keyset semantics and document index storage/insert cost |
| Severity /10 | 4 |
| Urgency /10 | 3 |
| Status | Fixed - measured chronological indexes in migration 0036 |

<a id="aud-09-task-record"></a>

#### AUD-09 task record

**Use Redis time for PDF cache eviction**

| Field | Full detail |
| --- | --- |
| ID | AUD-09 |
| Task name | Use Redis time for PDF cache eviction |
| Purpose | Make cache capacity accounting match actual blob expiry |
| Why important | Application clock skew can remove live index references without deleting their blobs |
| Impact | Actual retained PDFs can exceed the nominal 128-entry bound until their one-hour TTL expires |
| Type | perf |
| Code location | app/services/invoice_pdf_cache.py:46-48,147 |
| Current workflow | Blob TTL uses Redis EX; index scores/pruning use application time.time() |
| Proposed solution | Read Redis TIME inside the same Lua store operation and use that clock for scoring and pruning |
| Severity /10 | 3 |
| Urgency /10 | 3 |
| Status | Fixed - atomic Redis-time pruning and expiry scores |

<a id="aud-10-task-record"></a>

#### AUD-10 task record

**Batch order/proof media grant reads and claims**

| Field | Full detail |
| --- | --- |
| ID | AUD-10 |
| Task name | Batch order/proof media grant reads and claims |
| Purpose | Remove reducible SQL amplification for multi-image requests |
| Why important | Preparation and claims repeatedly read individual grant rows |
| Impact | Five media keys cause 15 grant statements, excluding other request work |
| Type | opt |
| Code location | app/services/media_service.py:194-229; app/repositories/media_repository.py; app/routers/orders.py:115 |
| Current workflow | Read N snapshots, reread N locked grants, execute N conditional claims |
| Proposed solution | Batch snapshots and ordered locked reads; add a confirmed-only batch claim returning keys; reject partial claims atomically. Do not reuse a helper that implicitly confirms uploads. Five keys can use three grant statements |
| Severity /10 | 3 |
| Urgency /10 | 3 |
| Status | Fixed - constant three-statement prepared media flow |

<a id="aud-11-task-record"></a>

#### AUD-11 task record

**Consider a small public catalog cache**

| Field | Full detail |
| --- | --- |
| ID | AUD-11 |
| Task name | Consider a small public catalog cache |
| Purpose | Reuse rarely changed city/featured-gift responses |
| Why important | Repeated public reads may share identical small payloads |
| Impact | Potential DB/read savings; current latency benefit has not been established |
| Type | opt |
| Code location | app/services/city_service.py:25; app/repositories/city_repository.py:20; app/repositories/planning_repository.py:21 |
| Current workflow | Read current active catalogs from SQL on each request |
| Proposed solution | Only if traffic warrants it, cache bounded catalog payloads for about 300 seconds with every real filter/limit and contract version in keys; invalidate on committed admin/catalog changes; keep active-city validation for writes authoritative |
| Severity /10 | 2 |
| Urgency /10 | 2 |
| Status | Fixed - bounded five-minute catalog cache; committed invalidation verified |

<a id="aud-12-task-record"></a>

#### AUD-12 task record

**Consider private occasion-page caching**

| Field | Full detail |
| --- | --- |
| ID | AUD-12 |
| Task name | Consider private occasion-page caching |
| Purpose | Reuse unchanged calendar ranges |
| Why important | Calendar screens can repeat identical owned range queries |
| Impact | Potential read savings; extra invalidation/version complexity may outweigh the gain |
| Type | opt |
| Code location | app/services/occasion_service.py:46; app/services/occasion_occurrence_service.py; app/repositories/planning_repository.py:67 |
| Current workflow | Read owned stored occasions or expand annual occurrences in SQL; no Redis result cache exists |
| Proposed solution | If justified, use account/authorization-version/range/limit/cursor/recurrence-version keys, one-hour maximum TTL, bounded entries/bytes and reliable committed-write invalidation including admin edits; fall back to SQL and keep fresh authorization |
| Severity /10 | 2 |
| Urgency /10 | 2 |
| Status | Fixed - one-hour private calendars; committed invalidation and deadlock regression verified |

<a id="aud-13-task-record"></a>

#### AUD-13 task record

**Consider short-lived private order-summary caching**

| Field | Full detail |
| --- | --- |
| ID | AUD-13 |
| Task name | Consider short-lived private order-summary caching |
| Purpose | Reuse frequent identical order-list requests |
| Why important | List refreshes can repeat owner-scoped queries and batched enrichment |
| Impact | Potential API read savings; stale status/ownership must never affect authorization or acceptance |
| Type | opt |
| Code location | app/services/order_service.py:239,286; app/repositories/order_repository.py |
| Current workflow | Authorize current account, read filtered orders, then batch enrichment; no Redis order-result cache exists |
| Proposed solution | Only if justified, cache summaries briefly, about 15-30 seconds, with actor/role/current authorization and data version plus all filters/cursors. Invalidate committed order/participant/rating/admin changes and cap cardinality/bytes. Never cache claim eligibility, balances or unresolved payment authority |
| Severity /10 | 2 |
| Urgency /10 | 2 |
| Status | Fixed - private 30-second order lists; committed participant/rating invalidation verified |


Open findings are real backend implementation work. Optional cache proposals are not
claims of measured slowness and must not be implemented indiscriminately. Supporting
evidence, cache inventory and accepted risks are in the [full audit](#full-audit-evidence-and-analysis) below.
There are no testing-only or mobile tasks in this tracker.

<a id="previously-postponed-backend-work"></a>

## Previously postponed backend work

| ID | Task | Purpose / dependency | Status | Urgency /10 | Created | Updated |
| --- | --- | --- | --- | --- | --- | --- |
| PUSH-SETUP | Activate Expo push | Configure worker provider and Expo/EAS platform credentials. | Postponed | 7 | 2026-10-13 | 2026-10-14 |
| STORE-SETUP | Activate private S3 | Configure matching private bucket, endpoint and credentials on API/worker; scheduler needs none. | Postponed | 7 | 2026-10-13 | 2026-10-14 |
| FIN-POLICY | Final account closure/fund return | Implement final behavior after business/legal retention policy approval; retain 14-day recovery/admin hold meanwhile. | Postponed | 7 | 2026-10-11 | 2026-10-14 |
| FIN-NOTIFY | Financial notification preferences | Define covered events/channels and implement actual delivery before exposing switches. | Postponed | 4 | 2026-10-07 | 2026-10-14 |

Accepted unrestricted admin CRUD risks, excluded work and unverified deployment/provider
limits remain in the full audit below. They are not silently marked fixed or reopened as testing tasks.

## Tracker publication status recorded on 2026-10-14

Audit documents are saved locally. Commit/push is pending because automatic approval
review reached its usage limit when accessing the managed release worktree.

---

<a id="full-audit-evidence-and-analysis"></a>

## Full audit evidence and analysis

**Giftly backend security and performance audit**

**Date:** 2026-10-14, Asia/Riyadh
**Published source reviewed:** `0ed562276ee5c36cd741c453877790afab235f3c`
**Review model:** three independent GPT-6.1 Sol reviewers at high reasoning, with parent verification.
**Result:** new implementation findings and conditional optimization proposals; no application fixes in this pass.

### Scope and interpretation

Reviewed authentication/OTP/JWT/refresh/logout, account-change proofs/recovery, moderation,
ownership/role boundaries, order/invoice/payment/wallet/withdrawal transitions, HTTP and
WebSocket authorization, storage/image validation, workers/outboxes, admin browsing and
exports, Redis/PDF caches, SQL pagination, indexes, resource limits and shutdown behavior.

The published checkout is `C:/Users/PC/.codex/worktrees/remaining-api-performance/backend`.
The user's main workspace is on older `e65072d` with unrelated unpublished integration,
schema and payout changes. Relevant local-only migration risk is explicitly labeled.
Neither a pushed source file nor a passing mock establishes deployed/provider behavior.

This is a comprehensive source/regression pass, not a security certification. Scores are
engineering severity/urgency estimates, not CVSS. Production p95/p99, query plans at real
cardinality, decoder RSS/CPU and capacity were not measured. Cache/index benefits remain
unmeasured where stated. No Docker, mobile edits, real notifications, production fixtures
or live bucket writes were used. Previously postponed work remains postponed.

### Global priorities

The complete severity-sorted task table, including purpose, importance, impact, type,
code locations, current workflow, mitigation and urgency, is in [tasks.md](tasks.md).

1. **AUD-01, security 6/10, urgency 7/10:** prevent stale verification resubmission from overwriting a ban.
2. **AUD-02, local-only release risk 5/10, urgency 6/10:** migrate pending reward enums forward instead of changing applied history.
3. **AUD-03, security 5/10:** fully decode order JPEG/PNG, not just avatar/HEIF.
4. **AUD-04, performance 5/10:** release invoice PDF read connections before render/cache waits.
5. **AUD-05 through AUD-10, 4/10 to 3/10:** batch reminder/media SQL, bound retained exports, reduce audit-choice scans, assess chronological browse indexes and correct cache eviction time.
6. **AUD-11, AUD-12 and AUD-13, optional 2/10 proposals:** narrowly scoped public catalog, private occasion and short-lived order-summary caches if their benefit warrants complexity.

<a id="security-findings"></a>

### Security findings

#### AUD-01 - Stale courier resubmission overwrites moderation

`app/services/user_service.py:116-132` reads a rejected courier through the plain joined
query in `app/repositories/user_repository.py:149-160`, checks REJECTED and later writes
PENDING_VERIFICATION. `app/services/admin_service.py:379-386` bans under a user lock and
revokes credentials. A ban committed between the resubmission read and write is then
overwritten by the stale transition.

A native PostgreSQL reproduction ran the actual UserService and AdminService in two
sessions against a separate disposable database. After the admin ban committed first,
resubmission committed and the final state was PENDING_VERIFICATION with auth_version=1.
Credential revocation remained intact. The login status check in
`app/services/auth_service.py:126-129` rejects BANNED/DELETED, so subsequent valid login
is permitted by the resulting pending state. That login conclusion is source-derived;
no real account login was performed. Pending/unverified marketplace access remains denied.

**Mitigation:** lock and refresh the user before validating the transition, then acquire
profile state in consistent User-to-Profile order, or use an atomic expected-status update.
Retain the ban/revocation and reject the losing transition. No new endpoint is needed.
A fix should preserve a native concurrent-ban/resubmission regression.

#### AUD-03 - Order JPEG/PNG confirmation accepts invalid image contents

`app/services/media_service.py:266-289` fully decodes HEIF and courier avatars. Order
request/delivery JPEG/PNG takes the prefix-only validation in
`app/integrations/storage/real.py:170-180`. A JPEG/PNG header alone is not a valid image.

A synthetic provider probe used the actual S3 adapter and MediaService with stubbed
transport, never a live bucket. JPEG-header-plus-garbage and PNG-header-plus-garbage both
passed confirmation; the existing full decoder rejected both. Dimension/decompression
guards do not run on that branch. This demonstrates unreadable media acceptance, not
payment theft or a proven downstream decoder exploit.

**Mitigation:** reuse existing bounded isolated decoding for order JPEG/PNG, preserving
actual type/size, owner/purpose checks, create-only storage writes, admission, deadlines
and claim fencing. Keep malformed/truncated and oversized-dimension cases as regressions.

#### Controls inspected without an additional confirmed defect

Ownership/role filters remain on participant reads; public/customer/courier boundaries
are enforced server-side. OTPs/proofs are purpose-scoped and hashed, token issuer/audience/
algorithm/expiry validation and refresh revocation are retained, and financial settlement
uses exact Decimal arithmetic, row locks and idempotency. Provider callbacks do not trust
browser redirects. Dynamic SQL identifiers are metadata-allowlisted; untrusted values use
bind parameters. User text is literal/escaped rather than executed. Logging scrubs tokens,
OTPs, personal data and signed URLs; storage failures use safe errors.

These controls were inspected, not asserted to cover every possible attack. A suspected
order-acceptance ban race was rejected after inspecting CourierRepository.lock: it joins
current ACTIVE/nondeleted courier state after the account lock, and response eligibility
is rechecked. That rejected hypothesis is not an implementation task.

<a id="sql-query-amplification-and-runtime-findings"></a>

### SQL, query amplification and runtime findings

#### AUD-04 - Invoice rendering retains a database connection

`app/routers/invoices.py:206-214` performs fresh authorized invoice/item reads, then
awaits PDF cache lookup, render admission and thread work. The read-only request
transaction in `app/core/deps.py:60-66` commits only when the route returns.
Unlike wallet statement export, this path does not release its reads before rendering.

Default per-process pool capacity is five persistent plus ten overflow connections;
the PDF cache allows four renders and 128 distinct in-flight identities. Thus cold PDF
requests can hold connections while waiting and interfere with unrelated API reads.

**Mitigation:** capture authorized immutable rendering data, release the read transaction
using the existing non-expiring session factory, then await cache/render. Never skip the
fresh ownership/content checks before a cache hit. Production latency savings are unknown.

#### AUD-05 - Due-reminder materialization performs one insert per reminder

`app/repositories/notification_reminder_repository.py:63-111` locks up to 100 due rows,
then issues one INSERT ON CONFLICT RETURNING for each, all in the same transaction.
This is reducible bounded per-item SQL, not unavoidable per-item worker isolation.

**Mitigation:** one bounded bulk inbox insert returning event keys, while retaining
duplicate/expired decisions, reminder versions and locks. At 100 reminders, insertion
round trips can fall from 100 to one; row-level audit/storage work still remains.

#### AUD-06 - Retained export files have no aggregate download bound

`app/admin/backup_router.py:56-66` releases its dump-creation lock before returning
BackupResponse. The snapshot remains until response cleanup at lines 33–38. The 512 MiB
per-dump limit in `database_export_service.py:93-120` does not limit simultaneously
retained snapshots from slow downloads.

Actual route calls with fake exports and tiny disposable files produced two retained
snapshots while no creation lock remained; maximum simultaneous dump locks was one.
Simulated send failure removed both files, so disconnect cleanup works.

**Mitigation:** bound admission and retained bytes/files over creation and response
lifetime, with a download deadline or renewable token-owned lease. Extending an unrenewed
300-second lock over an arbitrary download is insufficient. This is authenticated-admin
resource exposure; no public-user exploit was demonstrated.

#### AUD-07 - Audit dropdown discovery repeats history-dependent work

`app/repositories/audit_repository.py:58-74` runs two DISTINCT/ORDER/LIMIT 200 queries
for each activity page at `app/admin/router.py:921`. Output is bounded but discovery
work depends on stored history; current indexes serve other category/time/actor/entity
paths.

**Mitigation:** small category-scoped choice cache with a short explicit TTL and DB
fallback, or justified narrow indexes/catalog maintenance. Preserve dynamic stored
choices and HTTP-log exclusions. No actual scan plan or production speedup was measured.

#### AUD-08 - Global financial admin browsing lacks matching chronological indexes

Active generic browsing uses `app/repositories/admin_browse_query.py:113-131` with
created_at/id ordering and bounded pages. Invoices, transactions and wallet_topups have
owner/order/wallet-leading indexes in `app/models/tables.py:692-710,966,1229-1252`, not
the unscoped chronological order.

**Mitigation proposal:** only appropriate created_at/id indexes on growing active browse
paths, through a new migration with rollback and write/storage cost analysis. This is a
source-confirmed index mismatch; planner choice and benefit remain unmeasured. Do not add
indexes for every arbitrary admin sort.

#### AUD-10 - Order/proof media claims make 3N grant calls

`app/services/media_service.py:194-229` prepares N grants, then rereads N locked grants
and performs N conditional claims. A service-level probe counted 3 calls at N=1 and
15 at N=5; the current repository executes one statement per such operation. This is
bounded by five media keys, but reducible.

**Mitigation:** batch snapshots and ordered locked reads, plus a confirmed-only batch
claim returning keys. Reject partial claims atomically and preserve ownership, purpose,
metadata comparisons and stable locking. Do not blindly reuse confirm_and_claim_many:
it also confirms grants and does not require the existing confirmation condition.
Per-object storage reads/decoding are still needed; this proposal batches SQL only.

#### Existing efficient patterns and N-squared assessment

Order ratings, chat attachments and admin relationship/actor labels are already batched.
Hot list reads apply owner/status/date filters before bounded keyset pagination.
Courier report totals use SQL aggregates, not full Python ledger collections.
No confirmed unbounded O(n-squared) Python loop was found in the inspected mobile paths.
A row-by-fixed-column serializer or a constant number of passes is not itself O(n-squared).

Worker per-item transactions may deliberately isolate failures/retries. Do not replace
them indiscriminately with a large transaction; the reminder finding concerns inserts
that already share a single transaction. Shared Redis pools, bounded messages/uploads/
decoder jobs and worker concurrency/shutdown controls are retained.

<a id="cache-correctness-and-inventory"></a>

### Cache correctness and inventory

| Cache / reuse | Existing behavior | Assessment |
| --- | --- | --- |
| Invoice PDF Redis cache | One-hour absolute TTL; content/template fingerprint; fresh authorization/content reads on every hit; 128 indexed entries; 256 KiB decoded PDF cap; 100 ms Redis budgets; safe fallback on bad values or outage | Freshness/authorization controls are sound in inspected paths; AUD-04 connection hold and AUD-09 capacity accounting remain |
| PDF rendering singleflight | Same-process identical misses share work; four render slots; 128 distinct identities; cancellation does not release a live render's slot | Bounded per process; cross-process duplicate rendering is possible, but no need for distributed coordination was measured |
| Read-only request memoization | read_snapshot.py:21-37 caches rows and missing results for the exact read-only SessionTransaction | Mutation paths bypass it and new transactions invalidate reuse; no cached write authority found |
| Prepared media heads | At most five actor/purpose/key snapshots per service instance, followed by fresh locked grant checks | Required attachment checks retained; batch the SQL instead of trusting the memoized head alone |
| Settings and templates | Process-lifetime validated settings; compiled Jinja environments, not cached user responses | Restart for configuration changes; do not use these as shared session/authorization state |
| Admin static assets | Content-hashed public assets have immutable caching; private admin HTML is no-store | Appropriate separation of static assets from private data |
| Orders, occasions and financial balances | No Redis result cache found in published source | Earlier cache expectations are not implemented contracts; authoritative reads remain current |

#### AUD-09 - PDF eviction uses a different clock from TTL expiry

`app/services/invoice_pdf_cache.py:46-48,147` prunes/scores its index using application
time.time(), while Redis EX controls actual blob expiry. A forward host-clock jump can
remove live index references without deleting those blobs.

A native Redis reproduction ran the actual store Lua script in an isolated temporary
namespace with cap 2 and three tiny blobs. Advancing the third supplied clock by 3,601 seconds
left index size 1 while all three blobs remained live. All probe keys were removed.
The same accounting issue applies to the real 128-entry cap; blobs still expire after
one hour. No cached ownership or financial-state bypass was found.

**Mitigation:** Redis TIME inside the same Lua operation for both scoring and pruning.

#### Conditional new caching proposals: AUD-11, AUD-12 and AUD-13

Public catalogs are the simplest shared-cache candidate. Private occasion and order
summary pages are possible but require every real filter/cursor plus actor/role/current
authorization and reliable data-generation keys. Proposed starting TTLs are about 300 seconds
for catalogs, a one-hour maximum for occasions, and 15–30 seconds for order summaries.

Every candidate needs bounded entry count, bytes and key cardinality; short dependency
timeouts; safe DB fallback; and invalidation after all relevant committed writes including
admin/raw database changes and workers. Preserve current authorization before reuse.
Caching does not replace SQL indexes, batching, connection release or ownership checks.

Do not cache authoritative available balances, unresolved payments, OTPs, sessions or
claim eligibility. Do not reuse private pages across accounts or publicly cache signed
media/PDF downloads. Keep financial reports authoritative until a genuinely safe version/
invalidation design and material benefit are established. No cache was added during this audit.

<a id="local-only-migration-risk-aud-02"></a>

### Local-only migration risk: AUD-02

Primary unpublished changes add GIVEAWAY/PROMO_CREDIT to applied migration 0020 and to
TransactionType/credit_wallet_reward. A read-only enum query on the freshly migrated
released schema confirmed neither label exists. A database already at 0034 does not rerun
0020 when the local feature is published.

This is not a released wallet-reward endpoint defect. Restore applied migration history
and introduce a forward migration with a safe downgrade/refusal policy before publishing
the pending feature. Reconcile local edits with current master without overwriting unrelated
work or regressing the released provider/runtime safeguards.

<a id="accepted-risks-and-exclusions-retained"></a>

### Accepted risks and exclusions retained

| Existing ID / topic | Disposition | Remaining impact |
| --- | --- | --- |
| SEC-16 / privileged financial CRUD | Accepted by user, 9/10 | Authorized admins can directly alter posted financial state |
| SEC-17 / audit CRUD | Accepted by user, 9/10 | Privileged history can be edited/deleted |
| SEC-19 / published testing-default rejection | Excluded by user, 7/10 | Deployment operators must keep real secrets separate; no new rejection rule was added |
| Ingress credential redaction and GitHub alert reconciliation | Previously excluded | Current remote guarantees/alert disposition remain unconfirmed |
| Real SMS/email/push/Dhamen/storage integrations | Postponed/external scope | Fake/mock success is not live delivery or payment readiness |
| Audit archival | Excluded for development | No automatic purge/archive was added |

The locked production dependency audit reported no known vulnerabilities. GitHub previously
reported a moderate alert; this pass did not reconcile that excluded alert. Those statements
are not contradictory evidence of resolution. Production use must not expose development
OTP/fake-provider behavior to real customer data; the intentionally deferred setup is not
a claim of production readiness.

<a id="checks-actually-performed"></a>

### Checks actually performed

- `uv run --locked pytest --cov=app --cov-report=term --cov-fail-under=85` on fresh disposable native PostgreSQL 16 and Redis: **1522 passed, zero skips, 89.44% coverage**, one unchanged Starlette/httpx deprecation warning.
- Security reviewer: 117 focused unit checks; cache/runtime reviewer: 43 focused checks. These overlap the full suite and are not additive totals.
- Locked production export plus `uvx pip-audit==2.10.1 --strict --no-deps --disable-pip`: no known vulnerabilities reported.
- Native two-session moderation/resubmission probe reproduced the lost ban.
- Actual store Lua/native Redis probe reproduced clock-skew capacity drift; temporary keys cleaned up.
- Actual S3 adapter/MediaService with stubbed transport accepted malformed JPEG/PNG that the full decoder rejected.
- Media orchestration probe confirmed 2N grant reads plus N conditional claims.
- Backup route probe with fake exports confirmed simultaneous retained files and working failure cleanup.
- Released-schema enum query confirmed local pending reward labels are absent.

Existing tests passing does not prove the newly found failure cases are fixed. They require
implementation and durable regression coverage as part of those fixes, not standalone
testing tasks. No production load/latency/EXPLAIN measurement or live provider verification
was performed. No application source, dependencies, migrations or public contract was changed.

### Review publication status recorded on 2026-10-14

Documents are saved in the permitted main workspace. Commit/push remains pending:
automatic approval review hit its usage limit during a release-worktree operation.
This was a review-service failure, not a finding that the requested Git action was unsafe.

---

## Implementation and publication log

### AUD-01 — courier verification resubmission

**Implemented:** 2026-10-15. **Category:** security. **Original severity:** 6/10.

The service now reads moderation state through a dedicated repository operation that
locks and refreshes User first, then locks and refreshes CourierProfile. Resubmission
validates the current role, nondeleted account and REJECTED status under those locks.
Normal profile edits acquire the same User-to-Profile locks before changing either row and reject newly banned/deleted accounts; this prevents the flush-order deadlock identified during review.
A committed ban/deletion cannot be replaced by a stale request, and competing
resubmissions produce one transition and one audit event. Existing success/error
schemas and routes remain unchanged; no migration or mobile changes are required.

The initial eight focused unit tests failed against the old implementation in seven relevant
cases, then passed after the locking fix. Three additional unit regressions first failed for normal profile edits, then passed after that write path was aligned. Five native PostgreSQL tests cover stale
banned/deleted state, a moderation lock held until commit and simultaneous
resubmissions plus concurrent mixed profile edits. Existing mobile profile contracts also pass (22 combined focused checks).
Final disposable PostgreSQL/Redis suite: **1,538 passed, zero skips, 89.63% coverage**; one unchanged upstream deprecation warning. Both pre-commit/pre-push gates passed, including strict mypy. Focused GPT-6.1 Sol high review identified the profile flush-order inversion, which was fixed and re-reviewed with no remaining actionable issue. No Docker, production writes, real provider delivery or mobile edits were used. Source publication is verified against origin/master after the push; this does not establish deployment.

Rollback is a code revert; no persisted-schema or data backfill is needed. Reverting
would reintroduce the race, so a security rollback should retain equivalent locking.

The original audit narratives above describe the 2026-10-14 baseline. They remain
unchanged as evidence; current task status and this log describe subsequent fixes.

### AUD-02 — forward reward transaction enum migration

**Implemented:** 2026-10-15. **Original severity:** 5/10; local-only release risk.

Applied migration 0020 remains unchanged in the release. Its two unpublished reward
additions and reward-only rollback check were removed from the primary copy only
once the remaining content matched the published file exactly. All other pending
reward implementation changes remain untouched and unreleased.

New migration 0035_reward_transaction_types follows 0034 and adds GIVEAWAY and
PROMO_CREDIT idempotently to the existing transaction_type. Existing databases upgrade
without replaying 0020. This migration exposes no reward endpoint and enables no
financial operation by itself. The primary checkout is still an older unpublished
snapshot; reconcile that pending feature onto current master, which contains 0035,
before releasing it. Do not copy 0035 alone onto the old 0028 migration chain.

Rollback locks transactions and refuses if either reward type appears in retained
history, regardless of transaction status. With no reward rows it moves the Alembic
revision back while retaining unused labels: PostgreSQL cannot safely drop individual
enum labels without replacing the financial type. This additive residue is deliberate,
allows older code to run on old-type rows and supports idempotent re-upgrade. Stop
reward writers before rolling back; never delete retained reward rows to bypass the guard.

The schema regression first failed on the already-migrated 0034 database. Native
PostgreSQL upgrade to 0035 then passed all four focused cases, including both reward
rollback guards and unused-label rollback. Downgrade to 0034 and re-upgrade to 0035
also passed on the disposable full schema. A separate fresh database migrated from empty through 0035 and passed all four focused cases. Final full suite: **1,542 passed, zero skips, 89.61% coverage**, one unchanged upstream warning. Both hook stages and focused GPT-6.1 Sol high review passed. Publication is checked against origin/master after this commit; deployment is not inferred.
No public response, mobile contract, credentials or production data was changed.

### AUD-03 — full order-photo and delivery-proof decoding

**Implemented:** 2026-10-15. **Category:** security. **Original severity:** 5/10.

JPEG/PNG order request photos and delivery proof now follow the same full-content
validation path as courier avatars and HEIF. The service checks current metadata,
reads no more than the declared byte size, requires the exact number of bytes and
runs the isolated decoder for the declared format. Magic prefixes alone cannot
confirm an object. The existing 20-million-pixel ceiling, two-slot image admission,
25-second overall budget and decoder termination/reaping rules remain in force.
Ownership, purpose, confirmation, cleanup fences, conditional PUT and reauthentication
around storage waits remain unchanged. Valid image contracts and endpoints are unchanged;
malformed, mislabeled or size-mismatched objects return the existing safe 400 error,
and unavailable/timed-out validation returns the existing safe 503 error without
confirming or claiming the grant. No new storage permissions or mobile changes are needed.

Ten initial tests failed on the prefix-only implementation, then passed with full
decoding. Thirteen image regressions now include valid JPEG/PNG for both purposes,
malformed headers, wrong declared format, incomplete downloads, excessive dimensions,
timeouts and oversized streams. Existing fake-storage integration fixtures explicitly
record real image bytes; the application fake and production validation have no new
bypass. Transaction-only tests mock the decoder boundary while retaining their
authorization, cleanup and mutation assertions. Media-flow checks31 and decoder/avatar
checks36 passed; the counts overlap and are not additive totals.

Full decoding has an intentional CPU/I/O cost relative to reading a header. Admission,
byte/pixel limits and isolated-process cancellation bound that cost. No production
latency or capacity improvement is claimed. The additional delivery/idempotency fixtures also supply real bytes; all19 related lifecycle/ownership checks passed. Final native PostgreSQL/Redis suite: **1,555 passed, zero skips, 89.65% coverage**, one unchanged upstream warning. Both hook stages and focused GPT-6.1 Sol high security review passed. No application fake bypass, Docker, production account/bucket changes, mobile edits or new dependencies were introduced. Source publication is verified separately from deployment.
Rollback is a code revert, but it would restore acceptance of malformed image headers;
retain full validation in any security rollback. No migration or data rewrite is needed.

### AUD-04 — release invoice PDF database reads before rendering

**Implemented:** 2026-10-15. **Category:** performance. **Original severity:** 5/10.

GET /api/invoices/{invoice_id}/pdf still performs current account/participant checks
and current invoice/item reads on every request, including cache hits. It now copies
only rendering/fingerprint fields into frozen slotted header/line records, then commits
the read-only transaction before Redis lookup, render admission or thread work.
Rendering receives immutable scalar values and an immutable line tuple, so cancellation,
rollback or Session teardown cannot expire the document's input or start lazy DB reads.
Existing worker/direct renderer callers remain compatible with stored ORM models.

No totals are recalculated and all amounts remain Decimal. The snapshot produces the
same actual PDF drawing streams and fingerprint as the original input. The one-hour
cache, content/status invalidation, bounded rendering, branded English layout, GMT+3
dates and private/no-store download headers remain unchanged. Fresh authorization is
never replaced by cache authority. The renderer source hash changes once on rollout,
which intentionally refreshes older cached documents without changing their layout.

Four of five initial boundary cases failed before the fix. Native HTTP with a single
PostgreSQL connection observed zero checked-out connections inside rendering after
the fix; both customer/courier reads pass and an unrelated account still receives404
without cache access. Unit regressions cover cached/fallback paths, commit failure,
immutable independent inputs and real PDF/fingerprint equivalence. Focused15 checks
passed. No production latency/capacity improvement is claimed from these local checks.

Final full suite on fresh disposable PostgreSQL/Redis: **1,561 passed, zero skips, 89.69% coverage**, one unchanged upstream warning. Both hook stages and focused GPT-6.1 Sol high review passed. A reused test database had exposed an older order test assumption that its row was on the first page; the unchanged suite passed on the fresh database. No assertions or application filters were weakened. Source publication is checked against origin/master separately from deployment. No API schema, migration,
new dependency or mobile change is needed. Rollback is a code revert; it restores the
connection-hold and ORM-lifetime behavior, so preserve equivalent release/snapshotting
in any operational rollback. Original audit evidence above remains dated to its baseline.


### Execution checkpoint — 2026-10-15

The first four tasks were implemented, independently reviewed, checked and pushed
separately in the requested order. Each remote commit was verified before work began
on the next task.

| Completed task | Published implementation commit | Final full-suite evidence |
| --- | --- | --- |
| AUD-01 — moderation/resubmission locking | bc05e9f8c306f476ee0a1f7d02e0c157baa98048 | 1,538 passed; zero skips; 89.63% coverage |
| AUD-02 — forward reward enum migration | 3fffa4b9ada7920eaa91abb0da32c075d4ae4477 | 1,542 passed; zero skips; 89.61% coverage |
| AUD-03 — full order image decoding | d1cfd072b3b9d0c68c42bd404902b9481de51261 | 1,555 passed; zero skips; 89.65% coverage |
| AUD-04 — release PDF read connections | 45947b6afe03e019c3352c6ccb1dac0fba78b1da | 1,561 passed; zero skips; 89.69% coverage |

All four also passed the commit/push gates and focused security review. Public API
fields and endpoints remain compatible; no mobile code or UI-agent change is required.
Migration 0035 is additive and must run through the usual deployment bootstrap.
Pushed commits are not a claim that any specific deployment has rolled out.

**Next:** AUD-05, bounded reminder-inbox batching. AUD-05 through AUD-10 remain
unfinished; AUD-08 still needs measured index justification. AUD-11 through AUD-13
remain conditional cache proposals, requiring evidence and safe invalidation rather
than indiscriminate caching. All five previously postponed backend items and all
accepted/excluded risks retain their earlier disposition.

At this checkpoint the account's five-hour usage window reported 89% used, about 11%
remaining. Work stopped before starting the next task's implementation/test cycle
to preserve the user's earlier approximately 10% reserve. No incomplete AUD-05 changes
are staged or published. The dated 2026-10-14 publication limitations above are historical;
the implementations and consolidated review through AUD-04 are now published as listed.


### AUD-05 — batch due-reminder inbox inserts

**Implemented:** 2026-10-15. **Category:** optimization. **Original severity:** 4/10.

The repository still selects and locks the same bounded due-reminder page, with
active customer, ownership relationship, enabled state and reminder-version checks.
It now issues one PostgreSQL INSERT ON CONFLICT DO NOTHING for the complete selected
page, returning the actually inserted event keys. A set lookup preserves the original
per-reminder decision: existing inbox events suppress another push, newly inserted
unexpired events remain pending, and events at least one day old retire their push.
The inbox insert and reminder updates remain atomic in the caller transaction.
Empty pages issue no insert. No network operation moved inside the transaction.

Native PostgreSQL regressions reproduced 10 and 100 INSERT statements before the
change and one afterward, with the same inbox contents and reminder decisions.
The 1/10/100-item cases also cover existing events, exact expiry, pending events,
delivered timestamps and repeated empty materialization. All 28 focused reminder
and annual-recurrence tests passed. This reduces client/database round trips; it
does not remove per-row database constraints or audit work, and no deployed latency
or capacity improvement is claimed from this local evidence.

No schema, migration, dependency, HTTP/WS contract or mobile change is required.
Rollback is a code revert and restores the per-item SQL cost. Original audit details
above remain intact as historical evidence. Full native PostgreSQL/Redis suite: **1,564 passed, zero skips, 89.69% coverage**,
one unchanged upstream warning. Both hook stages and focused GPT-6.1 Sol high
review passed. No Docker, production writes or provider calls were used. Publishing
is verified separately from deployment. **Next:** AUD-06, retained export downloads.


### AUD-06 — bound retained database export downloads

**Implemented:** 2026-10-15. **Category:** performance/resource safety. **Original severity:** 4/10.

The existing global generation lock, admin authorization, CSRF verification,
read-transaction release, 60-second dump timeout, 512 MiB SQL limit, private files,
password encryption and no-store attachment headers remain. While the generation
lock is still held, a completed export now acquires a separate global Redis download
slot. The slot remains held after the route returns and is released only after
response cleanup. Competing completed exports receive the existing conflict error;
only their own generated temporary directory is removed.

Streaming has a two-minute deadline from download admission. The token-owned Redis
lease expires after three minutes, leaving a minute of cleanup margin. A cancelled,
failed, disconnected or completed stream removes its file and releases admission.
The ASGI deferred path-send optimization is disabled for these private snapshots so
cleanup follows actual file streaming, rather than deleting before server file access.
No response body or file is buffered in full in application memory.

This bounds normal concurrent retention to one completed download plus one ongoing
export generation across replicas. Encryption can temporarily retain both its SQL
source and encrypted output; this is existing bounded generation storage, not an
unlimited download queue. A competing request can still generate a snapshot before
it discovers the download slot is busy. Hard process termination can leave a private
orphan on ephemeral storage, as already documented; this change does not claim crash
or Redis-state-loss guarantees for those orphaned files.

The initial regression failed on admission ending before response streaming, and
another failed on the missing deadline. Thirteen focused checks now pass, including
real Redis overlapping admission, rejection cleanup, disconnect release, subsequent
retry, timeout cleanup, ASGI path-send exclusion, CSRF, authorization, password
validation, size rejection and byte-preserving export/encryption behavior.
No production export or writes were performed. Full native PostgreSQL/Redis suite:
**1,566 passed, zero skips, 89.69% coverage**, one unchanged upstream warning. Both
hook stages and focused GPT-6.1 Sol high review passed. Source publication is
verified separately from deployment. **Next:** AUD-07, audit dropdown catalogs.
No migration, environment variable, public API or mobile change is required.
Rollback is a code revert and restores the retained-file exposure.


### AUD-07 — cache audit dropdown catalogs safely

**Implemented:** 2026-10-15. **Category:** optimization. **Original severity:** 4/10.

Authenticated admin activity reads now reuse a Redis catalog containing only the
selected tab's distinct action/entity labels. The three supported tab combinations
map to three fixed keys; arbitrary category combinations bypass caching. Each key
expires after 30 seconds. Values are validated for shape, string type, label lengths,
200 choices per field and bounded serialized length before use. Optional Redis reads
and writes each have a 100 ms budget and fall back to the original database query on
connection failure, timeout or invalid cached content. Cache logs contain no payload.

The original repository remains authoritative on a miss and retains category scope,
HTTP/WS/system-job exclusions, sorting and choice limits. No activity records, actor
identities, entity values, authorization decisions or private responses are cached.
Admin session checks still run before this service call. New, edited or removed label
choices can remain stale for at most 30 seconds; this does not hide new activity rows
or prevent an already selected action filter from running against current data.

The initial regression reproduced repeated repository calls on identical reads.
Native PostgreSQL/Redis query counting now observes two discovery statements on the
first read, zero on the next read and two after removal/expiry refresh. Twenty-two
focused checks cover category separation, bounded key count, cache reuse, refresh,
malformed/oversized values, invalid UTF-8 and outage fallback alongside existing
filter tests. Focused review found the Redis decode error bypassed fallback; its
new regression failed before the narrow exception fix and passed afterward.
No production latency measurement is claimed. Concurrent cold misses may still
repeat the original bounded discovery reads; no distributed refresh lock or new
infrastructure was added for this low-traffic admin-only label hint. Database errors
remain visible rather than being converted to empty/successful activity results.

No new environment variable, dependency, migration, public contract or mobile change
is needed. Rollback is a code revert; versioned keys expire naturally. Full native PostgreSQL/Redis suite:
**1,577 passed, zero skips, 89.75% coverage**, one unchanged upstream warning. Both
hook stages and focused GPT-6.1 Sol high review passed after the fallback fix.
Publication is checked separately from deployment. **Next:** AUD-08, measured
chronological financial browse indexes. Original audit evidence above is retained.


### AUD-08 — support measured chronological financial browsing

**Implemented:** 2026-10-15. **Category:** SQL optimization. **Original severity:** 4/10.

The actual allowlisted admin browse query already applies filters and stable keyset
pagination before returning 26/51/101 rows. Its global default created_at/id ordering
lacked a matching unscoped index for invoices, transactions and wallet_topups. The
existing owner/wallet/order-leading indexes cannot efficiently serve that global
chronological prefix. Migration 0036 adds only (created_at DESC, id DESC) on those
three tables; model metadata is aligned. Existing scoped indexes, SQL, authorization,
financial invariants, ordering and response contracts remain unchanged.

Reproducible benchmark: `uv run --locked python -m tests.admin_browse_query_plans`,
with GIFTLY_PERF_DATABASE_URL set to an explicitly disposable loopback PostgreSQL16
URL. It creates session-local temporary tables from the current model definitions,
retains existing indexes and non-FK constraints, and seeds 100,000 synthetic rows per
table with tied created_at timestamps. Foreign keys and application/audit triggers
are deliberately absent; these are query/index cost measurements, not live ledger,
production throughput or end-to-end latency benchmarks. It compares actual browse
SQL for first/cursor/ascending pages at page sizes 25/100, four measured read samples,
index sizes and four rolled-back 1,000-insert samples before/after each candidate.
All 18 query shapes must return identical rows. Temporary data disappears on close.
Raw EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) evidence is retained locally under
.superpowers/audit-fix-08-plans.json; it contains synthetic data only.

Each candidate changed scan/sort plans to direct index scans and occupied 4,079,616
bytes (about 3.9 MiB) at 100,000 rows. There is one additional B-tree write per inserted
financial row, extra storage and WAL, and timestamp/index maintenance cost on edits.
Repeated sample timings are recorded below; results are specific to this synthetic
history and local environment, not a deployment capacity promise. No indexes were
added for arbitrary optional sorts, status filters or unrelated tables.

Migration 0036 is a new forward revision; applied history is unchanged. It uses ordinary
transactional index builds. Each lock wait is limited to 5 seconds and each CREATE
INDEX statement to 60 seconds. Earlier table locks remain held until all three builds
commit, so the first table's write-blocking interval can span three build budgets
plus lock waits, not merely 60 seconds total. Run this revision during a low-traffic
maintenance window. Contention or a build timeout rolls back the entire revision;
retry after resolving contention. For substantially larger/busy installations,
prepare a separately reviewed concurrent-index rollout instead of raising these
limits or repeatedly blocking writes. No production migration was run here.

Four initial regressions failed because the indexes and migration were missing.
The focused native checks now confirm model/database definitions and reversible
index removal/recreation. Full fresh-database bootstrap through 0036 and real Alembic
downgrade to 0035 and upgrade to head also pass. Downgrade removes only the three read indexes,
without altering financial rows; old code remains compatible, with slower admin
chronological reads after rollback.

Final four-sample medians, without the full suite running concurrently:

| Table | First 25-row page before / after (ms) | 1,000 inserts before / after (ms) | Extra index bytes |
| --- | --- | --- | --- |
| invoices | 79.661 / 0.022 | 48.850 / 57.228 | 4,079,616 |
| transactions | 60.839 / 0.021 | 42.376 / 50.367 | 4,079,616 |
| wallet_topups | 51.708 / 0.020 | 40.049 / 42.071 | 4,079,616 |

The first-page numbers measure 26 selected rows, including the pagination lookahead.
Cursor and ascending shapes also use the new index, with identical row contents.
Full native PostgreSQL/Redis suite: **1,581 passed, zero skips, 89.74% coverage**,
one unchanged upstream warning. Both hook stages and focused GPT-6.1 Sol high
review passed. Publication is verified separately from deployment.
**Next:** AUD-09, Redis-clock-based invoice PDF cache eviction. No public/mobile contract, environment setting or dependency changed.


### AUD-09 — use Redis time for PDF cache eviction

**Implemented:** 2026-10-15. **Category:** performance/cache resource safety.
**Original severity:** 3/10.

The atomic invoice PDF storage Lua now reads Redis TIME once and uses that clock
for expiry-index pruning and newly stored document scores. Redis's own EX expiry
and the eviction index therefore use the same clock domain. Fractional seconds
retain ordering between successive writes. The API server no longer supplies its
wall time or influences cache eviction. The Lua call's private argument list was
updated with its direct-script regression; no public endpoint or response changed.

A native Redis reproduction exercised the actual cache service with capacity two
and three different invoice documents. Before the fix, a two-hour forward API-clock
jump pruned the index while all three blobs remained live. After the fix, capacity
stays at two and index scores remain approximately one hour after Redis time.
Coverage includes backward skew as well as the existing oldest-entry eviction,
exact TTL/no hit-extension, status/item fingerprint changes, outage fallback,
render coalescing, bounded thread capacity, cancellation and immutable inputs.
The initial 16 focused PDF checks passed before adding the backward-skew variant;
that variant is included in the final full-suite evidence below.

The 128-entry/256-KiB-document bounds, one-hour TTL, fresh authorization/current
invoice reads, private responses and content/status invalidation remain unchanged.
Existing blobs already orphaned by old code still expire under their original TTL;
this patch does not scan Redis or claim immediate reclamation of preexisting orphan
keys. Roll out all API instances so old scripts no longer write skewed index scores.
The index/cache keys remain compatible; no migration, dependency, environment change
or mobile update is needed. Rollback is a code revert, restoring clock-skew exposure.
No production Redis inspection, key deletion or deployment verification was done.
Full native PostgreSQL/Redis suite: **1,583 passed, zero skips, 89.76% coverage**,
one unchanged upstream warning. Both hook stages and focused GPT-6.1 Sol high
review passed. Original audit evidence is retained. Publication is checked separately
from deployment. **Next:** AUD-10, bounded media grant reads and conditional claims.


### AUD-10 — batch order/proof media reads and claims

**Implemented:** 2026-10-15. **Category:** SQL optimization. **Original severity:** 3/10.

The normal order/request-photo and delivery-proof flow now loads all bounded grant
snapshots once, validates each object's actual content outside the attachment
critical section, and then loads/locks all current grants once in storage-key order.
The existing populate_existing refresh is retained. Ownership, key allowlist, avatar
expiry/storage restrictions where relevant, issued purpose, confirmed state,
unattached/not-deleting state and validated metadata are checked again before the
single conditional batch claim. Claimed keys must exactly match the requested set.
Duplicate keys and more than five keys fail before grant access; empty batches issue
no SQL. Per-file HEAD/byte/decoder validation remains necessary and was not bypassed.

The new repository operation is deliberately confirmed-only. It requires owner,
purpose, confirmed_at, unattached and not-deleting predicates and returns the keys
actually consumed. It does not reuse chat's confirm-and-claim operation, which has
different semantics, and never implicitly confirms an upload. Caller transaction
rollback still covers all claims and order/proof writes. Lock acquisition is stable,
so opposing client key order cannot invert locks. Direct unprepared service callers
use a bounded snapshot read and object validation before the fresh locking read;
there are no storage waits while those grant locks are held. Normal HTTP preparation
still releases read transactions before storage I/O and reauthenticates afterward.
Single-grant confirmation/claims and their existing avatar flow remain compatible.

Native query counting reproduced fifteen statements for five attachments before the
change. The prepared flow now uses three statements for either one or five files:
one snapshot SELECT, one ordered SELECT FOR UPDATE and one conditional UPDATE
RETURNING. The unprepared fallback likewise uses a constant bounded three statements.
This removes round-trip amplification without claiming to remove per-row PostgreSQL
constraints or audit work. No production latency/capacity measurement is claimed.
All validation loops/maps are bounded to five entries; no unbounded collection or
quadratic algorithm was introduced.

Thirty-two focused checks pass. New native regressions cover query counts at one/five,
ordered lock SQL, partial-claim rollback and refusal to confirm unconfirmed grants.
Existing stale metadata/owner/purpose/confirmation/cleanup/consumed tests now exercise
both single and batch paths. Cross-account, revoked-session, independent cleanup and
opposing concurrent key-order checks retain their assertions. Test doubles now supply
batch reads, and the pausing concurrency repository pauses after the actual ordered
lock batch; no application validation bypass or weakened assertions were introduced.

No migration, dependency, environment variable, API response/schema or mobile update
is needed. Rollback is a code revert and restores bounded per-item query cost. Full native
PostgreSQL/Redis suite: **1,594 passed, zero skips, 89.78% coverage**, one unchanged
upstream warning. Both hook stages and focused GPT-6.1 Sol high review passed.
Publication is checked separately from deployment. **Next:** AUD-11, conditional
public catalog cache assessment. Historical audit details remain intact.


### AUD-11 — assess a public catalog result cache

**Assessed:** 2026-10-15. **Category:** optional optimization. **Severity:** 2/10.
**Decision:** defer a new result cache; this was a conditional proposal, not a defect.

Source inspection confirms GET /api/cities performs one current active-city query,
ordered by name/id, without per-city database enrichment. CityService's write-time
active ID/name lookups are separate authoritative checks used by onboarding, orders
and admin operations. Those checks must not use stale cached choices. The featured
gift repository operation is already SQL-limited and has an active/display-order
index; current app source has no caller exposing that repository method as a mobile
route. No duplicate route or cache for an unused operation was added.

Read-only EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) of actual compiled queries against
the disposable test database used five samples. The existing 20 active city rows
returned in median 0.014 ms; the empty featured-gift catalog returned in 0.007 ms.
These small warm samples establish neither real traffic frequency nor large-catalog
capacity. They do not justify adding Redis reads, serialization, memory/cardinality
limits and committed-write invalidation to an already cheap single SQL operation.
The catalog has no per-row query amplification to fix.

If traffic later justifies caching, retain the original proposal: serialize a bounded
public DTO, version keys by contract and every real filter/limit, use at most about
300 seconds, invalidate committed generic-admin and catalog changes, and fall back
to SQL on cache failure. Never cache active-city write authorization. A hard payload
bound must fail safely rather than silently truncate the existing city response.
No reliable global catalog data-version invalidation currently exists; the normal
audit commit hook logs events and is not a catalog invalidation guarantee.

No runtime change, endpoint, migration, environment variable or mobile update was
introduced. This assessment does not mark an unimplemented cache as implemented,
pushed or deployed. The preceding application release remains at 1,594 passing
checks/89.78% coverage; it was not rerun for this documentation-only decision.
Both documentation/push gates and finding-link checks are required before this
assessment is published. **Next:** AUD-12, private occasion cache assessment.


### AUD-12 — assess private occasion-page caching

**Assessed:** 2026-10-15. **Category:** optional optimization. **Severity:** 2/10.
**Decision:** defer a new result cache; retain current owned SQL and fresh settings.

Saved occasion reads apply user ownership, inclusive date bounds, stable date/id
ordering and SQL limit before materialization. A cursor adds one current owned-anchor
read and preserves the same range checks. Annual occurrences use owner-scoped SQL
expansion for a validated range of at most 366 days, at most two Gregorian years,
then sort/limit before returning a page. A cursor is checked against the same owned
occurrence set. There is no per-returned-row SQL lookup or Python full-history
filtering. Editing/deleting still uses current ownership and the existing edit fence.

Actual compiled repository SQL was measured with five EXPLAIN (ANALYZE, BUFFERS,
FORMAT JSON) samples against a session-local copy of the current occasions schema
and indexes. Synthetic data contained100,000 total rows across100 owners:1,000 rows
for the selected owner, mixed one-off/annual recurrence and tied dates. The saved
week returned15 matching rows in median0.031 ms. The annual month returned26 rows
including lookahead in median1.003 ms. Raw synthetic plans remain locally in
.superpowers/audit-fix-12-plans.json. No application tables, reminder schedules or
production accounts were changed. These are warm query samples, not capacity or
end-to-end mobile measurements; larger individual histories remain unmeasured.

A one-hour private cache would add authorization/data-version coordination, payload
memory limits and invalidation for API writes, generic admin edits/deletes/ownership
changes and relevant recurrence/reminder changes. Current commit hooks log audit
metadata; they are not complete per-owner cache invalidation/version hooks. Omitting
those paths could hide edits or serve obsolete private calendar data. The measured
query cost and absent repeat-read/traffic evidence do not justify that added risk.

The original future proposal is retained in full above. Reconsider only with repeated
account/range read evidence, capped key/payload budgets, every filter/cursor/limit and
recurrence/data/auth version in keys, reliable committed-write invalidation and SQL
fallback. Fresh authorization and owned cursor checks remain required on hits; one
hour is a maximum, not permission to serve stale changed data for an hour.

No runtime cache, endpoint, migration, configuration or mobile change was added.
No unimplemented cache is described as deployed. The latest application suite is
still1,594 passed/89.78% coverage; this documentation-only assessment does not require
a new full application run. Both hook stages and finding-link checks gate publication.
**Next:** AUD-13, private order-summary cache assessment.


### AUD-13 — assess private order-summary caching

**Assessed:** 2026-10-15. **Category:** optional optimization. **Severity:** 2/10.
**Decision:** defer a new result cache; preserve fresh ownership and order state.

OrderService selects the authenticated actor's customer/courier query, preserving
courier eligibility and customer-only search. OrderRepository applies ownership,
status, inclusive delivery-date bounds and stable created_at/id keyset pagination
inside SQL before returning the bounded page. Cursor anchors are fetched through
the same filtered query, so a foreign/mismatched anchor remains a documented error.
City relationships use select-in loading, and customer rating state is fetched once
for the page and assembled through a set lookup. Couriers use a bounded false-state
map without rating SQL. There is no per-order enrichment query or quadratic loop.
Available-order eligibility and atomic acceptance remain separate authoritative
operations; notification/WebSocket messages remain hints to refresh current reads.

Actual compiled customer/courier list SQL was measured with five EXPLAIN (ANALYZE,
BUFFERS, FORMAT JSON) samples against a session-local copy of current orders,
constraints and indexes. Synthetic data had 100,000 total rows, two heavy owners
with 50,000 orders each, valid delivery windows, tied creation dates and mixed
statuses. No public application tables, accounts or financial records were changed.
The main page query selected 26 rows including pagination lookahead:

| Main order-list query | Customer median (ms) | Courier median (ms) |
| --- | --- | --- |
| No date/status filters | 0.020 | 0.017 |
| Selected week | 6.297 | 8.097 |
| Selected month | 6.169 | 7.039 |
| Selected month plus status | 6.315 | 7.239 |

Raw synthetic plans are retained locally in .superpowers/audit-fix-13-plans.json.
These samples measure the main SQL only: authentication, courier checks, cursor
lookups, select-in city loading, customer rating enrichment, network transfer and
mobile rendering are excluded. Warm local plans do not establish deployed request
latency or capacity. Date-filtered reads on this deliberately heavy owner cost more
than unfiltered chronological reads; that alone does not establish repeated identical
requests or justify a private cache.

A 15–30-second result cache requires correct invalidation for committed order/status,
assignment/participant, rating and generic-admin changes, plus active account/role
and authorization versions. Current SQL remains authoritative; there is no complete
account/filter result-cache data-version mechanism to reuse. Adding partial
invalidation could show another participant's old labels or stale status and obscure
real changes. Every search/date/status/cursor/limit combination also increases key
cardinality. No request-frequency, hit-rate, aggregate latency or memory-benefit
evidence currently outweighs those costs.

The original conditional proposal remains in full above. Reconsider after observed
repeat-account/filter traffic and measured hit benefit, with explicit bounded key/
byte budgets, committed version/invalidation paths, short TTL and safe SQL fallback.
Never let cached availability, balances, unresolved payments or summaries authorize
writes. This assessment adds no result cache, endpoint, migration, environment
variable or mobile requirement, and does not claim an undeployed cache is live.

Both hook stages, navigation checks and scope review gate this documentation-only
publication. Latest complete application evidence remains **1,594 passed, zero skips,
89.78% coverage** with the AUD-10 release; no new application edits occurred here.

### Execution outcome — 2026-10-15 continuation

All ten confirmed defects/local migration risks from the dated audit are fixed.
Each task was checked, reviewed and pushed to master before implementation of the
next began. This continuation published the following individual task releases:

| Task | Published commit | Final full-suite result |
| --- | --- | --- |
| AUD-05 — batch reminder inbox inserts | 419ab569bd4a4c892151f2537f2e4baa255d0b9d | 1,564 passed; zero skips; 89.69% |
| AUD-06 — bound retained export downloads | 113f45f566f2a44e7426bf79776752b8cc78ae52 | 1,566 passed; zero skips; 89.69% |
| AUD-07 — cache audit dropdown catalogs | 2db089c37793617110cc4180d98a6e44f1652613 | 1,577 passed; zero skips; 89.75% |
| AUD-08 — measured financial chronology indexes | 94f4bfbcfa2494cff1670a80a63a12202ef7e3d3 | 1,581 passed; zero skips; 89.74% |
| AUD-09 — Redis-time PDF eviction | 3f63188c966d75269223c722e0d9c48c36cc7ede | 1,583 passed; zero skips; 89.76% |
| AUD-10 — constant-query media claims | a98346dfe28b9b8a9f6586ab80717df52ebd6925 | 1,594 passed; zero skips; 89.78% |

AUD-01 through AUD-04 were published previously and their complete evidence remains
above. AUD-11 was assessed/pushed in 8a677e644b1c3b92955b8ad5939709afc10a63a7;
AUD-12 in 9770bfa7f8ca658269336afb7325e92fcfafbf9d. AUD-13's assessment is part of
this documentation update. These three optional caches remain deliberately deferred,
not implemented or represented as resolved runtime defects. Their proposals, query
costs, safety prerequisites and revisit conditions remain in this single document.

No HTTP/WS endpoint or response schema changed in AUD-05 through AUD-10; no mobile
code or UI-agent update is required. Migration 0036 must run through normal bootstrap
with its documented maintenance/locking precautions; migration 0035 from AUD-02 is
also part of the released chain. Push verification establishes published source,
not deployment. No Docker, production fixtures/exports, real provider delivery or
storage writes were used. Unrelated primary-workspace edits were preserved.

The five previously postponed backend items and all explicitly accepted/excluded
risks remain unchanged. In particular unrestricted admin financial/audit CRUD is
still an accepted risk, not a security fix or immutable audit guarantee. The remote
push continues to report one moderate GitHub dependency alert; reconciliation was
excluded by the user and is not claimed solved. This completes the actionable fixes
from this dated review; it is not a claim that the codebase has no possible defects
or vulnerabilities, nor a new deployed security audit.


### Cache implementation resumed — user approval supersedes deferral

**Date:** 2026-10-15. The user explicitly requested all three proposed caches after
reading the assessment. AUD-11 through AUD-13 are therefore reopened for sequential
implementation; the historical measurements and deferral rationale remain above.
No prior assessment is silently rewritten as implementation evidence.

### AUD-11 — bounded catalog cache implementation

GET /api/cities now caches the existing response DTO for 300 seconds. No endpoint,
request parameter or response field changed. The unused featured-gift repository
method has no mobile route to cache; none was invented. City and featured-gift
mutations invalidate the catalog namespace for current/future supported responses.
Active-city validation for writes still queries current PostgreSQL data.

Migration 0037 installs an internal cache-version table and catalog row/truncate
triggers. The version update commits or rolls back atomically with the catalog
change, regardless of whether the writer is API, admin, a worker or ordinary SQL.
No asynchronous Redis invalidation can be lost after a database commit. Every cache
read checks indexed current versions. There remains a small PostgreSQL version read
on hits; this is not query-free caching or a claim of improved deployed latency.
Uncommitted versions are invisible to other transactions. A new random generation
on migration upgrade prevents revival of snapshots from a previous installation.

The shared DTO cache has at most 256 entries of at most 256 KiB each (64 MiB raw
payload plus Redis/index overhead), 32 in-process coalesced fills, and a 250 ms maximum
wait before an independent SQL fallback. Only immutable serialized results are
shared, not request sessions, ORM objects or authorization decisions. Cross-replica
cold fills can repeat a bounded query. Keys hash the domain, owner/role/auth version,
all caller filters and transactional data versions. Future private caches use this
same bounded namespace; no financial/write eligibility is cached.

Redis lookups use bounded GETRANGE and 100 ms I/O budgets. Schema validation rejects
malformed content. Redis failure falls back to SQL with safe payload-free warnings;
PostgreSQL/version failure does not serve unverified cached data. A second version
check prevents publication when data changed during loading. The atomic capacity
script uses Redis TIME, and its index outlives the maximum one-hour payload lifetime,
so 30-second writes cannot orphan one-hour entries. Oversized responses are returned
normally but not cached. User text remains literal data, not rewritten or executed.

Ten focused checks pass, covering SQL query reuse, edit invalidation, rollback,
commit visibility, mixed-TTL capacity, account/filter/version separation, literal
text, outages, cancellation and malformed/oversized content. Real Alembic downgrade
to 0036 and upgrade to 0037 pass on disposable PostgreSQL. The version table is
operational cache metadata, outside the application's editable entity registry,
like Alembic metadata; it is not a new mobile or admin data feature. No secrets,
Docker, production writes, provider delivery or storage operations were used.

Rollback requires old uncached application code before downgrading 0037; the new
reader requires the version table. Redis keys expire naturally. No new environment
variable is required. Full native suite: **1,604 passed, zero skips, 89.83% coverage**, one unchanged
upstream warning. Both hook stages and focused GPT-6.1 Sol high review passed.
The final fresh-epoch migration guard was also checked by real downgrade/re-upgrade
and the ten focused checks. Publication is verified separately from deployment.
**Next:** AUD-12, one-hour private occasion pages.


### AUD-12 — one-hour private calendar caching

GET /api/occasions and GET /api/occasions/occurrences now reuse existing response DTOs
for up to 3,600 seconds. They retain the same date bounds, limits, stable ordering,
opaque/UUID cursor contracts and errors. Cached pages remain separate by saved versus
expanded view, authenticated owner, role, validated credential version, all date
bounds, limit and cursor, plus transactional owner/global data versions. Both return
private/no-store HTTP headers; Redis caching does not authorize public/client caching.
Fresh JWT/revocation/account/role checks precede cache access on every request.

Migration 0038 invalidates the old and new owners when an occasion changes, including
create/edit/delete, recurrence/reminder settings and admin ownership transfers.
As requested earlier, order changes also invalidate that customer's calendar cache.
Owner tokens rotate at commit through deferred constraint triggers. A shared
transaction advisory lock serializes only this short final invalidation phase,
preventing disjoint multi-owner batches from acquiring epoch locks in reverse order.
Business transactions must not force these constraints immediate; cached reads use
read-only transactions. Other customers' tokens are unchanged. Truncate rotates a global calendar token;
normal writes do not contend on a global calendar version row. A fresh namespace
seed prevents re-upgrade from reusing a prior installation's tokens. Metadata grows
by at most one row per historical affected customer, not per filter/date/cursor.

Reminder jobs that only update delivery progress do not change these response fields;
actual occasion/order writes trigger invalidation regardless of writer. The existing
one-off/annual semantics and reminder scheduling were not changed. PostgreSQL epoch
reads remain authoritative on cache hits. All shared storage, JSON validation,
coalescing, timeout, outage-fallback and memory bounds from AUD-11 still apply.
No financial authority or data-modification decision is read from Redis.

The two new endpoint regressions first failed because repeated calls performed two
calendar queries. After caching, identical calls use one collection query, while
foreign accounts receive their own data. Tests also verify literal text after edits,
next pages, deletion invalidating an already cached cursor, and a banned account
receiving 401 rather than its cached response. Native version checks cover owner
transfers, unaffected customers and order-created invalidation. The explicit TTL
regression requires 3,600 seconds. All 58 focused calendar/reminder/auth/cache checks
passed. A further two native regressions verify real commit/rollback/truncate
visibility and opposite-owner concurrent batches. The latter reproduced a real
deadlock before the deferred-trigger correction and passes afterward. The complete
fresh native PostgreSQL/Redis suite passed: **1,610 tests, zero skips, 89.86%
coverage**, with one unchanged warning. Both hook stages and independent review
passed. Migration 0038 downgrade/re-upgrade and all 13 finding links were verified.
The gate serializes final invalidation commits; deployment throughput is unmeasured.

No schema field, endpoint, environment variable, dependency or mobile change is
needed. Apply 0038 after 0037. Revert to uncached calendar code before removing its
triggers; otherwise future writes could stop invalidating pages. Worker/API database
identities need the version-table SELECT/INSERT/UPDATE privileges used by invoker
triggers; standard same-owner deployments already have them. No production fixtures,
notifications, storage writes or Docker were used.


### AUD-13 — short private order-summary caching

**Implemented:** 2026-10-15. **Category:** optimization. **Original severity:** 2/10.
The user's request supersedes the earlier conditional deferral; that assessment
remains above as historical evidence.

Only GET /api/orders uses the existing bounded result cache, with a 30-second TTL.
Available-order discovery, individual order details, acceptance/claim authorization,
wallets, balances and unresolved payments remain authoritative uncached operations.
The existing eligibility dependency runs before every cache access: JWT signature,
expiry/revocation, current account/role and current courier verification still apply.

Keys include owner, role, validated credential version, status, exact search text,
inclusive date bounds, limit and cursor. Current owner/global version tokens and
the catalog token cover serialized city names. Response schema, chronological
ordering, cursor semantics, customer-only search and date validation stay unchanged.
HTTP responses are private/no-store; clients and shared HTTP caches receive no
permission to retain private pages.

Migration 0039 uses deferred constraint triggers on orders and ratings, rotating
old/new participant epochs in the same commit. Rating changes resolve participants
through at most two indexed order IDs, without per-list-row calls. Old/new raters
also invalidate independently of that lookup, covering a concurrent transfer back
to a historical rater even when the participant lookup preceded the transfer commit. API, worker,
generic-admin and ordinary SQL mutations are covered. Deletes/reassignments
invalidate the losing participant, and existing empty pages for the gaining
participant also expire by version. TRUNCATE rotates the separate orders_all token.
A seeded random namespace prevents downgrade/re-upgrade from reviving stale keys.

The migration shares 0038's advisory gate for short final invalidation work.
Application transactions must retain deferred constraints until commit; cache
loaders run only on read-only GET paths. There are no provider/Redis calls while
the gate is held. This trades a serialized commit phase for deadlock-safe owner
epochs; production throughput is unmeasured. Each historical affected participant
adds at most one version row; filters and pages never create metadata rows.

All AUD-11 cache protections remain: validated serialized DTOs only, 256 shared
entries, 256 KiB per entry, bounded coalescing/waits, 100 ms Redis budgets, mixed-TTL
index lifetime and SQL fallback on malformed entries or Redis failure. Fresh SQL
epochs remain necessary on hits. No stale result authorizes a business mutation,
and no complete collection is loaded before pagination.

The initial regressions failed because identical customer/courier requests issued
two collection queries. They now issue one across the pair. HTTP regressions cover
both roles, ownership, status/search/range keys, successive pages, literal text,
city label invalidation, rating enrichment, old/new participant transfer, cached
empty results and immediate rejection after courier verification is removed.
An explicit unit regression requires the 30-second TTL and catalog dependency.
Native migration downgrade/re-upgrade passes. A historical-rater regression failed
before the correction and passes afterward; a second real two-transaction regression
forces rating deletion to wait behind ownership transfer and verifies the restored
owner's epoch changes after deletion commits. The final fresh native PostgreSQL/Redis
suite passed: **1,615 tests, zero skips, 89.88% coverage**, one unchanged warning.
Both hook stages, published mobile wire-contract checks, all 13 navigation links
and independent final review passed. Earlier full1613 evidence was superseded by
this complete run after the historical-rater fix and added concurrency regressions.
All AUD-01 through AUD-13 now have completed implementations; postponed and
accepted/excluded work remains explicitly listed, and deployment is separate.

No endpoint, payload, environment variable, dependency or mobile change is needed.
Apply 0039 after 0038 before starting the new API. Roll back to uncached order-list
code before dropping these triggers. Runtime roles need the version-table privileges
described above. No Docker, production records, provider deliveries or storage
writes were used.


### Chat authentication compatibility — 2026-10-15

**Scope:** authentication compatibility and credential-log protection only. No mobile
edits, message payload/history/event changes, duplicate-message behavior or retry
flag changes. Existing query-free authentication was already implemented in
`5c2cec4`; it was reused rather than replaced.

The route remains GET /api/ws/conversations/{conversation_id} with WebSocket
upgrade. Native Authorization Bearer without query parameters or subprotocols
works. Browser offers giftly.chat and bearer.<access token>; only giftly.chat is
selected. Legacy query credentials remain compatible. Precedence stays header
(including malformed headers), then first bearer-prefixed protocol, then legacy
query. Current JWT/revocation/account/role, conversation membership and connection
limits remain authoritative; no authentication result is cached.

One confirmed logging gap was reproduced: an opaque malformed bearer-prefixed
protocol, or a separately structured Sec-WebSocket-Protocol field, could survive
the scrubber when it did not resemble a JWT. Sensitive protocol-header key variants
now redact completely, and free-form bearer-dot values are scrubbed. This changes
logging only, not credential selection or client contracts. Regression first failed
before the fix and now passes.

Actual local Uvicorn TCP tests use dedicated disposable PostgreSQL/Redis fixtures,
short-lived synthetic credentials and the real HTTP refresh endpoint. Native
customer/courier, browser, legacy and refreshed reconnections succeed; selected
protocols are safe. Missing, malformed, expired, revoked, foreign-account,
unverified-courier and conflicting-credential connections are rejected. Existing
unit tests recheck active connections before delivery and on idle revocation.
Application diagnostics redact credentials; no real providers or production
accounts were used.

**Live evidence, separately limited:** six invalid/missing-credential TLS upgrades
against backend.giftly.sa and backend-production-vqwolm.cranl.net all returned HTTP
403. Public readiness returned 200 with PostgreSQL/Redis healthy. CranL's request
records show exactly six GET/403 entries with method/path/status/timing/host fields;
the public invalid canary is absent from those records and the 56 available runtime
log lines. This is sampled evidence, not proof of all proxy logging configuration.
The provider documentation did not establish raw ingress header/query redaction.

CranL still reports the app as deploying despite successful GitHub builds. A
read-only runtime source fingerprint command failed with unreadable output.
Therefore the running release cannot be matched to this pushed source, and no
successful authenticated deployed handshake/refresh was established. No production
fixtures or actual user credentials were created/extracted to bypass that limit.
Full proxy configuration/header forwarding and raw ingress redaction remain
UNCONFIRMED. These are verification limits, not added testing-task rows.

**Safe to enable EXPO_PUBLIC_CHAT_HEADER_AUTH=true on the verified deployment: NO.**
Activation remains blocked until authenticated native and
browser upgrades, refresh reconnect, running-release identity and proxy credential
redaction are verified on the target deployment. No recommendation to enable
EXPO_PUBLIC_CHAT_RETRY_SAFE is made.

The mobile wire schema is unchanged. api.md now documents exact precedence,
negotiation, pre-upgrade HTTP 403 versus established socket close codes, and bounded
reconnect behavior. The exact final fresh native PostgreSQL/Redis suite passed:
**1,621 tests, zero skips, 89.87% coverage**, with one unchanged warning. All 48
focused authentication/logging checks, published mobile contract drift checks,
both hook stages and independent review passed. All 13 finding links and four
maintained UTF-8 Markdown files were verified. No migration/dependency/environment
change is required for the chat patch. Source publication is checked separately
from deployment; the NO activation verdict remains in effect.

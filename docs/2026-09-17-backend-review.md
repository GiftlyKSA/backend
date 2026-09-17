# Giftly backend review — 2026-09-17

## Scope and method

Reviewed the working tree based on commit `95bc023`, including the accompanying
backend-guideline, Git-hook, payment-removal, and fresh-database migration changes.
The review criteria are [AGENTS.md](../AGENTS.md), including the
[OWASP Top 10:2025](https://top10.owasp.org/2025/). Review date: 2026-09-17,
Asia/Riyadh. Source anchors refer to the files committed with this report.

The review covered configuration, authentication/authorization, admin operations,
media, chat, payment/ledger services, repositories, migrations, workers, dependency
locks, CI, deployment configuration, tests, and documentation. Separate passes
covered security, performance/scalability, and maintainability/readability.

This report contains **15 open findings: 4 High, 10 Medium, 1 Low**. No Critical
finding was established. It records every actionable finding established during
these passes, rather than claiming every possible defect has been discovered.
Source inspection establishes the described control flow; production exploitation,
load capacity, and deployed infrastructure outside this repository were not tested.

Severity means: **High** = serious confidentiality, authentication, financial, or
operational failure under the stated conditions; **Medium** = meaningful failure
or exposure with narrower prerequisites; **Low** = maintenance/readability weakness
that makes future defects harder to prevent. Conditional exposure is stated explicitly.
All entries below are **Open** unless listed in the separate repaired-items section.

## Global priority index

Findings are ordered by severity, then assessed impact. Categories below use the
same ordering within each category. Fix plans must retain existing behavior through
small, independently verified changes; this review does not authorize a broad rewrite.

| Priority | ID | Severity | Category | Finding |
| --- | --- | --- | --- | --- |
| 1 | SEC-01 | High | Security | Concurrent refresh-token rotation can mint multiple successors |
| 2 | SEC-02 | High | Security | WebSocket connections outlive authorization and token validity |
| 3 | REL-01 | High | Reliability/scalability | Scheduled maintenance has no configured scheduling path |
| 4 | REL-02 | High, latent | Financial reliability | Failed simulated split payments strand wallet reservations |
| 5 | SEC-03 | Medium | Security | Phone changes preserve previous account sessions |
| 6 | SEC-04 | Medium | Security | Upload keys have no actor/purpose authorization |
| 7 | SEC-05 | Medium | Security | OTP verification and consumption are not atomic |
| 8 | SEC-06 | Medium | Supply-chain security | Locked dependencies have four distinct published advisories |
| 9 | REL-03 | Medium | Reliability | Expiry can overwrite a paid top-up's terminal status |
| 10 | REL-04 | Medium | Reliability | Reconciliation reads inconsistent financial snapshots |
| 11 | PERF-01 | Medium | Performance/scalability | Reconciliation retains the complete wallet population in memory |
| 12 | CI-01 | Medium | Verification | Security job does not explicitly audit the application graph |
| 13 | BUILD-01 | Medium | Reproducibility | Deployment toolchain and base images float outside the lockfile |
| 14 | MAINT-01 | Medium | Maintainability | Financial expiry rules are implemented inside a worker |
| 15 | READ-01 | Low | Readability/typing | Broad object types and ignores conceal important interfaces |

## Security

### SEC-01 — Concurrent refresh-token rotation can mint multiple successors

- **Severity/status:** High / Open. OWASP A07, A06.
- **Evidence:** [auth_repository.py:108](../app/repositories/auth_repository.py#L108)
  reads a refresh token without a row lock; `mark_refresh_used` unconditionally
  updates it. [auth_service.py:202](../app/services/auth_service.py#L202) checks that
  previously read state before issuing a successor.
- **Trigger and impact if unresolved:** two requests using the same unused token can
  both read it before consumption, then each issue a valid successor. The rotation
  mechanism fails to identify this concurrent replay and can create parallel sessions.
- **Minimal fix:** lock the token before checking state and serialize consumption,
  successor issuance, and relevant family revocation in the transaction.
- **Expected system impact:** only one rotation can succeed; replay follows the
  existing family-revocation policy. Contending refresh requests serialize briefly.
- **Verification:** coordinate two PostgreSQL sessions at the token read; assert that
  two usable successor chains cannot result. Also test expiry, revoked families,
  rollback, and normal single-request rotation. Concurrency not reproduced here.

### SEC-02 — WebSocket sessions outlive authorization and token validity

- **Severity/status:** High / Open. OWASP A01, A07.
- **Evidence:** [chat.py:301](../app/routers/chat.py#L301) checks the JTI denylist at
  handshake but not the ban flag. [courier_eligibility_service.py:25](../app/services/courier_eligibility_service.py#L25)
  checks eligibility only for courier actors. The outbound pump at
  [chat.py:224](../app/routers/chat.py#L224) does not revalidate access, while the
  inbound ban check at line 253 runs only after receiving a frame.
- **Trigger and impact if unresolved:** a banned customer can connect with a still-valid
  token and passively receive chat. An existing connection can continue receiving
  after logout, ban, or token expiry. Private content remains available to revoked sessions.
- **Minimal fix:** check ban/account status at handshake, retain token expiry, and
  enforce revocation/expiry before outgoing delivery and through bounded connection
  termination. Do not depend on the client sending another frame.
- **Expected system impact:** unauthorized sockets close promptly; shared revocation
  checks require a bounded, measured implementation across API instances.
- **Verification:** connect, revoke/ban/expire, then publish from the other participant;
  assert closure without delivery. Separately test a banned customer's handshake.

### SEC-03 — Administrative phone changes preserve old sessions

- **Severity/status:** Medium / Open. OWASP A07.
- **Evidence:** [admin_service.py:304](../app/services/admin_service.py#L304) checks
  phone uniqueness, updates the login identity, and audits the change without
  revoking refresh families or invalidating existing access tokens.
- **Trigger and impact if unresolved:** after a phone reassignment or recovery, the
  former holder of a valid session can still access the account and refresh it.
- **Minimal fix:** on an actual phone change, revoke refresh families and invalidate
  existing access tokens through a session version or revocation cutoff checked at
  authentication boundaries.
- **Expected system impact:** identity changes force reauthentication; unrelated
  profile edits should not unnecessarily sign users out.
- **Verification:** issue tokens, change the phone, reject the old access/refresh
  credentials, and confirm fresh login through the new phone succeeds.

### SEC-04 — Upload keys have no actor or purpose authorization

- **Severity/status:** Medium / Open. OWASP A01, A06.
- **Evidence:** [media_service.py:36](../app/services/media_service.py#L36) issues
  uploads without storing an owner; confirmation at line 62 validates object properties,
  not ownership. [order_service.py:113](../app/services/order_service.py#L113) and
  [fulfillment_service.py:104](../app/services/fulfillment_service.py#L104) accept keys
  without binding them to the submitting actor and intended upload purpose.
- **Trigger and impact if unresolved:** someone who knows another valid object key
  can attach that object as their own, including reusing request media as delivery
  evidence. Unpredictable UUIDs reduce discovery but are not authorization.
- **Minimal fix:** persist upload owner, purpose, and confirmation/attachment state;
  enforce actor/purpose checks and an explicit reuse policy when attaching an object.
- **Expected system impact:** prevents cross-account attachments and recycled proof;
  requires an upload-record schema and updated client attachment handling.
- **Verification:** actor B cannot confirm/attach actor A's key; request-purpose images
  cannot become delivery proof; the rightful owner's valid upload succeeds. No arbitrary
  signed-download exposure is claimed: the signing method currently has no callers.

### SEC-05 — OTP consumption is not atomic

- **Severity/status:** Medium / Open. OWASP A07, A06.
- **Evidence:** [otp_service.py:98](../app/services/otp_service.py#L98) performs a
  separate Redis read, comparison, and deletion. A local in-memory barrier test
  coordinating two calls returned **`[True, True]` for the same code**.
- **Trigger and impact if unresolved:** concurrent verification can authenticate
  twice with one challenge. A verification overlapping a new issuance can also
  delete the replacement challenge without checking its value.
- **Minimal fix:** atomically enforce attempts, compare the submitted HMAC, and consume
  only the matching challenge, using a Redis script or equivalent transaction.
- **Expected system impact:** at most one success per challenge; stale requests cannot
  destroy a newly issued code. The public successful-login response need not change.
- **Verification:** repeat coordinated verification and overlapping issuance against
  Redis in CI; assert one success and preservation of replacement challenges.
  The local reproduction proves the control-flow race, not Redis load behavior.

### SEC-06 — Locked dependencies contain published advisories

- **Severity/status:** Medium / Open; actual exploitability varies. OWASP A03.
- **Evidence:** [uv.lock:64](../uv.lock#L64) locks `aiohttp==3.14.1`;
  [uv.lock:684](../uv.lock#L684) locks `cryptography==49.0.0`. A 2026-09-17 audit of
  uv-exported production requirements returned eight records representing **four
  unique advisories**, not eight distinct vulnerabilities.

| Package | Advisory | Reported fixed version | Relevant exposure |
| --- | --- | --- | --- |
| aiohttp | [GHSA-cq5v-8q36-5273](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-cq5v-8q36-5273) / CVE-2026-69244 | 3.14.3 | Malformed HTTP responses can crash the C client parser; aiohttp is used transitively by the storage stack. Published 2026-07-25. |
| aiohttp | [GHSA-mq44-7p77-q5h7](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-mq44-7p77-q5h7) / CVE-2026-59881 | 3.14.2 | Client WebSocket decompression issue; no application use of aiohttp WebSockets was established. |
| aiohttp | [GHSA-mfx4-hv73-q22v](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-mfx4-hv73-q22v) / CVE-2026-69243 | 3.14.2 | Server WebSocket upgrade parsing issue; this backend serves through FastAPI/Uvicorn, not an aiohttp server. |
| cryptography | [GHSA-g6cj-pr64-35w5](https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5) / CVE-2026-69247 | 50.0.0 | PKCS#7 decryption oracle; the affected PKCS#7 decryption API was not found in application use. Published 2026-07-31. |

- **Impact if unresolved:** affected code remains in deployed dependency artifacts;
  the client parser issue presents a conditional availability risk. This is not
  evidence of a remotely demonstrated exploit or broken application AES-GCM encryption.
- **Minimal fix:** deliberately upgrade compatible dependency versions, review the
  transitive changes, and regenerate the lockfile. Do not silently force incompatible versions.
- **Expected system impact:** removes known affected versions; storage and cryptographic
  compatibility need regression testing, especially for the major cryptography update.
- **Verification:** rerun the production-lock audit plus storage, JWT, encryption,
  rotation, and packaging checks. No dependency upgrade was performed in this review.

## Reliability and scalability

### REL-01 — Scheduled maintenance has no configured scheduling path

- **Severity/status:** High / Open.
- **Evidence:** [docker-compose.yaml:96](../docker-compose.yaml#L96) starts the worker
  with `app.workers.broker:broker`. [broker.py:16](../app/workers/broker.py#L16) creates
  the broker without importing the task modules. No scheduler instance/service or
  task-module discovery configuration was found in the repository.
- **Trigger and impact if unresolved:** the checked-in deployment does not establish
  a path to register and enqueue cron-labelled expiry, auto-approval, receipt,
  reconciliation, and refresh-token cleanup tasks. Required maintenance may never run.
  An independently configured external scheduler was not inspected.
- **Minimal fix:** explicitly register task modules, configure the Taskiq scheduler,
  and define/document its separate process in deployment configuration.
- **Expected system impact:** scheduled maintenance starts running reliably; capacity
  and duplicate-execution safeguards must be checked before enabling multiple workers.
- **Verification:** assert registered task names, then verify each scheduled task is
  enqueued and executed in CI. Calling task functions directly is insufficient evidence.

### REL-02 — Failed simulated split payments strand wallet reservations

- **Severity/status:** High / Open, **latent financial risk confined to current
  simulation/test flows** because production payments are disabled.
- **Evidence:** [payment_service.py:347](../app/services/payment_service.py#L347)
  marks a failed callback without releasing the wallet hold.
  [expiry.py:101](../app/workers/expiry.py#L101) releases a hold only for an open
  intent, while [payment_repository.py:209](../app/repositories/payment_repository.py#L209)
  selects only `NEW` intents. A retry overwrites `invoice.amount_from_wallet`.
- **Trigger and impact if unresolved:** split payment, failure, then retry/expiry can
  leave reserved money unavailable and lose the amount attributable to the first
  reservation. Reusing this flow for a live gateway would expose real balances.
- **Minimal fix:** retain reservation ownership per intent and atomically release
  failed reservations under consistent locks; make repeated failure processing idempotent.
- **Expected system impact:** failed checkout funds become usable once, without
  duplicate release or accumulated holds on retries.
- **Verification:** exercise split failure, duplicate callback, retry, success, and
  expiry; assert exact available/held balances after every transition.

### REL-03 — Expiry can overwrite a paid top-up

- **Severity/status:** Medium / Open; the gateway race is currently a simulation/test concern.
- **Evidence:** [expiry.py:70](../app/workers/expiry.py#L70) selects expired `NEW`
  intents. The later locked operation at [expiry.py:130](../app/workers/expiry.py#L130)
  checks purpose but not the current status before marking the row expired.
- **Trigger and impact if unresolved:** a callback can commit `PAID` after candidate
  selection but before the lock. Expiry then changes that terminal status to
  `EXPIRED`, making the payment record disagree with the credited ledger.
- **Minimal fix:** under the row lock, recheck both `NEW` status and expiration before mutation.
- **Expected system impact:** paid or otherwise terminal intents remain unchanged;
  stale sweep candidates become harmless no-ops.
- **Verification:** a two-session race test settles the candidate between selection
  and lock acquisition, then asserts that both payment status and ledger remain correct.

### REL-04 — Reconciliation observes inconsistent financial snapshots

- **Severity/status:** Medium / Open.
- **Evidence:** [money_service.py:497](../app/services/money_service.py#L497) reads
  balances and ledger sums in separate statements. [db.py:27](../app/core/db.py#L27)
  does not configure an isolation override; PostgreSQL normally uses READ COMMITTED.
- **Trigger and impact if unresolved:** a valid posting between the reads can compare
  old balances with new ledger totals and report false financial drift. Repeated false
  alarms erode trust in reconciliation and can cause unnecessary incident response.
- **Minimal fix:** compare within one consistent snapshot, using a single SQL
  statement or an appropriately scoped repeatable-read transaction.
- **Expected system impact:** removes this source of false alerts; avoid holding a
  long-running snapshot unnecessarily while resolving PERF-01.
- **Verification:** coordinate a balanced concurrent posting between reads; no drift
  should be reported. Actual deployment isolation settings remain unverified.

## Performance

### PERF-01 — Reconciliation memory scales with the entire wallet population

- **Severity/status:** Medium / Open.
- **Evidence:** [wallet_repository.py:142](../app/repositories/wallet_repository.py#L142)
  materializes every wallet, then line 146 builds a complete per-wallet aggregation
  dictionary. [money_service.py:497](../app/services/money_service.py#L497) retains both.
- **Trigger and impact if unresolved:** growing account/ledger populations increase
  worker memory and reconciliation duration without a bound, potentially causing
  out-of-memory failures or delayed maintenance. No production threshold was measured.
- **Minimal fix:** return mismatches/counts from SQL or use bounded batches under a
  consistent snapshot; coordinate with REL-04.
- **Expected system impact:** memory becomes proportional to bounded batches or
  returned discrepancies rather than all wallets. Benchmark latency rather than assuming improvement.
- **Verification:** representative large test data, peak-memory measurement, and
  identical correct/incorrect-ledger outcomes before and after the change.

**N+1 review:** no additional endpoint N+1 defect was established in these passes.
The reconciliation code already batches ledger sums instead of making one query
per wallet, but still has the unbounded-memory issue above. This is not a blanket
guarantee: query-count tests and representative query plans remain necessary for
future list/serialization changes.

## CI and build reproducibility

### CI-01 — The security job does not explicitly target application dependencies

- **Severity/status:** Medium / Open.
- **Evidence:** [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs
  `uvx pip-audit --strict` after syncing the project. The isolated tool invocation
  supplies neither exported project requirements nor the project interpreter.
- **Impact if unresolved:** the gate can inspect the audit tool's environment rather
  than the backend's actual runtime graph, giving misleading assurance.
- **Minimal fix:** export locked production requirements with uv and explicitly
  pass them to a pinned audit tool; separately decide whether to audit development dependencies.
- **Expected system impact:** CI checks the distributions actually selected for
  deployment; known findings such as SEC-06 become visible instead of being missed.
- **Verification:** compare audited names/versions to the production export, including
  aiohttp and cryptography. Root's independent audit used an explicit requirements file.

### BUILD-01 — The deployment toolchain floats outside the lockfile

- **Severity/status:** Medium / Open.
- **Evidence:** [Dockerfile:8](../Dockerfile#L8) copies `ghcr.io/astral-sh/uv:latest`;
  lines 5 and 26 use mutable `python:3.13-slim` tags.
- **Impact if unresolved:** rebuilding the same application revision can incorporate
  unreviewed base/toolchain changes; `uv.lock` does not make the full image reproducible.
- **Minimal fix:** pin reviewed tool and base-image versions/digests with a deliberate
  update process rather than permanently freezing security updates.
- **Expected system impact:** reproducible inputs and reviewable upgrades; application
  behavior should remain unchanged after validation.
- **Verification:** CI builds, checks non-root execution and packaging/runtime behavior,
  and records image provenance. No local Docker build was performed after it was prohibited.

## Maintainability, readability, naming, and style

### MAINT-01 — Financial expiry rules live inside a worker

- **Severity/status:** Medium / Open.
- **Evidence:** [expiry.py:92](../app/workers/expiry.py#L92) directly releases financial
  holds/promotions, changes invoice/payment status, and resets order state/amount.
- **Impact if unresolved:** policy changes must coordinate worker code with payment
  and invoice services; alternate callers cannot reuse one service-owned rule.
  This creates divergence risk at financial state boundaries.
- **Minimal fix:** extract the existing transaction-level expiry operation into a
  service, keeping task scheduling, batches, and transaction orchestration in the worker.
- **Expected system impact:** one reusable domain operation with no intended external
  behavior change; preserve lock ordering and transaction boundaries during extraction.
- **Verification:** existing expiry coverage plus repeat/idempotency and failure cases
  for holds, promos, invoice state, and order state.

### READ-01 — Broad object types and ignores conceal real interfaces

- **Severity/status:** Low / Open; affects maintainability and readability as well as typing.
- **Evidence:** [invoice_repository.py:74](../app/repositories/invoice_repository.py#L74)
  accepts `line: object` and suppresses eleven attribute reads. Its import-cycle
  comment is misleading: the same module already imports `PricingResult` from the
  module defining `PricingLine`. Workers use `object` sessions/factories plus ignored
  operations, for example [expiry.py:48](../app/workers/expiry.py#L48) and line 92;
  [chat.py:236](../app/routers/chat.py#L236) follows the same pattern.
- **Impact if unresolved:** strict mypy can pass while incompatible objects reach
  financial persistence/background code. Readers must reconstruct the expected contract
  from suppressed accesses and stale explanatory comments.
- **Minimal fix:** use `PricingLine`, `AsyncSession`, and `async_sessionmaker[AsyncSession]`
  or a precise callable protocol; remove corresponding ignores and the inaccurate comment.
- **Expected system impact:** clearer interfaces and earlier detection of incompatible
  callers; no intended runtime behavior change or broad refactoring.
- **Verification:** strict mypy and affected invoice/worker/chat tests; invalid arguments
  should fail type checking rather than runtime execution.

**Formatting/naming assessment:** Ruff lint/format and mypy pass. No independent
naming-only defect with concrete impact was established. Do not create cosmetic
rewrite work merely to populate a review category; use the approved naming and
minimal-comment rules for future changes.

## Intentional limitations and contract decisions

- Dhamen is selected but not implemented as a live client. Production top-ups,
  invoice payments, and direct payment-webhook processing fail closed with
  `PAYMENTS_DISABLED`; simulation routes are not registered in production. This is
  explicitly approved behavior, not an unexpected outage finding.
- Logout currently revokes the presented access-token JTI, not every refresh token
  or device session. That is the implemented contract. Product-level expectations
  for full-session logout should be decided before changing it; SEC-02 still applies
  because a revoked access token should not retain an open receiving socket.
- Migration history cleanup was authorized because the owner confirmed no existing
  database needs compatibility. It must not be applied as an upgrade to an older
  deployment. No production database was modified.
- The old static documentation deletion was explicitly approved. CI now publishes
  generated OpenAPI as an artifact; this report is newly requested documentation.
- Production provider calls, production infrastructure, load thresholds, and externally
  provisioned scheduler/alerting/secret-management controls remain **UNCONFIRMED**.

## Repairs made while implementing the requested setup

These items are separate from the 15 open review findings:

| Item | Change | Evidence/status |
| --- | --- | --- |
| Backend development contract | Added approved AGENTS.md, OWASP controls, Python conventions, minimal-change/comment rules, and no-local-Docker rule | Source/document review complete |
| Local quality gates | Added pre-commit/pre-push configuration and EditorConfig; locked hook dependencies; aligned CI quality commands and master PR target | Installed both hooks; local hook checks pass |
| Production payment suspension | Removed retired live client/config; added disabled client and service guards before effects | Regression checks first failed, then passed; route/fake guards covered |
| Generic payment persistence | Replaced provider-specific runtime fields with generic checkout fields and cleaned fresh-install history | Fresh upgrade succeeded before Docker prohibition; final database rerun pending CI |
| Enum rollback constraint | Drop/recreate the media proof constraint around enum replacement | Earlier DB run exposed failure; offline SQL regression failed before fix and passes after; final live rollback pending CI |
| Test fixtures | Corrected obsolete fixed table count, wallet insertion before owner ID, and missing verified courier fixture | Final database-backed rerun pending CI; assertions/authorization not weakened |
| Simulator lookup scope | Scoped simulated checkout lookup to its provider discriminator | Static review; database-backed regression rerun pending CI |

## Verification record and remaining checks

- **Pass:** 136 unit tests, including production-disablement, route registration,
  and offline migration SQL regression tests. Executed with explicit dummy test
  environment values using the project's uv environment; no real credentials used.
- **Pass:** Ruff lint, Ruff format, strict mypy over 129 source files, pre-commit
  config validation, configured pre-commit/pre-push checks, and `git diff --check`.
- **Pass:** parsed 187 Python files; scanned application/tests/configuration and
  README/AGENTS/environment template with zero retired-provider reference matches.
- **Earlier database run:** clean migration upgrade succeeded and the full suite
  reached **86.41% coverage**, but **five tests failed**. These failures identified
  the stale fixtures and rollback constraint repaired above. That run is **not** a
  passing final full-suite result.
- **Not rerun locally:** the database-backed suite, final live migration
  upgrade/downgrade, and Docker build. The user prohibited local Docker after the
  earlier run; Docker was confirmed not running and was not restarted. CI is
  configured with a disposable migration database for the remaining checks.
- **Security audit:** exported production dependencies with `uv export --locked
  --no-dev --no-hashes`, then ran `uvx pip-audit --requirement <export> --no-deps
  --disable-pip --format=json`. Four distinct advisories remain open (SEC-06).
- **Reproduced:** OTP double-success through an in-memory concurrency barrier.
  Other concurrency findings are source-established risks awaiting their specified
  PostgreSQL/Redis/WebSocket integration regressions.

Resolve High findings before treating the backend as production-ready. Preserve
the production payment block until the Dhamen protocol and financial state paths
are implemented, reviewed, and verified. Do not confuse passing lint/unit checks
with closure of these findings or successful production acceptance.

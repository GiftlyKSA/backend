# Giftly codebase review

**Reviewed:** 2026-10-08 · **Scope:** remaining confirmed findings and combined changes against `4a10919`.

[Documentation](documentation.md) · [API contract](api.md) · [Outstanding tasks](tasks.md)

Security comes first, followed by application/SQL efficiency, memory/CPU use and maintainable scalability. Scores are priority estimates, not CVSS: High 7–10, Medium 4–6, Low 1–3. Implementation, local verification, publication and deployment are separate states. This review does not certify that the system has no vulnerabilities.

## Global priority index

| Priority | Finding | Category | Severity | Current disposition |
| --- | --- | --- | --- | --- |
| Policy | SEC-16 | Financial integrity | High 9/10 | Accepted: unrestricted authenticated admin financial CRUD retained |
| Policy | SEC-17 | Audit integrity | High 9/10 | Accepted: unrestricted authenticated admin audit CRUD retained |
| Excluded | SEC-19 | Secret configuration | High 7/10 | Deferred by explicit user instruction; public test-secret rejection not implemented |
| P1 | FIN-01 | Discount allocation | High 7/10 | Fixed; exact cent allocation and bounded shares |
| P1 | FIN-02 | Wallet reservations | High 7/10 | Fixed; freshly locked funds and unrelated holds preserved |
| P2 | SEC-18 | Authentication | Medium 6/10 | Fixed; refresh replay commits account-wide credential revocation |
| P2 | PERF-10 | CPU/memory and cancellation | Medium 6/10 | Fixed; isolated image decoding and owned child cleanup |
| P2 | REL-12 | Receipt delivery | Medium 6/10 | Fixed; eligible email candidates selected before LIMIT |
| P2 | PERF-11 | Push fanout | Medium 5/10 | Fixed; serialized ten-device quota and bounded pages |
| P2 | REL-13 | Chat recovery | Medium 5/10 | Fixed; scoped send identity and durable fenced live retries |
| P2 | API-02 | Pagination | Medium 4/10 | Fixed; malformed nonempty inbox cursors return 400 |
| P2 | REL-14 | Failure handling | Medium 4/10 | Fixed; correlated protected safe 500 responses |
| P3 | QUAL-02 | Input consistency | Low 3/10 | Fixed; REST/WebSocket share the typed send schema |
| P3 | QUAL-03 | Readability | Low 2/10 | Fixed; obsolete design citations removed from application source |
| Gate | SEC-15 | Credential transport | Medium 5/10 | Mitigated in source; ingress redaction remains UNCONFIRMED |
| Gate | SQL-01 | Capacity measurement | Medium 5/10 | Partially measured; representative deployment load remains open |
| Gate | PERF-09 | Audit retention | Medium 5/10 | Policy and independent archive validation needed |
| Gate | SEC-20 | Supply chain | Medium 5/10 | Current remote alert disposition remains UNCONFIRMED |
| Gate | OPS-01 | Runtime/deployment | Medium 5/10 | Full service/native media/CI and public rollout validation needed |
| Gate | INT-01 | Vendor contracts | Medium 5/10 | External validation; production provider integration not certified |

## Security and authentication

### High: accepted risks and explicit exclusion

**SEC-16 — Privileged financial maintenance (9/10, accepted).** `app/services/admin_table_service.py`, `app/repositories/admin_table_repository.py` and migration `0002_schema_guards` retain the user-approved maintenance override. An authorized mistaken or compromised administrator can alter posted financial history. Authentication and CSRF cannot prove accounting correctness. Preserve the requested CRUD; restrict administrative access operationally and use independently reviewed reconciliation and backup recovery. An immutable ledger maintenance policy requires a new product decision.

**SEC-17 — Mutable audit history (9/10, accepted).** Generic admin CRUD still includes audit logs. Changes to audit rows do not recursively audit themselves. A privileged actor can erase investigation evidence. An independently secured archive would preserve evidence after local edits or deletions; its delivery, restoration and access controls remain unverified. This risk is not reported as fixed.

**SEC-19 — Public testing secrets accepted in production (7/10, deferred).** `app/core/config.py` validates key sizes and configuration interlocks without rejecting exact published test values. Copying public test secrets into a real environment can permit credential forgery or exposed encrypted data. The user explicitly excluded the proposed rejection rule. Development examples and secret checks were not changed; no real deployment secrets were inspected. Replace testing values through the protected deployment secret store before real use. Safe rotation requires a separately reviewed ciphertext/session plan.

### Medium: fixed and remaining validation

| ID / score | Evidence and original impact | Minimal change and system effect | Verification |
| --- | --- | --- | --- |
| SEC-18 / 6 | `app/services/auth_service.py:240` previously revoked only one refresh family; minted access JWTs remained valid after detected replay. | Reuse existing credential invalidation under User → RefreshToken locks, advance auth_version, revoke refresh/admin sessions and commit before 401. All account devices must authenticate again. Current HTTP/socket account checks reject old credentials. | Offline access checks and PostgreSQL replay/rotation/session tests; explicit denial survives rollback of the outer error response. |
| SEC-15 / 5 | Legacy query JWTs can appear in ingress logs. Actual credential capture is UNCONFIRMED. | `app/routers/chat.py:612` prefers Authorization Bearer, then `bearer.JWT` subprotocol; legacy query remains compatible. Only `giftly.chat` is echoed as negotiated protocol. Production origins are checked when supplied; native clients may omit Origin. | Transport/precedence/auth checks; do not claim upstream query/header redaction has been verified. Next: fake-token probes at every ingress boundary, without real credentials. |
| SEC-20 / 5 | Earlier pushes reported GitHub alerts, but authenticated alert metadata was unavailable. A previous complete locked-graph audit was clear after targeted patches. | Reconcile each remote advisory with the exact current master lockfile/image, and apply only relevant reviewed version changes. No broad dependency churn or suppression. | Earlier audit evidence is dated 2026-10-04, not a current remote-alert clearance. CI and repository security-maintainer confirmation remain required. |

The device and chat review also reproduced concurrent account changes. Device registration now checks current locked role/status before token mutation. Chat checks current marketplace eligibility before reads/decryption and again after acquiring the conversation lock. Delayed workers cannot decrypt/publish for inactive senders. PostgreSQL barrier tests reproduce a committed ban between the initial check and lock acquisition.

## Financial correctness

### High: fixed

| ID / score | Trigger and impact if unresolved | Fix and expected system impact | Evidence / verification |
| --- | --- | --- | --- |
| FIN-01 / 7 | Twenty SAR 0.01 items with a 50% discount produced a negative final component discount under residual-to-last rounding. Line prices became misleading despite a correct aggregate. | `app/core/pricing.py:195` uses integer-cent largest remainders with deterministic component-order ties. Every allocation stays within its component and the exact sum equals the discount. Decimal serialization and aggregate pricing policy remain unchanged. No historical invoices are silently rewritten. | Tiny/fixed/percentage/full discounts, zero courier fee and exact reconstruction regressions; pricing and invoice integration checks. Existing issued/paid malformed history, if any, needs an explicitly scoped reconciliation. |
| FIN-02 / 7 | A payer's available balance could be read before another request reserved those funds, then incorrectly fund another invoice. | `app/services/money_service.py` checks gross debits against fresh locked available balances, consumes only the operation's own hold atomically and preserves other holds. Sorted wallet/ledger locking and under-lock replay checks retain double-entry and idempotency guarantees. | PostgreSQL two-session race: one request reserves SAR 630 while another tries a stale SAR 630 wallet payment; the second is rejected and the first hold, invoice and order state remain intact. Unit debit/hold and integration reconciliation tests. |

No cached authorization, available balance, unresolved payment state or client-calculated total authorizes a mutation. Monetary API values remain decimal strings. No new financial schema migration or production data correction is introduced by FIN-01/FIN-02.

## Performance, SQL, memory and CPU

### Medium: fixed and measurement limits

| ID / score | Original cost or risk | Change and expected effect | Evidence / limitation |
| --- | --- | --- | --- |
| PERF-10 / 6 | Cancelled Pillow threads continued consuming CPU after admission was released, allowing overlapping decoders. Native subprocess output could also block cleanup when pipes filled. | `app/services/image_decoder.py` performs complete validated JPEG/PNG decode in an isolated child. `chat_media_validation.py` owns spawn, bounded private files, 20-second deadline and 64 KiB output limits; cancellation drains writes and kills/reaps the child before releasing admission. Cleanup drains both output pipes after cancelling readers. | Native valid/misdeclared image and oversized two-megabyte output regressions; cancellation during process creation, repeated cancellation and output cleanup tested. Pixel limit remains 20 million. Python allocation limits are not a production RSS/cgroup guarantee. Native ffmpeg/ffprobe and concurrent deployment load remain open. |
| PERF-11 / 5 | Unlimited registrations made recipient snapshots and push work grow without an account bound; generic fanout loaded complete token collections. | `app/services/device_service.py` serializes destination quota under the user lock: at most ten active devices, including safe token reassignment. Existing token refresh at ten succeeds; an eleventh returns 409 DEVICE_LIMIT_REACHED. `notification_service.py` pages generic fanout at 500. | PostgreSQL last-slot concurrency, foreign deletion/reassignment, rejected account changes and legacy 501-device paging tests. Provider invalid-token feedback is not implemented: do not delete valid tokens based on an invented age rule. Existing legacy over-quota accounts can unregister owned tokens. |
| SQL-01 / 5 | Dataset/pool/snapshot costs need representative measurement; local tests alone cannot establish capacity. | Constant-query projections and five measured indexes in migration0025 are already released. Preserve bounded keysets and exact aggregates rather than blanket caching. Measure pool waits, p95/p99 latency, audit/snapshot writes and decoder RSS/CPU before increasing capacity. | Synthetic PostgreSQL plans and query counts exist; real distribution, multi-worker Redis/socket load and production pool headroom remain unverified. |
| PERF-09 / 5 | Committed-row auditing, recipient snapshots and delivery state add storage/index/backup write amplification. | Retain action-only metadata auditing. Agree retention and independent archive access/restore policy before implementing bounded archival; do not silently purge records. Existing protected database export is not proof of a running independent archive. | Source growth path confirmed; production pressure, approved retention and archive restoration remain unverified. New chat delivery CRUD adds one metadata audit row per actual mutation. |

### Previously released API optimizations

| IDs | Current result | Cost / remaining verification |
| --- | --- | --- |
| AP-P01 / AP-P02 | Separate bounded HTTP/security and WebSocket Redis pools; one grant-preparation query and three batch attachment persistence queries for 1–5 objects. | Live Redis reconnect/load and complete service suite remain required. |
| AP-P03 / AP-P04 / AP-P05 | 64 KiB recording streams, one fresh joined chat-monitor query, scalar eligibility/keyset projections without unused city loading. | Synthetic 120 MiB stream used under 1 MiB traced Python allocation; cursor pages keep three queries at 1/100 rows. These are not process RSS or production throughput claims. |
| AP-P06 / AP-P07 | Rating/statement alternatives measured; exact aggregates retained because candidate indexes did not provide consistent benefit. | No stale financial-summary cache. Revisit only with measured deployment latency and transactional invalidation. |
| AP-P08 / AP-P09 | Migration0025 released: two owner/date indexes and three partial payment-priority indexes. | Synthetic day lookup about 29 ms → 0.07–0.09 ms; repeated automatic prepared recovery 0.010–0.013 ms, forced-generic 14.5–24.7 ms. Write medians per 1,000 rows: orders 12 → 27.9 ms; payments 4.95 → 7.13 ms. Measure broader ranges/status and real writes before claiming universal speedup. |
| AP-P10 | Bounded identical PDF-render coalescing, current ownership/content fingerprints and one-hour cache. | Per-worker map 128 and four render slots; cross-worker bursts and real Redis measurements remain open. |

No unbounded quadratic loop was confirmed in the inspected mobile read paths. This is a scoped observation, not a whole-program complexity guarantee.

## Reliability and API behavior

### Medium: fixed

| ID / score | Trigger and impact | Fix and system effect | Verification |
| --- | --- | --- | --- |
| REL-12 / 6 | The oldest 100 paid invoices without customer email repeatedly filled receipt sweeps and starved reachable recipients. | `app/repositories/invoice_repository.py:287` joins the owner and filters nonempty email before LIMIT. No-email invoices are not falsely marked sent; adding an email makes them eligible later. Fenced receipt claims/provider idempotency remain intact. | PostgreSQL 100-missing-email plus reachable recipient, stable ordering and later-email eligibility tests. Live email acceptance remains INT-01. |
| REL-13 / 5 | Committed chat sends could be duplicated after uncertain retries and live fanout could disappear during Redis/API failure. | Optional client_message_id UUID is unique within conversation + sender; replay returns the original message only for identical type/text/ordered attachment keys. A metadata-only live outbox persists with the encrypted message. Fenced 30-second leases, five-second publish timeout, at most eight attempts and bounded minute sweeps recover temporary failures. | PostgreSQL concurrent replay, changed-payload conflict, used-upload retry, account-ban lock races, lease/retry exhaustion, migration rollback and action-audit rollback tests. Only authorized active senders are decrypted. Delivery is at least once, not offline retention or exactly once; clients deduplicate by message ID and reconcile history on reconnect. |
| API-02 / 4 | A malformed nonempty inbox cursor silently restarted page one, hiding synchronization mistakes. | `app/routers/chat.py:643` validates bounded timezone-aware timestamp + UUID and returns 400 BAD_REQUEST for invalid input. Empty/omitted cursor and existing deterministic keysets are unchanged. | Invalid separators/date/UUID/naive timestamp, empty and valid aware cursor tests. Clients reset a bad cursor explicitly. |
| REL-14 / 4 | Unhandled exceptions lost request_id and protective response headers. | `app/core/middleware.py` renders safe pre-response failures inside the protective ASGI boundary, retains request state and correlation, and resets contexts in finally. Cancellation and already-started streams are not converted into a second response. Logs contain safe exception type and request ID, not exception text/secrets. | Synthetic 500 envelope, security/CORS headers, redaction, response-start and cancellation tests. Upstream logging remains SEC-15. |

**Migration0026:** adds nullable legacy-compatible messages.client_message_id, its scoped unique constraint and chat_live_deliveries with a pending index and existing metadata audit trigger. Legacy messages are not retroactively broadcast. Rollback keeps message IDs/ciphertext but removes retry identities and pending delivery metadata; stop publishers/workers and review pending work before rollback. This adds indexed writes and audit rows. New server/worker code requires migration0026 before traffic.

## Quality, readability and maintainability

### Low: fixed

| ID / score | Evidence / impact | Minimal change | Verification |
| --- | --- | --- | --- |
| QUAL-02 / 3 | Socket coercion accepted null/object values and overlong text rejected by REST, making validation inconsistent. | `app/routers/chat.py` validates with SendMessageRequest, preserves literal code/whitespace, and emits safe VALIDATION_ERROR frames without persistence or closing the connection. Existing byte/rate guards remain. | Null/object/4,001-character negative sends followed by a valid send; one valid commit and retained socket. HTTP invalid bodies remain 422. Rendering remains escaped text; input content is not executed. |
| QUAL-03 / 2 | Application comments/docstrings cited removed SPEC/ADR/decision documents and could mislead maintenance. | Removed stale citations and stated current invariants; most affected files change comments/docstrings only. Updated refresh-replay, plain-text order description and privileged ledger-maintenance explanations. | Citation scan and runtime AST comparison on the 89 citation-only files; Ruff and strict typing validate combined source. Applied migration history was not rewritten. |

## Verification and release gates

Focused regression failures were reproduced before fixes. Independent whole-change review inspected pricing/holds, replay/session locks, current eligibility, outbox idempotency/privacy/fencing, migration compatibility, cancellation ownership, cursor/input boundaries and admin table integration. Selected review tests passed; no concrete introduced blocker remained in that scope.

Native PostgreSQL 16.15 was run only in a disposable loopback temporary cluster. Migration0025 upgrade/downgrade/re-upgrade and migration0026 transactional roundtrip preserve committed messages. Focused financial, account-ban, device quota, receipt, chat recovery and paging tests passed. No Docker, real provider delivery, production writes or deployment-secret inspection was performed.

Checkpoint verification: full unit suite passed. Both all-file pre-commit and pre-push gates passed, including Ruff and strict mypy over 218 application files; generated OpenAPI drift/export checks passed. Independent combined review ran 72 selected checks without a blocker. Focused PostgreSQL follow-ups passed: 86 financial/expiry/mobile checks; 24 legacy finance/eligibility checks; 23 chat/recovery/media checks; six audit/rotation/catalog checks; ten receipt/admin checks with one explicit missing-Redis skip. These sets overlap and must not be added into a claimed unique total.

The initial broad PostgreSQL run finished with **1,140 passed, 49 skipped and 47 failed**: 32 depended on unavailable Redis; 15 exposed outdated expectations or fixture isolation/coding issues. The relevant fixtures were repaired while preserving authorization, ledger, rollback and audit assertions. An offline rerun exposed six standalone chat tests missing the standard database-availability guard; that guard was then added. The final offline aggregate rerun passed: **983 passed, 258 skipped, four upstream TestClient warnings** (`uv run --locked pytest -n 4 -o addopts="" -q`, unavailable-service mode). Complete PostgreSQL/Redis CI, live native ffmpeg/ffprobe and the 85% coverage gate are not claimed green. Service tests were not replaced with fakes merely to pass.

**OPS-01 / Medium 5:** verify complete disposable PostgreSQL + Redis CI, native ffmpeg/ffprobe, Python3.13 image startup, scheduler/worker recovery and public route/schema readiness. Database migrations cannot prove a new image is deployed.

**INT-01 / Medium 5:** verify SMS/email/push vendor sandbox contracts and Dhamen callback authentication/merchant flow before enabling live payments. Mock success is not live success. Vendor-contract work remains external scope.

**Deployment:** last recorded CranL rollout failed with missing DHAMEN_APP_ID while Dhamen was selected; the old service stayed healthy and new payment-session routes returned routing404. Current deployment is UNCONFIRMED. Do not change provider selection or credentials without the required operational approval. Source/pushed contract and deployed contract must be checked separately.

Previous body-size/rate-limit/access-cursor/media-lock/recipient-snapshot/profile/identity/doc-cleanup fixes remain in source. VAT is fully removed; item prices are final, service/courier fees remain. Encrypted identity numbers are retained per policy; fingerprints and duplicate checks are removed. User-approved admin CRUD risks are not silently reversed.

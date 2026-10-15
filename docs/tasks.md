# Giftly backend — audit and task register

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
| [AUD-01](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:72) | Serialize courier verification resubmission | security | **6** | 7 | Fixed - full suite and security review verified |
| [AUD-02](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:93) | Use a forward migration for local reward enums | opt | **5** | 6 | Fixed - forward migration and rollback verified |
| [AUD-03](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:114) | Fully decode order request and delivery images | security | **5** | 5 | Fixed - bounded full image decoding verified |
| [AUD-04](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:135) | Release invoice PDF read connections before rendering | perf | **5** | 5 | Fixed - immutable PDF snapshot releases DB connection |
| [AUD-05](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:156) | Batch due-reminder inbox inserts | opt | **4** | 4 | Fixed - one bounded conflict-safe inbox insert |
| [AUD-06](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:177) | Bound retained database export downloads | perf | **4** | 4 | Fixed - shared retained-download admission and deadline |
| [AUD-07](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:198) | Cache audit dropdown catalogs safely | opt | **4** | 3 | Fixed - bounded 30-second category label cache |
| [AUD-08](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:219) | Support chronological financial admin browsing | opt | **4** | 3 | Proposal - index mismatch confirmed; benefit unmeasured |
| [AUD-09](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:240) | Use Redis time for PDF cache eviction | perf | **3** | 3 | Open - native Redis clock-skew reproduction |
| [AUD-10](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:261) | Batch order/proof media grant reads and claims | opt | **3** | 3 | Open - 3N calls reproduced at N=1 and N=5 |
| [AUD-11](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:282) | Consider a small public catalog cache | opt | **2** | 2 | Optional proposal - not a confirmed defect |
| [AUD-12](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:303) | Consider private occasion-page caching | opt | **2** | 2 | Optional proposal - benefit unmeasured |
| [AUD-13](C:/Users/PC/Desktop/giftly/backend/docs/tasks.md:324) | Consider short-lived private order-summary caching | opt | **2** | 2 | Optional proposal - benefit unmeasured |

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
| Status | Proposal - index mismatch confirmed; benefit unmeasured |

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
| Status | Open - native Redis clock-skew reproduction |

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
| Status | Open - 3N calls reproduced at N=1 and N=5 |

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
| Status | Optional proposal - not a confirmed defect |

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
| Status | Optional proposal - benefit unmeasured |

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
| Status | Optional proposal - benefit unmeasured |


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
| BANK-PROFILE | Saved courier bank profile | Define validation/masking/verification, then implement private saved details. | Postponed | 4 | 2026-10-07 | 2026-10-14 |
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


### AUD-05 � batch due-reminder inbox inserts

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


### AUD-06 � bound retained database export downloads

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


### AUD-07 � cache audit dropdown catalogs safely

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

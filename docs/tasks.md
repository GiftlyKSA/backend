# Giftly task tracker

**Created:**2026-10-11 · **Updated:**2026-10-13.
Source: codebase_review.md. Status distinguishes source, publication, deployment and
mobile implementation ownership. Scores indicate urgency, not CVSS.

## Work ownership - updated 2026-10-13

This chat handles backend implementation, contracts, documentation and backend release
verification only. Mobile implementation belongs to the separate mobile chat. Do not edit
the mobile repository here. UI-01 through UI-04 are complete according to the user's mobile commit 5501a79 report.
This chat did not edit or independently verify that mobile commit.

Verification-only tasks were removed at the user's request on 2026-10-13, including
environment-dependent, Docker/CI, provider and performance validation. Removal does not
mean those checks passed; evidence and limitations remain in codebase_review.md.
Implemented backend features must not be reopened solely because mobile wiring is pending.

## Completed backend capabilities

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| MOB-05 | Courier reports | Settled payout including reimbursement, charts/comparison and optimistic targets | Deployed; authenticated empty-ledger/settings/role checks passed | High7 | 2026-10-07 | Riyadh/Sunday weeks, maximum366days; no unsupported financial switch |
| MOB-06 | Inbox and timeline | Owned paginated notifications/read actions and actual order transitions | Deployed; ownership/read/pagination checks passed | High7 | 2026-10-07 | Older orders explicitly incomplete; foreign access denied |
| MOB-07 | Occasion reminders | One-off and annual reminders at 09:00 Asia/Riyadh with durable dispatch | Implemented and deployed; worker running | Medium5 | 2026-10-07 | Actual Expo device delivery remains unverified |
| MOB-09 | Phone change | Current-phone reauth,new-number verification and credential revocation | Deployed; authenticated proof/change/revocation checks passed | High8 | 2026-10-07 | Scoped60secondOTP; single-use300secondproof; fresh login required |
| MOB-10 | Account requests |14-day recovery,then admin review,durable detailed email | Implemented; worker running | High8 | 2026-10-07 | No automatic permanent erasure/refund; retention/completion policy unresolved |
| MOB-11 | Own-order search | Literal bounded description prefix/exactUUID,existing status/date/cursor | Deployed; ownership/cursor/customer-only checks passed | Medium5 | 2026-10-07 | Index/custom/automatic/generic/write measurements recorded |
| OPS-01 | Isolated worker | Separate worker/scheduler Dockerfiles,single scheduler,two concurrent tasks | Deployed; health and queue execution verified | High7 | 2026-10-04 | Riyadh/shared DBRedis; independent apps; keep one scheduler |
| UI-01 | Mobile integration | Add courier-home earnings, customer q search and actual timeline to existing integration | Completed in mobile chat (user-reported 5501a79) | High7 | 2026-10-11 | User report 2026-10-13; no mobile edits in this chat |
| UI-02 | Courier home earnings | Wire week/month cards to existing earnings reports; reuse helpers, never wallet balance. | Completed in mobile chat (user-reported 5501a79) | Medium5 | 2026-10-12 | User report 2026-10-13; no mobile edits in this chat |
| UI-03 | Customer order search | Add bounded q search; preserve date/status filters and reset cursors. | Completed in mobile chat (user-reported 5501a79) | Medium5 | 2026-10-12 | User report 2026-10-13; no mobile edits in this chat |
| UI-04 | Historical order timeline | Load participant timeline with pagination and history_complete warning. | Completed in mobile chat (user-reported 5501a79) | Medium5 | 2026-10-12 | User report 2026-10-13; no mobile edits in this chat |
| MOB-13 | Annual recurrence | Opt-in annual calendar feed and version-fenced reminders; leap years only | Deployed; runtime contract confirmed | Medium5 | 2026-10-13 | Existing one-off CRUD preserved |
| MOB-14 | Statement PDF | Complete owned informational snapshot, literal descriptions and Arabic rendering | Implemented, pushed and deployed in dadc421; runtime contract confirmed | Medium5 | 2026-10-13 | 5000 rows, 250 pages, 1M description characters and 8 MiB; private/no-store |
| PUSH-01 | New-order push implementation | City-scoped committed outbox, Expo tickets/receipts and invalid-token fencing | Implemented, pushed and deployed in dadc421 | High7 | 2026-10-13 | event_type=ORDER_AVAILABLE; legacy type retained; actual order UUID; device delivery unverified |
| STORE-01 | Private S3 implementation | Public HTTPS storage selection with owned signed upload/confirm/avatar flow | Code pushed and deployed | High7 | 2026-10-13 | Courier avatars only; activation depends on deferred real credentials |

## Remaining setup and mobile work

No confirmed backend implementation task remains open within the latest approved scope.
The following rows are configuration or mobile implementation work, not verification-only tasks.

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| PUSH-SETUP | Activate Expo delivery | Configure the worker provider and Expo/EAS platform credentials | Pending runtime setup | High7 | 2026-10-13 | Set PUSH_PROVIDER=expo; EXPO_ACCESS_TOKEN if enhanced security is enabled; do not claim device delivery from build success |
| STORE-SETUP | Activate private S3 | Supply real S3 credentials and select the reachable HTTPS storage origin | Deferred by user | High7 | 2026-10-13 | Use S3_ENDPOINT, S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY; no localhost workaround |
| UI-05 | Latest mobile handoff | Wire event_type fallback, annual occurrence calendar and authenticated statement PDF download | Prompt delivered; mobile implementation unconfirmed | Medium5 | 2026-10-13 | Separate mobile chat owns implementation; do not reopen completed UI-01 through UI-04 |

## Deferred work and accepted decisions

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| FIN-POLICY | Final closure policy | Resolve obligations, fund-return workflow and legal retention before completion | Postponed by user (2026-10-13) | High7 | 2026-10-11 | Existing 14-day recovery/admin-review hold remains; no automatic completion or refund |
| MOB-12 | Optional features | Bank profile and financial switches | Deferred by user | Medium4 | 2026-10-07 | Do not expose fabricated endpoints |
| SEC-19 | Published test secrets | Reject published defaults in production | Excluded by user | High7 | 2026-10-04 | Protect runtime secrets operationally |
| PERF-09 | Audit retention | Growth monitoring/archival | Archival excluded for dev | Medium5 | 2026-10-04 | No automatic history deletion |
| SEC-16/17 | Admin record integrity | Unrestricted authenticated financial/audit CRUD | Accepted risk | High9 | 2026-10-04 | User explicitly retained unrestricted CRUD |

Independent courier appointments are excluded by the user (2026-10-13); assigned-order calendar remains.


## Latest release evidence — 2026-10-13

- Application changes: dadc421; reference documentation: 48879e9, published to master.
- Both public occurrence/PDF contracts matched the release; API readiness and worker/scheduler health passed.
- Native disposable PostgreSQL/Redis suite: 1511 passed, zero skips, 89.45% coverage; both hook stages passed; PDF preview inspected.
- Real Expo delivery and authenticated live PDF rendering were not exercised. No production fixtures, real pushes, Docker or mobile edits were used.
- Locked production dependency audit was clean; GitHub still reports a moderate alert whose reconciliation remains excluded by the user.

Detailed evidence and limitations belong in codebase_review.md. Verification-only tasks
remain removed; absence of a task does not establish external integration success.

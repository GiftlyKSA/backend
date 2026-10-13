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

## Current release

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| MOB-05 | Courier reports | Settled payout including reimbursement, charts/comparison and optimistic targets | Deployed; authenticated empty-ledger/settings/role checks passed | High7 | 2026-10-07 | Riyadh/Sunday weeks, maximum366days; no unsupported financial switch |
| MOB-06 | Inbox and timeline | Owned paginated notifications/read actions and actual order transitions | Deployed; ownership/read/pagination checks passed | High7 | 2026-10-07 | Older orders explicitly incomplete; foreign access denied |
| MOB-07 | Occasion reminders | Opt-in one-off09:00Riyadh reminder and bounded durable push retries | Implemented; worker running | Medium5 | 2026-10-07 | Annual recurrence deployed; provider limits documented in review |
| MOB-09 | Phone change | Current-phone reauth,new-number verification and credential revocation | Deployed; authenticated proof/change/revocation checks passed | High8 | 2026-10-07 | Scoped60secondOTP; single-use300secondproof; fresh login required |
| MOB-10 | Account requests |14-day recovery,then admin review,durable detailed email | Implemented; worker running | High8 | 2026-10-07 | No automatic permanent erasure/refund; retention/completion policy unresolved |
| MOB-11 | Own-order search | Literal bounded description prefix/exactUUID,existing status/date/cursor | Deployed; ownership/cursor/customer-only checks passed | Medium5 | 2026-10-07 | Index/custom/automatic/generic/write measurements recorded |
| OPS-01 | Isolated worker | Separate worker/scheduler Dockerfiles,single scheduler,two concurrent tasks | Deployed; health and queue execution verified | High7 | 2026-10-04 | Riyadh/shared DBRedis; independent apps; keep one scheduler |
| UI-01 | Mobile integration | Add courier-home earnings, customer q search and actual timeline to existing integration | Completed in mobile chat (user-reported 5501a79) | High7 | 2026-10-11 | User report 2026-10-13; no mobile edits in this chat |
| UI-02 | Courier home earnings | Wire week/month cards to existing earnings reports; reuse helpers, never wallet balance. | Completed in mobile chat (user-reported 5501a79) | Medium5 | 2026-10-12 | User report 2026-10-13; no mobile edits in this chat |
| UI-03 | Customer order search | Add bounded q search; preserve date/status filters and reset cursors. | Completed in mobile chat (user-reported 5501a79) | Medium5 | 2026-10-12 | User report 2026-10-13; no mobile edits in this chat |
| UI-04 | Historical order timeline | Load participant timeline with pagination and history_complete warning. | Completed in mobile chat (user-reported 5501a79) | Medium5 | 2026-10-12 | User report 2026-10-13; no mobile edits in this chat |
| MOB-13 | Annual recurrence | Opt-in annual calendar feed and version-fenced reminders; leap years only | Deployed; runtime contract confirmed | Medium5 | 2026-10-13 | Existing one-off CRUD preserved |
| MOB-14 | Statement PDF | Complete owned informational snapshot with bounded generation | Deployed; runtime contract confirmed | Medium5 | 2026-10-13 | No legal/earnings document claim |
| PUSH-01 | Expo delivery | Durable tickets/receipts and registration-fenced invalid-token cleanup | Adapter deployed; Expo/EAS setup pending | High7 | 2026-10-13 | Runtime Expo/EAS setup required |
| STORE-01 | Private S3 selection | Public HTTPS real storage independent of other providers | Code deployed; keys deferred by user | High7 | 2026-10-13 | Do not weaken mobile HTTPS/image validation |

## Deferred work and accepted decisions

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| FIN-POLICY | Final closure policy | Resolve obligations, fund-return workflow and legal retention before completion | Postponed by user (2026-10-13) | High7 | 2026-10-11 | Existing 14-day recovery/admin-review hold remains; no automatic completion or refund |
| MOB-12 | Optional features | Bank profile and financial switches | Deferred by user | Medium4 | 2026-10-07 | Do not expose fabricated endpoints |
| SEC-19 | Published test secrets | Reject published defaults in production | Excluded by user | High7 | 2026-10-04 | Protect runtime secrets operationally |
| PERF-09 | Audit retention | Growth monitoring/archival | Archival excluded for dev | Medium5 | 2026-10-04 | No automatic history deletion |
| SEC-16/17 | Admin record integrity | Unrestricted authenticated financial/audit CRUD | Accepted risk | High9 | 2026-10-04 | User explicitly retained unrestricted CRUD |

Independent courier appointments are excluded by the user (2026-10-13); assigned-order calendar remains.


2026-10-13 contract follow-up: compatible event_type push alias and complete literal PDF
text are implemented and locally verified. Existing recurrence/PDF routes are deployed;
the latest follow-up rollout is checked separately. No appointments were added.

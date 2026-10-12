# Giftly task tracker

**Created:**2026-10-11 · **Updated:**2026-10-12.
Source: codebase_review.md. Status distinguishes source, publication, deployment and
external/mobile verification. Scores indicate urgency, not CVSS.

## Current release

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| MOB-05 | Courier reports | Settled payout including reimbursement, charts/comparison and optimistic targets | Implemented; release verification underway | High7 | 2026-10-07 | Riyadh/Sunday weeks, maximum366days; no unsupported financial switch |
| MOB-06 | Inbox and timeline | Owned paginated notifications/read actions and actual order transitions | Implemented; release verification underway | High7 | 2026-10-07 | Older orders explicitly incomplete; foreign access denied |
| MOB-07 | Occasion reminders | Opt-in one-off09:00Riyadh reminder and bounded durable push retries | Implemented; worker running, vendor delivery unverified | Medium5 | 2026-10-07 | Annual recurrence deferred; live vendor delivery unverified |
| MOB-08 | Courier avatar | Existing private upload/confirm plus attach/remove/profile read | Implemented; release verification underway | Medium5 | 2026-10-07 | Customers excluded; owned decodedJPEG/PNG only |
| MOB-09 | Phone change | Current-phone reauth,new-number verification and credential revocation | Implemented; release verification underway | High8 | 2026-10-07 | Scoped60secondOTP; single-use300secondproof; fresh login required |
| MOB-10 | Account requests |14-day recovery,then admin review,durable detailed email | Implemented; worker running, vendor delivery unverified | High8 | 2026-10-07 | No automatic permanent erasure/refund; retention/completion policy unresolved |
| MOB-11 | Own-order search | Literal bounded description prefix/exactUUID,existing status/date/cursor | Implemented; release verification underway | Medium5 | 2026-10-07 | Index/custom/automatic/generic/write measurements recorded |
| OPS-01 | Isolated worker | Separate worker/scheduler Dockerfiles,single scheduler,two concurrent tasks | Deployed; health and queue execution verified | High7 | 2026-10-04 | Riyadh/shared DBRedis; independent apps; keep one scheduler |
| UI-01 | Mobile integration | Wire published capabilities using exact contracts and chat handoff | Mobile work pending | High7 | 2026-10-11 | No mobile code changed; capability-gate until public deployment verified |

## Deferred, accepted or externally unverified

| ID | Task | Description | Status | Urgency | Created | Note |
| --- | --- | --- | --- | --- | --- | --- |
| OPS-02 | Remote Linux CI | Verify authenticated CI,image and production workload behavior | Unverified | Medium5 | 2026-10-11 | Local tests do not prove remote capacity |
| SQL-01 | Capacity and memory | Measure API p95/p99,pool waits,queue lag,decoder CPU/RSS | Partially measured | Medium5 | 2026-10-04 | Synthetic query/Redis evidence exists; real workloads still required |
| FIN-POLICY | Final closure policy | Resolve obligations,fund-return workflow and legal retention before completion | Product/legal decision required | High7 | 2026-10-11 | Admin review holds requests; no automatic completion |
| MOB-12 | Optional features | Appointments,bank profile,annual recurrence,statement PDF,financial switches | Deferred by user | Medium4 | 2026-10-07 | Do not expose fabricated endpoints |
| INT-01 | Live vendor acceptance | SMS,email,push,Dhamen callback/provider validation | Excluded by user | Medium5 | 2026-10-04 | No real-provider success claim; production payments remain disabled |
| SEC-15 | Ingress redaction | Verify credential redaction beyond application boundary | Excluded by user | Medium5 | 2026-10-04 | Safer WS transports exist; legacy compatibility retained |
| SEC-19 | Published test secrets | Reject published defaults in production | Excluded by user | High7 | 2026-10-04 | Protect runtime secrets operationally |
| SEC-20 | Dependency alerts | Reconcile GitHub alerts/deployed image with lockfile | Excluded by user | Medium5 | 2026-10-04 | Earlier dated local graph audit does not resolve remote alerts |
| PERF-09 | Audit retention | Growth monitoring/archival | Archival excluded for dev | Medium5 | 2026-10-04 | No automatic history deletion |
| SEC-16/17 | Admin record integrity | Unrestricted authenticated financial/audit CRUD | Accepted risk | High9 | 2026-10-04 | User explicitly retained unrestricted CRUD |

## Verification record

All local application tests passed with disposable PostgreSQL16,Redis7,native media
binaries; 1,391tests passed with89.42%coverage,zero skips. Focused ownership,OTP/race/rollback tests
and independent reviews passed. Ruff lint/format,mypy260modules and both hook stages
passed before final staging. Additive migration0029..0032 roundtrips/refusal guards were
tested on isolated databases. Publication/deployment will be updated after verification.
No Docker,live-user writes,mobile repository edits or real vendor calls were performed.

2026-10-12: Dockerfile.worker and Dockerfile.scheduler split consumer/scheduler,retain health/migration gates and legacy combined compatibility. CI now builds all three images. Docker/PaaS execution not claimed.

Split-image verification2026-10-12: full nativePG/Redis suite1,399passed,no skips,89.53%coverage; lint/format,mypy260modules and both hook stages pass. CI builds configured but not executed locally. No mobile contract change.

2026-10-12: API PROCESS_ROLE worker fallback removed; only explicit separate image commands launch queued/scheduled services. No API startup/task consumer/cron loop. Existing platform cron removal/deployment verification remains operational.

API-only follow-up2026-10-12: source regression/full1,399tests89.44%coverage and hooks pass; CranL backend cron inventory is empty. Separate worker/scheduler deployment remains pending; backend performs no automatic queue consumption or scheduling.

## CranL deployment verification - 2026-10-12

OPS-01 is deployed in Riyadh: separate worker/scheduler health checks passed and logs
confirmed queued job execution. The initial worker environment mismatch was resolved
by restarting after its build. Both apps retain development mode and shared API
DB/Redis settings. Live vendor delivery and sustained capacity remain unverified.
See documentation.md for app URLs, overrides and singleton scheduler requirements.

## Configuration/logging follow-up - 2026-10-12

Per-process validation and bounded scrubbed error formatting are implemented; release
verification is in progress. Scheduler does not require API/worker secrets; worker requires
only its current job dependencies. API interlocks remain unchanged. External error-alert
service/destination setup is pending selection; no Slack/Telegram/ClickUp messages sent.

Configuration/logging local verification:1,417tests passed,zero skips; both hook stages
and strict typing passed. No Docker,production data changes or external alerts were used.

939e3f3 deployed and remote environment cleanup verified: scheduler6variables, worker25
API-only variables removed, all3app health200; scheduler/worker activity confirmed.
Production empty-secret guard follow-up is under final verification.

Final configuration/logging verification:1,419tests passed,no skips,89.49%coverage.
Empty production secret regression fixed; API/worker fail closed on blank credentials.

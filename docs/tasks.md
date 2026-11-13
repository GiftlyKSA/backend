# Giftly backend tasks

Updated 2026-11-13. This tracker contains outstanding work. The dated audit,
completed implementation evidence and benchmark details are in the
[previous task register](https://github.com/GiftlyKSA/backend/blob/44200cae743a37b634054eb1d3c3e01b7bb5e02c/docs/tasks.md).

<a id="full-audit-evidence-and-analysis"></a>

## Outstanding findings

| Task | Severity / urgency | Status | Remaining work and completion condition |
| --- | --- | --- | --- |
| AUD-32 · Authentica challenge binding | 6/10 · 7/10 | Provider dependency | The proposed provider-generated SMS integration verifies by phone and OTP. It has no documented challenge ID or purpose parameter. Keep it out of release until Authentica supplies purpose-bound verification or the product approves a separate verification design. Test resend overlap, one-use consumption and ambiguous issuance before switching. [Authentica verification contract](https://docs.authentica.sa/api-reference/otp-verification/verify-otp) |
| [AUD-30](#aud-30) · Legacy chat credentials at ingress | 6/10 · 7/10 | Deferred to CranL and client rollout | Verify upstream redaction or schedule retirement of query-token compatibility after supported clients migrate. The prior canary was synthetic. Preserve working chat clients and never use real credentials to probe log retention. |
| AUD-16 / RL-01 · Trusted proxy and handshake admission | 5/10 · 5/10 | Deferred by request | Ask CranL to confirm the trusted forwarding chain before enabling narrower endpoint budgets or introducing pre-auth IP admission. Account-scoped WebSocket admission remains active. Verify address attribution, reconnects and shared-IP behavior with approved fixtures. |
| AUD-19 / AUD-22 / RL-04 · Pooler and timeout tuning | 4/10 · 3/10 | Deferred by request | Confirm whether CranL uses direct PostgreSQL or a transaction pooler and its prepared-statement support. Measure pool wait and command/lock contention before changing defaults. Existing timeout controls are implemented. |
| AUD-38 · Terminal push and receipt retention | 4/10 · 4/10 | Retention decision required | Approve a terminal retention window and durable replay/deduplication horizon before adding bounded deletion. Preserve active leases and accepted deliveries. Test late receipts and replay across the purge boundary. |
| AUD-37 · Repeated delivery-video validation | 3/10 · 3/10 | Storage guarantee required | Keep full validation at attachment until the active storage provider can prove the exact validated object version and enforce reads against it. Verify replacement and deletion races before skipping a decode. |
| AUD-28 / AUD-42 / AUD-43 · Runtime measurements | 2/10 · 2/10 | Measure on representative traffic | Use the shipped request, cache and queue samples to establish p95/p99 and determine whether cross-request wake-up coalescing, extra pool timing or an admin overview rollup is justified. Keep durable recovery and avoid unbounded telemetry. |
| IDX-02 / IDX-05–09 · Optional query indexes | 2/10 · 2/10 | Revisit as data grows | The deployed promo catalog had one row at the last check. Keep the existing indexes for promos, courier earnings, media cleanup, invoice history and order prefix search. Evaluate BRIN only for large append-heavy time ranges. Require representative compiled plans, index size and write cost before adding coverage. |

<a id="aud-30"></a>

The legacy query-token risk applies to upstream request history. Application logs
already scrub credentials. See [AUD-30 evidence](https://github.com/GiftlyKSA/backend/blob/44200cae743a37b634054eb1d3c3e01b7bb5e02c/docs/tasks.md#aud-30).

## Postponed product and provider work

| Task | Status | Dependency |
| --- | --- | --- |
| FIN-POLICY · Final account closure and fund return | Postponed by request | Approve fund-return, data-retention and closure rules. The 14-day recovery and administrative review flow stays in place. |
| FIN-NOTIFY · Financial notification preferences | Product decision required | Define supported events and channels before exposing preferences. |
| PUSH-SETUP · Expo delivery verification | External | Verify platform credentials, real device delivery and receipt handling with approved staging fixtures. |
| STORE-SETUP · Private S3 verification | External | Verify endpoint, scoped credentials, signed operations and provider version guarantees with approved staging fixtures. |

## Accepted risks and exclusions

- SEC-16 and SEC-17 retain unrestricted authorized admin CRUD, including posted
  financial records and audit history, as previously approved.
- SEC-19, dependency-alert reconciliation, live vendor checks and audit archival
  retain their prior exclusions. See the
  [recorded decisions](https://github.com/GiftlyKSA/backend/blob/44200cae743a37b634054eb1d3c3e01b7bb5e02c/docs/tasks.md#accepted-risks-and-exclusions-retained).

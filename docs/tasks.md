# Giftly backend tasks

**Created:** 2026-10-11 | **Updated:** 2026-10-14

Only outstanding backend implementation and runtime-configuration work is tracked here.
All items below are postponed by the user; no task is currently active within the latest
approved scope. Mobile work, completed work and testing/verification tasks are omitted.

| ID | Task | Short description | Status | Urgency | Created | Last updated | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PUSH-SETUP | Activate Expo push | Configure worker provider and Expo/EAS platform credentials. | Postponed | High7 | 2026-10-13 | 2026-10-14 | Adapter implemented; real delivery configuration still required. |
| STORE-SETUP | Activate private S3 | Configure matching private bucket, endpoint and credentials on API and worker. | Postponed | High7 | 2026-10-13 | 2026-10-14 | Avatar backend fixes pushed in bb85fcd; scheduler needs no storage secrets. |
| FIN-POLICY | Complete account closure and fund return | Implement final completion/refund/retention behavior after business and legal policy approval. | Postponed | High7 | 2026-10-11 | 2026-10-14 | Keep existing 14-day recovery and admin-review hold; no automatic erasure/refund. |
| BANK-PROFILE | Saved courier bank profile | Add private saved bank details after validation, masking and verification rules are approved. | Postponed | Medium4 | 2026-10-07 | 2026-10-14 | Existing withdrawal IBAN input is not a saved bank-profile API. |
| FIN-NOTIFY | Financial notification preferences | Implement preferences and actual delivery after covered financial events/channels are defined. | Postponed | Medium4 | 2026-10-07 | 2026-10-14 | Do not expose a functional switch before its delivery behavior exists. |

Accepted security risks, excluded work, release evidence and unverified limitations remain
in [codebase_review.md](codebase_review.md). Removing verification tasks does not establish
that those checks passed or that all security risks are resolved.

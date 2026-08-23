# ADR 0006 — Dhamen gateway production-contract gate

## Status

Proposed — production launch blocked pending written vendor, finance, and platform-security approval (2026-08-23).

## Context

SAFE-GIFT plans to integrate Dhamen Pay-InOut v1.5 for hosted customer cash-in and later
courier payouts. The supplied manual identifies the relevant operations and request
headers (`App-key`, `App-id`, `ClientId`, and `api-version: 2`), but it does not provide
vendor-issued base URLs, account values, callback authentication, callback retry limits,
source CIDRs, or a decision on the customer identifier format. Those omissions mean this
record is an integration decision and launch gate, not evidence of vendor approval or
sandbox validation.

## Decision

### v1.5 integration shape

- Use Customer Payment for wallet top-ups and invoice remainders, with a durable unique
  SAFE-GIFT payment reference, bounded expiry, and registered HTTPS return URL.
- Omit `supplierId` from Customer Payment. SAFE-GIFT has dynamic fees and owns the per-
  invoice calculation of fees, tax, promotions, courier payout, and split-wallet amounts.
- Settle internal ledger state only after a verified Dhamen payment notification or an
  authoritative Customer Payment Status reconciliation matches the expected payment
  reference, customer identifier, amount, paid state, and settled state. A return URL is
  never payment confirmation.
- Use Create/Update Supplier and Supplier Payment only after an internal courier
  withdrawal approval. Mark the withdrawal paid only after verified Supplier Payment
  status or notification reconciliation.
- Do not invoke Dhamen Refund or Refund-to-IBAN in the initial release. Customer dispute
  refunds remain internal wallet credits pending written finance approval of any external
  refund policy.

### Callback security: no mechanism selected

No vendor-supported callback authentication mechanism is currently documented. The
manual does not establish an HMAC/signature header and canonicalization, mutual-TLS
client-certificate contract, reverse-proxy source CIDRs, or provider authentication
header. Consequently, an unauthenticated Dhamen webhook is rejected: it could fabricate
settlement or payout events and compromise ledger integrity.

Production launch is explicitly blocked. Do not implement an opaque query token, guessed
webhook secret, self-defined signature, unauthenticated allowlist, or any weaker
substitute. The callback design may be approved only after Dhamen and platform security
provide and approve one of these vendor-supported arrangements:

1. HMAC/signature: exact header name, algorithm, canonicalization/raw-body rules, key
   provisioning and rotation, clock/replay window, and failure response.
2. mTLS: Dhamen client-certificate chain, hostname/SNI requirements, validation rules,
   certificate rotation/revocation process, and test procedure.
3. Reverse-proxy source-CIDR restriction plus a documented provider authentication
   header: exact CIDRs, header format/validation, change notice process, and replay
   protection.

For every approved arrangement, callbacks must be idempotent and status reconciliation
must remain available for delayed or failed deliveries.

## Required written approvals and facts before enabling production code

### Dhamen vendor

- Sandbox and production base URLs; environment separation; API version confirmation.
- Sandbox and production `App-key`, `App-id`, `ClientId`, and authority profile ID through
  approved secret delivery (values are not recorded here).
- Registered HTTPS return URLs and notification URL.
- Selected callback-security option with all details listed above; retry semantics,
  timeout/retry limits, notification batch behavior, response expectations, and source
  CIDRs where applicable.
- Confirmation that a persistent, opaque numeric 12-character `customerIdentifier` is
  permitted for authority-account collection. Until written confirmation, SAFE-GIFT must
  not assume it is acceptable in place of a legal/KYC identity number.
- Sanitized sandbox evidence for Customer Payment, success/failure notification,
  duplicate replay, Customer Payment Status, cancellation, supplier create/update,
  Supplier Payment, and successful/failed payout reconciliation.

### Finance and platform security

- Finance approval of the cash-in authority account, dynamic-fee flow without
  `supplierId`, later courier supplier payout, and dispute-refund treatment.
- Platform-security approval of the selected callback verification, secret/certificate
  custody and rotation, trusted reverse-proxy configuration, logging redaction, replay
  handling, and incident/change process.
- Joint approval of pending-link cutover: legacy StreamPay links remain serviceable until
  every open link is terminal and its maximum expiry plus reconciliation grace period has
  elapsed. No endpoint or legacy data removal occurs before that approval.

## Configuration record

`.env.example` intentionally contains only blank `DHAMEN_*` names. It does not assert
base URLs, credentials, a webhook secret, callback header, mTLS material, or CIDR range.
Populate only the vendor-confirmed values through the approved deployment secret path
after the approvals above.

## Consequences

- Dhamen code cannot be enabled in production yet; StreamPay documentation and endpoints
  remain active legacy behavior until an approved cutover.
- Production startup and deployment must fail closed when required Dhamen credentials or
  approved callback-authentication settings are missing.
- The external payment provider is never a ledger authority; SAFE-GIFT retains its
  append-only ledger and reconciles provider events against its own intent records.

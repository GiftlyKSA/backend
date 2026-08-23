# Payments

Wallet top-ups and invoice remainders each use one `payment_intents` record (ADR 0003).
StreamPay creates a consumer, one-time products, and a hosted payment link. All money is
sent and returned as decimal strings.

## Dhamen migration status (not production-enabled)

Dhamen Pay-InOut v1.5 is the planned successor to StreamPay; the StreamPay endpoints below
remain active legacy contracts until a separately approved cutover. No Dhamen endpoint,
callback URL, credential, or sandbox result is asserted by this document.

The planned cash-in flow creates a Dhamen **Customer Payment** for a wallet top-up or an
invoice remainder. It uses a durable SAFE-GIFT payment reference, a bounded expiry, and a
registered HTTPS return URL. `supplierId` is intentionally omitted: SAFE-GIFT calculates
dynamic fees, tax, promotions, courier payout, and split-wallet amounts per invoice, and
the Dhamen manual directs authority-account collection with dynamic fees not to use it.
The return URL is navigation only; a wallet credit or invoice settlement must wait for an
authenticated Dhamen payment notification or authoritative Customer Payment Status
reconciliation that matches the expected reference, customer identifier, amount, paid
state, and settled state.

The planned cash-out flow uses **Supplier Payment** only after SAFE-GIFT has approved an
internal courier withdrawal. Supplier registration/update and payout require the vendor's
contracted authority-account and verified supplier data. A local withdrawal is not marked
paid merely because a payout was requested; it awaits the applicable supplier-payment
status or authenticated notification reconciliation.

Dhamen Refund and Refund-to-IBAN endpoints are outside the initial release. Customer
dispute refunds continue as internal wallet credits until finance gives written approval
for any return-to-original-method policy.

### Production gate for Dhamen callbacks

The supplied v1.5 manual does not specify a callback signature, mutual TLS contract,
provider authentication header, source CIDRs, or retry contract. Therefore a Dhamen
notification endpoint is **not approved for production** and must not accept an
unauthenticated callback. Before enabling Dhamen, written vendor and platform-security
approval must record one supported mechanism in ADR 0006: exact HMAC header and
canonicalization, mTLS client-certificate lifecycle, or reverse-proxy source-CIDR
restriction plus a vendor-supported application authentication header. See ADR 0006 for
the complete launch checklist.

## POST /api/wallets/topup

Start a wallet top-up. **Auth:** Bearer JWT. **Role:** CUSTOMER or COURIER.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| amount | string | yes | 100.00-20000.00 |

Success `201`: `{ payment_intent_id, amount, payment_url }`. In production, redirect the
client to the returned hosted `payment_url`; the wallet is credited only after StreamPay
confirms it. In development, StreamPay is not called, the top-up is settled immediately,
and `payment_url` is `null`.

## POST /api/invoices/{invoice_id}/pay

Pay an issued invoice from the wallet, StreamPay, or both. The wallet is applied first.
When a remainder is due, it is held and the response is `PENDING` with `payment_url` in
production. In development, the remainder is settled immediately and `payment_url` is `null`.

StreamPay receives the frozen invoice items where they can exactly represent the payable
total, plus a visible adjustment for delivery, service fees, tax, and discounts. Split
payments use one authoritative outstanding-balance item so StreamPay's total always
matches the ledger amount exactly.

## POST /api/webhooks/streampay

StreamPay's public callback; no JWT is used. It requires:

- `X-Webhook-Signature: t=<timestamp>,v1=<HMAC-SHA256>`
- an HMAC over the exact raw body as `timestamp.raw_body`

The service locks the StreamPay payment-link ID, verifies status and amount, and settles
idempotently. A duplicate delivery is a no-op.

Expected JSON shape:

```json
{
  "event_type": "PAYMENT_SUCCEEDED",
  "data": {
    "payment_link": {"id": "<stream-payment-link-id>"},
    "payment": {"status": "PAID", "amount": "724.50"}
  }
}
```

Success `200`: `{ outcome }`, where outcome is `processed`, `already_processed`, or
`failed`.

## POST /api/dev/streampay/simulate

Development only. Submit `{ "payment_link_id": "..." }` to fire a correctly signed
Stream-shaped webhook at the real handler. This route is absent in test and production.

## Apple Pay

`payment_url` is StreamPay's hosted checkout URL. StreamPay presents Apple Pay when it is
enabled for the merchant and available on the payer's device; no API key ever reaches the
client. For future embedded checkout, host StreamPay's exact merchant-domain association
file at `/.well-known/apple-developer-merchantid-domain-association` and complete their
merchant registration first.

## Escrow model

An invoice total lands in `SYSTEM_ESCROW`, sourced by the wallet and StreamPay payment.
It is released only through the existing delivery and approval flow.

# Customer invoice promo integration — 2026-10-03

```text
Update the Giftly customer Invoice / Checkout screen to apply, replace and remove
promo codes before payment. Promo codes are case-insensitive: gift10, Gift10 and
GIFT10 mean the same code. Surrounding whitespace is trimmed; the backend returns
the canonical uppercase code. Never calculate or submit discounts/payment totals.

AUTHENTICATION
Authorization: Bearer <customer_access_token> for preview, application and payment.
Application requires the ACTIVE CUSTOMER who owns the invoice's order. Couriers,
admins and unrelated users cannot apply codes; ownership is checked on the server.

1. LOAD CURRENT INVOICE
GET /api/orders/{order_id}/invoice
Path order_id: UUID string. No body. HTTP 200: full InvoiceResponse below.
Use this endpoint to refresh after a conflict or returning to the invoice screen.
Only participants can read an invoice. The active invoice may have a different ID
after promo application; never keep paying an old cached invoice ID.

2. OPTIONAL PREVIEW (DOES NOT APPLY A DISCOUNT)
POST /api/promos/validate
Body: {"order_id":"550e8400-e29b-41d4-a716-446655440000","code":"gift10"}
order_id: required UUID string; code: required nonblank string, max 32 characters.
Unknown properties are rejected. HTTP 200 example:
{"code":"GIFT10","discount_amount":"60.00","original_total_amount":"724.50",
 "total_amount":"655.50"}
All amounts are decimal strings in SAR. This does not reserve/consume a code, change
the invoice or reduce the payable amount. Follow with application to save it.

3. APPLY OR REPLACE
POST /api/invoices/{invoice_id}/promo
Authorization: Bearer <customer_access_token>
Content-Type: application/json
Idempotency-Key: <new UUID string for this user operation>
Path invoice_id: current invoice UUID string.
Body: {"code":"gift10"}

REMOVE
Use the same endpoint and a new operation key with {"code":null}.
code is required: string applies/replaces; null removes. Empty/whitespace-only
strings, missing code, extra fields or a missing Idempotency-Key produce 422.
Idempotency-Key must have 1–128 characters. Do not send any amount or customer ID.

SUCCESS HTTP 200: existing full InvoiceResponse, example:
{
  "id":"550e8400-e29b-41d4-a716-446655440001",
  "order_id":"550e8400-e29b-41d4-a716-446655440000",
  "status":"ISSUED",
  "currency":"SAR",
  "items_net_amount":"500.00",
  "courier_fee_amount":"100.00",
  "service_fee_amount":"30.00",
  "discount_amount":"60.00",
  "net_after_discount_amount":"570.00",
  "tax_amount":"85.50",
  "total_amount":"655.50",
  "promo_code":"GIFT10",
  "issued_at":"2026-10-03T10:00:00Z",
  "expires_at":"2026-10-04T09:00:00Z",
  "items":[{
    "position":1,"title":"Gift","description":null,
    "unit_price_amount":"500.00","quantity":1,"tax_rate":"0.1500",
    "line_net_amount":"500.00","line_discount_amount":"50.00",
    "line_taxable_amount":"450.00","line_tax_amount":"67.50",
    "line_total_amount":"517.50"
  }]
}

TYPES
- id/order_id: UUID strings. status: DRAFT|ISSUED|PAID|CANCELLED|EXPIRED|REFUNDED.
- currency: string, SAR. All monetary fields: two-decimal strings, never floats.
- promo_code: canonical string|null.
- issued_at/expires_at: UTC ISO datetime strings|null. Display in local timezone.
- items: array. position/quantity integers; title string; description string|null;
  tax_rate decimal string representing a fraction; line amounts decimal strings.

Replace the ENTIRE displayed invoice with the application response. Its ID can
change: a new immutable revision supersedes the old invoice. Keep the returned ID.
The old invoice remains financial history with CANCELLED status. Original items,
fees, tax policy and payment deadline are preserved. Application does not pay it.
The order stays WAITING_PAYMENT. Same-code application/removing an absent code
are safe no-ops. Invalid replacements leave the previous invoice/reservation intact.

RETRIES AND CONFLICTS
For a network retry, retain the SAME target invoice ID, normalized code/null and
Idempotency-Key. A successful replay creates no additional revision or reservation.
Reusing a key for a different invoice/code returns 409 CONFLICT. Generate a fresh
key for each new user action, including replacing/removing a code.
A replay returns its original result invoice, which may now be paid or superseded;
inspect its status and refresh the current active invoice before a new action.
Do not automatically retry stale invoice conflicts or automatically start payment.

ERRORS
Envelope: {"error":{"code":string,"message":string,"request_id":string}}.
- 401: missing/invalid/expired/revoked credentials. Refresh or sign in.
- 403 FORBIDDEN: wrong role/ineligible customer.
- 404 NOT_FOUND: nonexistent invoice or invoice not owned by this customer.
- 409 CONFLICT: paid/cancelled/expired/obsolete invoice, wrong order state,
  payment in progress, conflicting idempotency request, or unavailable pricing
  policy. Refresh GET /api/orders/{order_id}/invoice and show the server message.
- 422: malformed input, no eligible discount, or promo rejection:
  PROMO_NOT_FOUND, PROMO_INACTIVE, PROMO_NOT_STARTED, PROMO_EXPIRED,
  PROMO_MIN_ORDER_NOT_MET, PROMO_USAGE_EXCEEDED, PROMO_USER_LIMIT_REACHED.
- 429 RATE_LIMITED: respect Retry-After when supplied.
Show safe server messages; preserve typed code on error. Disable Apply/Remove/Pay
while an operation is pending. Render all user text as plain text, never HTML.

4. PAY THE RETURNED CURRENT INVOICE
POST /api/invoices/{returned_invoice_id}/pay
Authorization: Bearer <customer_access_token>. No JSON request body.
Response HTTP 200:
{"invoice_id":"550e8400-e29b-41d4-a716-446655440001","status":"PENDING",
 "amount_from_wallet":"0.00","amount_from_gateway":"655.50",
 "payment_url":"https://provider.example/checkout"}
invoice_id/status are strings; amounts decimal strings; payment_url string|null.
PAID indicates confirmed settlement in supported flows; PENDING may provide a URL.
Only confirmed settlement consumes the promo. A browser redirect is not proof of
payment. Refresh invoice/order/wallet after checkout. Production payment remains
503 PAYMENTS_DISABLED until Dhamen is implemented and verified.

LIMITATION
Invoices created before this release may lack their original pricing-policy
snapshot. Such invoices cannot be safely repriced and return 409, rather than
silently using today's fees/VAT. Do not promise that every historical unpaid
invoice can accept a new code. New invoices store the policy automatically.

This change adds invoice promo application only. Do not invent chat-media,
notification-feed, earnings-report or live Dhamen contracts from this prompt.
Exact schemas: docs/mobile-openapi.json.
```

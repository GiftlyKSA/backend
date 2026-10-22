# Giftly API reference

## Mobile read additions — 2026-10-07

The inspected feature routes are deployed as of 2026-10-12. Authenticated checks and
remaining limits are recorded in tasks.md. These operations
require Bearer authentication and current customer/courier eligibility. They do not
expose administrative, development or provider operations.

| Screen | Operation | Input | Output / behavior |
| --- | --- | --- | --- |
| Wallet invoices | GET `/api/invoices` | Optional status, from_date/to_date (issued date in Riyadh), include_historical=false, UUID cursor, limit1..100 default25 | InvoicePage with compact InvoiceSummary rows, current marker, decimal total; newest created_at/id first. Customers cannot list drafts. |
| Wallet statement | GET `/api/wallets/me/statement` | Required Gregorian from_date/to_date; inclusive Riyadh days, max366 days; UUID cursor; limit1..100 default25 | WalletStatement: currency, timezone, bounds, as_of, whole-range status-separated money totals, items, next_cursor. Totals and items share one SQL snapshot per response. |
| Wallet history | GET `/api/wallets/me/transactions` | New optional Gregorian from_date/to_date; existing cursor/default20 preserved | Existing page plus additive nullable description/order_id/invoice_id/payment_intent_id metadata. No filters means existing behavior. |
| Order photos / delivery proof | GET `/api/orders/{order_id}/media` | Optional purpose ORDER_REQUEST or DELIVERY_PROOF, UUID cursor, limit1..100 default25 | Oldest created_at/id first. Trusted MIME/size, purpose and five-minute signed access_url/expires_at; no raw object key. Current participants only; foreign order404 even if empty. |

Financial date predicates use UTC midnight minus three hours through the following
local midnight, exclusive. Output timestamps remain UTC. Calendar extremes that cannot
be represented as UTC bounds are rejected422. Reversed/malformed dates422. Empty pages
return items=[] and next_cursor=null. New lists overfetch one row to avoid phantom next
pages. Existing wallet history preserves its existing next-cursor behavior.

Invoice current means the latest visible revision by created_at/id for an order,
regardless of the status filter; include_historical=true includes earlier revisions.
No invoice number is invented; use the returned UUID for existing detail/PDF operations.
Status accepts DRAFT, ISSUED, PAID, CANCELLED, EXPIRED, REFUNDED; drafts remain courier-only.

Statement settled_net is settled_credits minus settled_debits. Pending/reversed sums
are separate nonnegative credits/debits and do not affect settled_net. Totals cover
the full selected range, not a page. Each page refreshes totals/as_of if ledger state
changes: this is a live statement, not a frozen export or historical available balance.
Reset pagination when filters change; preserve identical filters thereafter. Foreign
or out-of-range anchors404. All new reads return Cache-Control: private, no-store.
Do not sum pages or infer earnings/payment success from wallet balances.

### Courier homepage summary

`GET /api/couriers/me/summary` requires a Bearer access token for the active, verified
courier. It takes no request body or query parameters and derives the courier ID from
the token. The response is an unpaginated aggregate independent of `/api/orders`
pagination. `current_orders_count` includes assigned orders in `ASSIGNED`,
`WAITING_PAYMENT`, `IN_PROGRESS`, `DELIVERED` or `DISPUTED`; `COMPLETED` is excluded.
`registered_orders_count` is the all-time count of every order assigned to this courier,
including terminal history, matching the unfiltered courier order collection. “Registered”
is a mobile presentation label, not a distinct backend status. `completed_orders_count`
counts `COMPLETED` only; `DELIVERED` is not complete.

`settled_earnings_total` uses the exact signed, settled courier-account `ESCROW_RELEASE`
ledger basis used by `GET /api/couriers/me/earnings`, including item reimbursement and
applying the existing treatment of compensating entries. Pending and reversed ledger
rows are excluded. This is accrued order proceeds, not proof of a bank transfer; Dhamen's
automatic weekly payout is provider-side. Courier balances are not exposed as wallets,
and invoice totals are not substitutes. Empty accounts return integer zero counts and the
decimal string `"0.00"`. The API returns `SAR` and
`as_of` as the UTC timestamp at which the single SQL aggregate statement was evaluated.
All values are calculated in one database statement without loading histories or using
pagination; the response uses `Cache-Control: private, no-store`.

Example response:

```json
{"current_orders_count":4,"registered_orders_count":8,"completed_orders_count":3,"settled_earnings_total":"258.75","currency":"SAR","as_of":"2026-10-21T09:30:00Z"}
```

Common domain errors use the existing error envelope; schema422 may use detail[].
Handle401/403/404/422/503 distinctly. On429 honor Retry-After seconds. Render all user
text as plain text; signed media links must be refreshed after expiry. No response
cache or schema migration is introduced for these read operations. PostgreSQL execution
plans/integration behavior require disposable database verification, not Docker locally.


Updated 2026-10-04. This is maintained API documentation, not an implementation prompt.

[Project documentation](documentation.md) · [Current review](tasks.md#full-audit-evidence-and-analysis) · [Tasks](tasks.md)

The [non-admin OpenAPI 3.1 specification](mobile-openapi.json) inventories 83 supported
non-admin HTTP operations, including the public hosted-payment browser return page.
[Full OpenAPI](openapi.json) also includes administrative API operations.
Schemas are authoritative for types, optional values, limits and status codes. The notes
below describe screens, prerequisites, dependencies and WebSocket reconciliation.
No endpoint should be inferred for an unsupported screen: it will be added later.

Latest changes: optional `gender` on registration/profile update; own-profile responses
now restore `dob` and `gender`. Gender accepts `MALE`, `FEMALE`, `OTHER`,
`PREFER_NOT_TO_SAY` or null. Deleted accounts use `DELETED` with retained internal
reason metadata. Courier identity numbers remain encrypted; document fingerprints
and duplicate-document rejection have been removed. Production payments remain disabled.
Order APIs accept a city and delivery date. A legacy optional plain-text
`delivery_address_note` exists in persistence/admin only, outside the mobile contract.
There is no delivery URL, coordinates, geometry, radius or distance property in the API.

## Core integration flow

An order participant can now look up its conversation directly with
`GET /api/orders/{order_id}/conversation`, then page older messages through the existing
REST history endpoint. User responses include a random, unique seven-digit
`public_identifier`; active city choices include Arabic `name_ar`. Customer profiles no
longer carry rating fields. Courier ratings are derived from customer reviews of completed
orders. Precise delivery locations are not collected; order financial totals remain computed by backend
invoice/settlement flows rather than entered by clients.

For login, use the returned `expires_in` (currently **60 seconds**) for the OTP countdown.
Development may return a five-digit numeric `otp_dev`; test and production use a six-digit
code, with production delivery through the configured SMS provider. They never expose
`otp_dev`. Replace
**both** tokens after every successful refresh and clear protected local state on logout,
which revokes account sessions on all devices. Admin-dashboard TOTP applies only to
`/v1/admin/admin`; it does not add a mobile login field or endpoint.

The city UUID, decimal-string money, three-photo order limit, and
production `PAYMENTS_DISABLED` behavior described below remain the current contracts.
Screens listed as gaps still need later backend work; do not build or guess routes for them.

## Integration conventions

- HTTP limits default to 60 requests per 60 seconds per verified bearer-token identity,
  or 30 requests per 3,600 seconds per unauthenticated IP. Public login/registration
  requests share that IP budget; OTP limits also apply. On HTTP 429, respect
  `Retry-After` and avoid automatic retry loops. Deployment settings can override defaults.
- User text is plain text, not executable HTML. Names, descriptions, invoice text,
  reviews, and chat content may contain literal HTML/JavaScript-looking characters;
  preserve them and display them through text widgets or DOM `textContent`. Never use
  raw HTML insertion, evaluate returned text, or treat it as a template. Do not
  HTML-decode text and then insert it as markup. Backend validation and JSON serialization
  do not replace safe frontend rendering. Treat media URLs as separate validated
  link fields, not as HTML supplied by users.
- Admin withdrawal-rejection bodies reject unknown fields with HTTP 422. Courier
  withdrawal requests are no longer supported; send only properties declared in OpenAPI.
- Base path is `/api`. Send `Authorization: Bearer <access_token>` on protected HTTP calls. Existing users receive 30-minute access and rotating 30-day refresh credentials; use `POST /api/auth/refresh` and replace both stored tokens. Logout invalidates credentials on every device. Do not derive role or ownership from the phone number, local fixture, or a client-supplied user ID.
- `UUID string`, ISO timestamp, and Gregorian `YYYY-MM-DD` are wire values. **All money values are decimal strings**, such as `"125.50"`, not JSON numbers or halala integers. Localize Arabic display text and numerals only in the UI. A question mark after a field name means the field may be omitted; `| null` means the wire value can be null.
- A successful `204` has no body. Lists use bounded `limit` (1–100) and `next_cursor`; missing/foreign or out-of-filter order/wallet anchors return `404 NOT_FOUND`, so refresh the list when an anchor is no longer valid; pass that cursor unchanged to the same list route. Order, message, and transaction cursors are UUID strings. Inbox cursors are opaque `<timestamp>|<uuid>` strings; malformed inbox cursors return `400 BAD_REQUEST` rather than restarting the list. Do not use offset or invent a next page when `next_cursor` is null.
- Domain failures generally use `{"error":{"code":"...","message":"...","request_id":"..."}}`; common codes include `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `INVALID_STATE_TRANSITION`, `VALIDATION_ERROR`, `RATE_LIMITED`, `PAYMENTS_DISABLED`, and `RATE_LIMIT_UNAVAILABLE`. FastAPI request-schema errors may instead return `{"detail":[...]}` with HTTP 422. HTTP 429 carries `Retry-After`. Handle status and both shapes; never assume every failure has a domain envelope.
- Production wallet top-up and invoice payment currently return HTTP 503 `PAYMENTS_DISABLED`; The Dhamen testing adapter does not establish verified production integration. Do not enable checkout UI as if it works. `/api/dev/*` exists only in development; the simulation webhook is absent in production. Direct S3 upload requires a signed PUT and then confirmation before attaching a key.
- This document describes the current backend, not a proposed API. Each **When/how** paragraph explains practical use and recovery. **Before** is the prerequisite; **Then / dependent API** tells the UI agent which subsequent call consumes or follows this result.
- **Future API work:** If a screen or action has no matching endpoint documented here, do not create, assume, or integrate a new route for it. Mark that feature as awaiting backend support; its route and contract will be added later in a separate backend change. Use only the implemented calls below for the current UI integration.

## Service health (not a mobile screen)

### GET /api/health

- **API name:** Liveness probe.
- **Screens:** None; service health only.
- **Who / authorization:** Signed-out or signed-in; no bearer token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `{"status":"ok"}` (`status: string`).
- **Before:** None.
- **Then / dependent API:** None.

**When/how (59 words):** Call this only for process liveness, such as a load balancer or local diagnostics. It returns a static marker without checking PostgreSQL or Redis, so a successful response does not mean account screens can load. Mobile screens should not poll it as their data source. Use readiness separately when an operator needs to know whether backing services are available.

### GET /api/health/ready

- **API name:** Readiness probe.
- **Screens:** None; service diagnostics only.
- **Who / authorization:** Signed-out or signed-in; no bearer token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `{"status":"ready"|"unavailable","checks":{"database":"ok"|"down","redis":"ok"|"down"}}` (all fields strings); HTTP 503 when unavailable.
- **Before:** None.
- **Then / dependent API:** None.

**When/how (58 words):** Use this for deployment readiness checks rather than a customer-facing screen. The handler probes both PostgreSQL and Redis and returns a dependency status map, with HTTP 503 when either is down. A phone app should surface normal request failures through its usual error states instead of polling this endpoint. Do not confuse readiness with the static liveness route.

## Authentication and session

### GET /api/cities

- **API name:** List active Saudi cities.
- **Screens:** Customer order creation `/request`; courier registration/onboarding; courier profile editing.
- **Who / authorization:** Customer, courier, or signed-out visitor; no bearer token.
- **Path, query, headers:** None.
- **Request body:** None.
- **Response:** HTTP 200; array of `{id: UUID string, name: string, name_ar: string, shortcut: string}`.
- **Before:** None; load before showing a city selector.
- **Then / dependent API:** `POST /api/auth/register` for couriers; `PATCH /api/users/me` for courier city; `POST /api/orders` for delivery city.

**When/how:** Fetch active cities to populate registration, profile, and delivery selectors. Submit the selected `id` as `city_id` during courier registration, `courier_city_id` during profile editing, or `delivery_city_id` during order creation. The backend stores these as UUID relationships to `cities.id` and rejects inactive IDs. Existing name-based request fields remain for older clients, but send only one city field per request. Display `name_ar` in Arabic and `name` in English. Refresh choices before a new selection rather than hard-coding the seed list.

### POST /api/auth/send-otp

- **API name:** Send OTP.
- **Screens:** Phone login `/login`; OTP `/verify-otp`.
- **Who / authorization:** Signed-out; no bearer token.
- **Path, query, headers:** None.
- **Request body:** `SendOtpRequest` — `phone: string`
- **Response:** HTTP 202; `SendOtpResponse` — `expires_in: integer`; `otp_dev?: integer | null` (five digits, development only; omitted otherwise)
- **Before:** None.
- **Then / dependent API:** `POST /api/auth/verify-otp`.

**When/how (55 words):** Submit the user's Saudi mobile number when they request sign-in or resend a code. The backend accepts common local formats and normalizes them to E.164; the response deliberately looks the same whether the phone already has an account. Start the resend countdown from `expires_in`. Numeric five-digit `otp_dev` is returned only in development and omitted otherwise.

### POST /api/auth/verify-otp

- **API name:** Verify OTP.
- **Screens:** OTP `/verify-otp`.
- **Who / authorization:** Signed-out; no bearer token.
- **Path, query, headers:** None.
- **Request body:** `VerifyOtpRequest` — `phone: string`; `otp: string`
- **Response:** HTTP 200; `VerifyOtpResponse` — `is_new_user: boolean`; `role?: string | null`; `access_token?: string | null`; `refresh_token?: string | null`; `registration_token?: string | null`
- **Before:** Successful send-otp and its five-digit development or six-digit non-development code.
- **Then / dependent API:** Existing account: `GET /api/users/me`; new account: `POST /api/auth/register`.

**When/how (59 words):** Send the phone number and the five-digit development or six-digit non-development code entered on the OTP screen. If `is_new_user` is false, securely store the access and refresh tokens, use the returned role to choose the customer or courier experience, and fetch the profile. If true, carry the short-lived registration token to registration; never infer role from prototype phone fixtures.

### POST /api/auth/register

- **API name:** Register.
- **Screens:** Customer registration `/register-customer`; courier onboarding when built.
- **Who / authorization:** Signed-out with registration token; no bearer access token.
- **Path, query, headers:** None.
- **Request body:** `RegisterRequest` — `registration_token: string`; `role: string [CUSTOMER, COURIER]`; `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `gender?: string [MALE, FEMALE, OTHER, PREFER_NOT_TO_SAY] | null`; `city_id?: UUID string | null` (preferred) or `city?: string | null` (legacy); `national_id?: string | null`; `passport_id?: string | null`
- **Response:** HTTP 201; `TokenResponse` — `access_token: string`; `refresh_token: string`; `role: string`
- **Before:** `POST /api/auth/verify-otp` returning `is_new_user=true`.
- **Then / dependent API:** `GET /api/users/me`; courier verification review before courier operations.

**When/how (59 words):** Create the account after OTP verification by sending the registration token and the chosen account role. Customer fields are optional at this API boundary; courier registration additionally requires city and at least one identity document in the service. Store the issued token pair only after success, then fetch `/me`. The current mobile prototype has no complete courier onboarding screen.

### POST /api/auth/refresh

- **API name:** Refresh.
- **Screens:** Shared session restoration and background token renewal.
- **Who / authorization:** Holder of a refresh token; no bearer access token required.
- **Path, query, headers:** None.
- **Request body:** `RefreshRequest` — `refresh_token: string`
- **Response:** HTTP 200; `TokenResponse` — `access_token: string`; `refresh_token: string`; `role: string`
- **Before:** A previous login or registration token pair.
- **Then / dependent API:** Retry the originally requested protected API with the new access token.

**When/how:** Rotate the current refresh credential when the access JWT expires or shortly before expiry. Replace both locally stored tokens atomically with the returned pair; a used token must never be retried in parallel. Detected reuse returns 401 and invalidates access, refresh and dashboard credentials on every device, including current socket actions. All devices must sign in again. If refresh fails, clear local credentials and return to login. Do not send the refresh token as a normal bearer access token.

### POST /api/auth/logout

- **API name:** Logout.
- **Screens:** Profile `/profile`; shared sign-out action.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 204; No body.
- **Before:** Valid access JWT.
- **Then / dependent API:** Clear local credentials; next use starts at `/login`.

**When/how (59 words):** Call logout when the user explicitly signs out, then delete both stored tokens and close chat sockets. The backend revokes the account's access, refresh, and dashboard credentials across all devices before returning 204. Other devices must log in again. Even if the request fails, do not continue to show protected local data after the user chooses to sign out.

## Profiles and participants

### GET /api/users/me

- **API name:** Get Me.
- **Screens:** Home `/home`; Profile `/profile`; session restoration.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `UserMeResponse` — `id: UUID string`; `public_identifier: integer` (1,000,000–9,999,999); `phone: string`; `role: string`; `status: string`; `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `gender?: string [MALE, FEMALE, OTHER, PREFER_NOT_TO_SAY] | null`; `courier_profile?: CourierProfileResponse | null` (courier rating/count only here)
- **Before:** Login, register, or refresh.
- **Then / dependent API:** Role-specific home, `GET /api/wallets/me`, and `GET /api/orders`.

**When/how (65 words):** Fetch the current user's authoritative profile after restoring a stored session and whenever the profile screen opens. Use the returned role and status to select the mobile experience; do not trust a saved role from prototype fixtures. Courier verification details appear only in `courier_profile`. The response restores optional date of birth and gender. Theme preference remains a client setting and is not included in the profile contract.

### PATCH /api/users/me

- **API name:** Update Me.
- **Screens:** Profile `/profile`.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `UserUpdateRequest` — `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `gender?: string [MALE, FEMALE, OTHER, PREFER_NOT_TO_SAY] | null`; `courier_city_id?: UUID string | null` (preferred) or `courier_city?: string | null` (legacy); `courier_bio?: string | null`
- **Response:** HTTP 200; `UserMeResponse` — `id: UUID string`; `public_identifier: integer` (1,000,000–9,999,999); `phone: string`; `role: string`; `status: string`; `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `gender?: string [MALE, FEMALE, OTHER, PREFER_NOT_TO_SAY] | null`; `courier_profile?: CourierProfileResponse | null`
- **Before:** `GET /api/users/me`.
- **Then / dependent API:** Refresh `GET /api/users/me` or update local profile from response.

**When/how (59 words):** Send only fields the user actually changed; omitted properties leave existing values untouched, while explicit null can clear optional values. Customer and courier users can edit common details; courier city and biography apply to courier profiles. Use the returned profile as the new display state. Theme preference stays local; use the dedicated phone-change and courier-avatar contracts below.

### POST /api/users/me/courier-verification/resubmit

- **API name:** Resubmit Courier Verification.
- **Screens:** Courier Profile `/profile` verification state.
- **Who / authorization:** Authenticated courier with rejected profile; bearer access token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `UserMeResponse` — `id: UUID string`; `public_identifier: integer` (1,000,000–9,999,999); `phone: string`; `role: string`; `status: string`; `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `gender?: string [MALE, FEMALE, OTHER, PREFER_NOT_TO_SAY] | null`; `courier_profile?: CourierProfileResponse | null`
- **Before:** `GET /api/users/me` shows rejected courier verification.
- **Then / dependent API:** `GET /api/users/me` to follow review status.

**When/how (60 words):** Use this action only after a courier has corrected a rejected profile and requests another verification review. It does not verify the courier immediately; the response returns the updated account and courier status for the UI. Do not expose it as a routine profile-save button. Operational courier endpoints remain unavailable until the profile is active and verified by the backend.

### GET /api/users/{user_id}/participant

- **API name:** Get Participant Profile.
- **Screens:** Order detail `/order/[id]`; Chat `/chat/[id]`.
- **Who / authorization:** Authenticated order or conversation participant; bearer access token.
- **Path, query, headers:** `user_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `ParticipantProfile` — `id: UUID string`; `public_identifier: integer` (1,000,000–9,999,999); `display_name: string`; `role: string`; `initials: string`; optional `rating: string | null`, `rating_count: integer | null`, `courier_city: string | null`, `courier_bio: string | null` for couriers only
- **Before:** Obtain the other party's ID from an owned order or inbox item.
- **Then / dependent API:** Optionally `GET /api/users/{user_id}/ratings/summary`.

**When/how (62 words):** Retrieve a minimal public-facing profile for the customer or courier on an order. The backend requires proof that the caller shares an order or conversation with the target; arbitrary account browsing is not supported. Use the seven-digit public identifier, display name, and initials in chat headers. Show rating and courier details only for a courier. Do not expect phone, email, identity documents, or full profile fields.

## Customer wallet and courier earnings

### GET /api/wallets/me

- **API name:** Get My Wallet.
- **Screens:** Customer Home `/home`; Wallet `/wallet`.
- **Who / authorization:** Authenticated active customer; bearer access token. Couriers use courier earnings reports instead.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `WalletResponse` — `balance: string`; `held_balance: string`; `available: string`; `currency: string`
- **Before:** Login and role/profile restoration.
- **Then / dependent API:** `GET /api/wallets/me/transactions`; optional customer top-up.

**When/how (49 words):** Load the signed-in customer's own wallet snapshot. Display balance, held balance, and available funds as decimal-string SAR amounts; never use floating-point arithmetic for financial decisions. Couriers cannot access wallet balances, fund a wallet, or request withdrawals. Use `/api/couriers/me/earnings` and `/api/couriers/me/summary` for courier proceeds.

### POST /api/wallets/topup

- **API name:** Start Topup.
- **Screens:** Wallet `/wallet` add-funds action.
- **Who / authorization:** Authenticated active customer; bearer access token. Couriers cannot top up.
- **Path, query, headers:** None.
- **Request body:** `TopupRequest` — `amount: string`
- **Response:** HTTP 201; `TopupResponse` — `payment_intent_id: string`; `amount: string`; `payment_url?: string | null`
- **Before:** `GET /api/wallets/me` and a user-entered positive decimal amount.
- **Then / dependent API:** Return to wallet and transaction list after settlement.

**When/how (59 words):** Start a wallet funding attempt with a positive decimal-string amount. In development the fake payment flow may settle locally and return a null payment URL; production currently returns `PAYMENTS_DISABLED` before any intent or balance change because Dhamen is not integrated. The mobile UI must not present this as a working production checkout or treat a browser return as settlement.

Couriers do not have a withdrawal-request API or self-service payout flow. They use the read-only courier earnings endpoints to see settled proceeds after platform fees. A Dhamen weekly payout, when configured in the provider account, is provider-managed; this backend does not schedule, initiate, or confirm that batch. The courier payout IBAN is managed by an administrator in the courier profile table and is never exposed to courier APIs. Historical withdrawal records and admin-only lifecycle operations remain for existing records.

### GET /api/wallets/me/transactions

- **API name:** List My Transactions.
- **Screens:** Wallet `/wallet`; courier Statement `/statement` preview.
- **Who / authorization:** Authenticated customer or eligible verified courier; bearer access token.
- **Path, query, headers:** `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `TransactionPage` — `items: TransactionResponse[]`; `next_cursor?: string | null`
- **Before:** Login; usually `GET /api/wallets/me`.
- **Then / dependent API:** Same endpoint with `next_cursor` for more rows.

**When/how (60 words):** Read the owner's ledger entries newest first using `limit` and the returned `next_cursor`. Each item contains signed decimal-string amount, transaction type and status, balance after, and creation timestamp. Append pages without inventing totals from only the loaded slice. The backend provides no date-range filters or statement summary, so the courier statement screen cannot be fully integrated from this endpoint alone.

## Image upload

### POST /api/media/upload-urls

- **API name:** Create upload URL.
- **Screens:** Create order `/request`; Delivery `/delivery/[id]`.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `UploadUrlRequest` — `purpose: string [ORDER_REQUEST, DELIVERY_PROOF]`; `content_type: string [image/jpeg, image/png, image/heic, image/heif]`; `byte_size: integer`
- **Response:** HTTP 201; `UploadUrlResponse` — `upload_url: string`; `storage_key: string`; `expires_in: integer`
- **Before:** Choose image, purpose, MIME type, and exact byte size.
- **Then / dependent API:** Direct signed PUT, then `POST /api/media/confirm`.

**When/how (59 words):** Request a five-minute signed URL for one JPEG or PNG, declaring whether it is an order request photo or delivery proof. Upload bytes directly to the returned URL with the issued `Content-Type`, `Content-Length`, and `If-None-Match: *` headers; the API does not receive the file body. Keep the returned storage key for confirmation and later attachment to the matching operation.

### POST /api/media/confirm

- **API name:** Confirm Upload.
- **Screens:** Create order `/request`; Delivery `/delivery/[id]`.
- **Who / authorization:** Authenticated owner of the upload grant; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `ConfirmRequest` — `storage_key: string`
- **Response:** HTTP 200; `ConfirmResponse` — `storage_key: string`; `confirmed: boolean`
- **Before:** Successful direct PUT from `POST /api/media/upload-urls`.
- **Then / dependent API:** `POST /api/orders` or `POST /api/orders/{order_id}/deliver`.

**When/how:** Confirm an uploaded object by its server-generated storage key after the direct PUT completes. The backend checks ownership, declared type and size, actual object presence, and full isolated image decoding with a 20-million-pixel ceiling. JPEG/PNG order photos and proof use the same bounded decoder as avatars and HEIF; file headers alone do not establish validity. Only confirmed keys may be attached, and each can be claimed once for its original purpose. A local file URI or an unconfirmed key is not a durable order or delivery attachment.

## Orders and fulfillment

### POST /api/orders

- **API name:** Create Order.
- **Screens:** Customer Create order `/request`.
- **Who / authorization:** Authenticated customer; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `CreateOrderRequest` — `description?: string | null`; exactly one of `delivery_city_id: UUID string` (preferred) or `delivery_city: string` (legacy); `delivery_date: date (YYYY-MM-DD)`; `request_media_keys?: string[]`
- **Response:** HTTP 201; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_city_id: UUID string`; `delivery_date: string`; `description: string | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Optional `POST /api/media/upload-urls` → direct PUT → confirm for 0–3 photos.
- **Then / dependent API:** `GET /api/orders/{order_id}` and `/waiting/[id]`; courier `GET /api/orders/available`.

**When/how:** Create a gift request for a city and Gregorian delivery date. No precise delivery location is collected. Send at most three previously confirmed `ORDER_REQUEST` storage keys, not local file URIs. The server returns a `NEW` order and generated UUID. Use that UUID for waiting and detail navigation; fetch its details through the participant endpoint.

### GET /api/orders

- **API name:** List Orders.
- **Screens:** Customer/Courier Home `/home`; Orders `/orders`; Courier Calendar `/calendar` partial.
- **Who / authorization:** Authenticated customer or eligible verified courier; bearer access token.
- **Path, query, headers:** `status?: string | null`; UUID `cursor?`; `limit` 1–100 (default 20); `from_date?`, `to_date?` Gregorian `YYYY-MM-DD` inclusive delivery-date bounds.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderListResponse` — `items: OrderSummary[]`; `next_cursor?: string | null`
- **Before:** Login and profile restoration.
- **Then / dependent API:** `GET /api/orders/{order_id}`; same endpoint with next cursor.

**When/how:** List customer-owned or courier-assigned orders by `created_at DESC, id DESC`.
Optional `from_date` and `to_date` filter `delivery_date` directly, including both
boundaries; status and ownership restrictions apply to every page and cursor anchor.
Omitted bounds preserve existing behavior. Courier availability is a separate route
and has no new filters. Reset the cursor when dates or status change. Reuse identical
filters on subsequent pages. Malformed dates or reversed ranges return the existing
HTTP 422 validation response; a cursor outside the filtered authorized list returns 404.

### Calendar range requests (orders and occasions)

```http
GET /api/orders?from_date=2026-10-04&to_date=2026-10-10&limit=20
GET /api/occasions?from_date=2026-10-04&to_date=2026-10-10&limit=25

GET /api/orders?from_date=2026-10-06&to_date=2026-10-06
GET /api/occasions?from_date=2026-10-06&to_date=2026-10-06

GET /api/orders?from_date=2026-10-01&to_date=2026-10-31&status=NEW
GET /api/occasions?from_date=2026-10-01&to_date=2026-10-31
```

These examples cover a homepage week, selected day, and calendar month. Bearer
authentication is required. Orders retain newest-created-first ordering; occasions
use `occasion_date ASC, id ASC`. Occasions are customer-only. Boundaries compare
Gregorian DATE fields directly, without UTC/UTC+3 conversions. Either boundary can
be omitted. No additional calendar-range or historical-read restriction is introduced.
Existing order-creation date bounds remain unchanged.

Both return existing `{ "items": [...], "next_cursor": UUID-or-null }` responses.
An empty valid range returns `{ "items": [], "next_cursor": null }`. Continue with
the returned cursor and identical date/status/limit parameters; clear the cursor
whenever the range or status changes. Cursor anchors must belong to the same
authorized filtered collection (404 otherwise). Stable date/ID or timestamp/ID
keysets prevent duplicates/skips for unchanged datasets. Concurrent edits can change
membership; this is not a snapshot across multiple HTTP requests.

Invalid dates, timestamps supplied as dates, and `from_date > to_date` return the
existing HTTP 422 validation format. For example, `2026-02-30` is invalid; the
reversed-range error identifies query `to_date`. Text search remains unavailable.

Existing indexes scope orders by customer/courier and creation order, and occasions
by user/date. No speculative migration is added. Date bounds are SQL predicates
before ORDER BY/LIMIT; order cursor lookup uses the same bounds. Real PostgreSQL
EXPLAIN/latency validation is pending a disposable environment, particularly for
narrow delivery-date ranges across large customer/courier histories. A scoped
delivery-date index would add write/storage costs and still require ordering work;
add it only when representative plans justify it.

### GET /api/orders/available

- **API name:** Available Orders.
- **Screens:** Courier Orders `/orders` available tab; Home `/home` radar.
- **Who / authorization:** Authenticated active verified courier; bearer access token.
- **Path, query, headers:** `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderListResponse` — `items: OrderSummary[]`; `next_cursor?: string | null`
- **Before:** Courier verification complete; matching city stored on profile.
- **Then / dependent API:** `POST /api/orders/{order_id}/accept`.

**When/how:** Load a paginated radar of `NEW` orders in the courier's city. Summaries intentionally omit customer identity and full media, so use this list for selection rather than a full private detail screen. The courier cannot read an unassigned order's participant detail through the normal detail route. Accepting is an explicit action and may fail if another courier wins the race.

### GET /api/orders/{order_id}

- **API name:** Get Order.
- **Screens:** Waiting `/waiting/[id]`; Order detail `/order/[id]`; Delivery `/delivery/[id]`.
- **Who / authorization:** Authenticated customer or assigned eligible courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_city_id: UUID string`; `delivery_date: string`; `description: string | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Order UUID from create, owned list, or accepted order.
- **Then / dependent API:** Active invoice, participant profile, chat, rating, or state transition.

**When/how:** Fetch the authoritative state of an order the caller participates in. Customers can inspect their own order; couriers gain participant access once assigned. The response includes status, city, date, computed amount, and assignment time. It does not include a full timeline, attached image URLs, delivery proof gallery, or invoice items; fetch related resources separately where endpoints exist.

### POST /api/orders/{order_id}/accept

- **API name:** Accept Order.
- **Screens:** Courier Orders `/orders` available tab; Order detail `/order/[id]`.
- **Who / authorization:** Authenticated active verified courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_city_id: UUID string`; `delivery_date: string`; `description: string | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** `GET /api/orders/available` supplies a `NEW` order UUID.
- **Then / dependent API:** `GET /api/orders/{order_id}`, chat, then invoice creation.
- **Expected failures:** `403 FORBIDDEN` when the courier account is not eligible; `404 NOT_FOUND` when the order is missing or belongs to another city; `409 ORDER_ALREADY_ASSIGNED` when the order is no longer `NEW` or another courier wins the claim race. Refresh available orders after a conflict.

**When/how (74 words):** Claim a `NEW` order as the current active, verified courier. The backend checks the courier's assigned city and current order state under locks; there is no active-assignment count limit. The first valid acceptance wins. Do not show assignment as complete until the response succeeds, because another courier may accept concurrently. The response provides assigned order detail; use its UUID and status to open chat and later issue an invoice. If the request returns `ORDER_ALREADY_ASSIGNED`, refresh the list instead of repeatedly submitting that order.

### POST /api/orders/{order_id}/cancel

- **API name:** Cancel Order.
- **Screens:** Waiting `/waiting/[id]`; Order detail `/order/[id]`.
- **Who / authorization:** Authenticated customer or assigned eligible courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `CancelOrderRequest` — `reason?: string | null`
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_city_id: UUID string`; `delivery_date: string`; `description: string | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Participant order still in a cancellable pre-progress state.
- **Then / dependent API:** Refresh `GET /api/orders/{order_id}` and owned list.

**When/how (65 words):** Cancel a participant order before work is in progress, optionally recording a short reason. The service rechecks the locked state, so a concurrent acceptance or payment transition may cause a rejection; display the server result rather than optimistic finality. A known backend gap can leave an issued invoice and wallet reservation pending when cancelling from `WAITING_PAYMENT`; do not promise immediate funds release in that state.

### POST /api/orders/{order_id}/deliver

- **API name:** Deliver Order.
- **Screens:** Courier Delivery `/delivery/[id]`.
- **Who / authorization:** Authenticated active verified assigned courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `DeliverRequest` — `proof_media_keys: string[]`; `note?: string | null`
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_city_id: UUID string`; `delivery_date: string`; `description: string | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Order `IN_PROGRESS`; 1–5 confirmed `DELIVERY_PROOF` image keys.
- **Then / dependent API:** Customer `POST /api/orders/{order_id}/approve` or dispute.

**When/how:** Submit one to five confirmed delivery-proof photo keys after completing delivery. The backend checks courier assignment, order state, and ownership and purpose of each image before marking the order delivered. It does not collect a precise delivery location, calculate distance, or verify physical presence.

### POST /api/orders/{order_id}/approve

- **API name:** Approve Order.
- **Screens:** Customer Order detail `/order/[id]` after delivery.
- **Who / authorization:** Authenticated customer who owns the order; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_city_id: UUID string`; `delivery_date: string`; `description: string | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Order is `DELIVERED` and proof reviewed.
- **Then / dependent API:** `POST /api/orders/{order_id}/ratings`; refresh wallet/order.

**When/how (62 words):** Approve the courier's delivered order when the customer accepts the proof. The backend completes the order and releases escrow according to its financial rules; the request has no body. Treat the returned status as authoritative, then refresh related wallet and rating state. A courier cannot approve on behalf of the customer. If the customer disagrees, use the dispute action instead of approval.

### POST /api/orders/{order_id}/dispute

- **API name:** Dispute Order.
- **Screens:** Order detail `/order/[id]` dispute action.
- **Who / authorization:** Authenticated customer or assigned eligible courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `DisputeRequest` — `reason: string`
- **Response:** HTTP 201; `DisputeResponse` — `id: string`; `order_id: string`; `status: string`; `reason: string`; `resolution_note?: string | null`
- **Before:** Participant order in a disputable state.
- **Then / dependent API:** Refresh order; admin resolution occurs outside mobile API.

**When/how (62 words):** Open a dispute with a reason of at least three characters when a delivery or financial outcome is contested. The backend freezes the appropriate escrow path and returns a dispute ID and status. Show that resolution is pending; there is no customer/courier resolution endpoint. Do not infer a refund or payout from successful dispute creation, because an administrator must resolve it later.

## Invoices and checkout

### POST /api/orders/{order_id}/invoices

- **API name:** Create Invoice.
- **Screens:** Courier Invoice `/invoice/[id]` authoring.
- **Who / authorization:** Authenticated active verified assigned courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `CreateInvoiceRequest` — `items: InvoiceLineRequest[]`; `courier_fee_amount?: string`; `promo_code?: string | null`
- **Response:** HTTP 201; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Accepted `ASSIGNED` order; collect 1–20 line items.
- **Then / dependent API:** Customer `GET /api/orders/{order_id}/invoice` and promo/payment flow.

**When/how:** Issue an itemized invoice for an assigned order. Send line titles, final unit prices, quantities, optional descriptions, courier fee, and optional promo code as decimal strings where applicable. The server computes service fees, discounts, totals, revision-backed storage, and expiry. No separate tax rate is accepted and no VAT is added. Use the returned invoice amounts; the mobile app must not send or calculate a trusted final total.

### GET /api/orders/{order_id}/invoice

- **API name:** Get Order Invoice.
- **Screens:** Customer/Courier Order detail `/order/[id]`; Invoice `/invoice/[id]`.
- **Who / authorization:** Authenticated customer or assigned courier participant; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Owned/assigned order with an active invoice.
- **Then / dependent API:** `GET /api/invoices/{invoice_id}`, promo preview, pay, or courier cancel.

**When/how (64 words):** Fetch the current active invoice for a participant order when the UI knows the order UUID but not the invoice UUID. Render the server's itemized amounts and status as decimal strings. This route is for the active invoice, not an invoice history list; a cancelled or expired invoice may no longer be the active result. Use the returned ID for payment or invoice-specific navigation.

### GET /api/invoices/{invoice_id}

- **API name:** Get Invoice.
- **Screens:** Customer/Courier Invoice `/invoice/[id]`; Order detail `/order/[id]`.
- **Who / authorization:** Authenticated customer or assigned courier participant; bearer access token.
- **Path, query, headers:** `invoice_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Invoice UUID from creation or active-order invoice.
- **Then / dependent API:** Customer payment or courier cancellation when eligible.

**When/how (63 words):** Read one invoice by its UUID after participant ownership checks. Unlike the order-scoped active-invoice route, this address can identify the particular invoice the screen is showing, including one whose status has changed. Display item lines, fee breakdown, promo snapshot, issue and expiry times, and total directly from the response. The backend does not expose saved card details or a collection of invoice revisions.

### POST /api/invoices/{invoice_id}/promo (2026-10-04)

- **API name / screen:** Apply, replace or remove invoice promo; customer Invoice/Checkout.
- **Who / authorization:** ACTIVE owning CUSTOMER; bearer token. Foreign invoices return 404.
- **Path / headers:** UUID `invoice_id`; required `Idempotency-Key` string, 1–128 characters.
- **Request body:** `ApplyInvoicePromoRequest`: required `code` string (nonblank, max 32) or null to remove. Unknown properties rejected. Codes are trimmed and case-insensitive.
- **Response:** HTTP 200, full `InvoiceResponse` including authoritative items, discount, total, expiry and ID; no VAT fields.
- **Before / after:** Load current invoice; optionally preview; apply/remove; replace displayed invoice with response; pay the returned invoice ID.

Apply a customer-selected promo only to a current, unpaid, unexpired ISSUED invoice
whose order is WAITING_PAYMENT and has no unresolved payment attempt. A revision
preserves the old financial history, original final item prices, fees and deadline.
The invoice ID can change. A repeated operation key and normalized request replays
its result without another reservation. Invalid replacements roll back. The promo
is consumed only on settlement; application does not enable production payments.

Errors: 401 authentication; 403 role/account eligibility; 404 ownership; 409 stale
state, in-flight payment, conflicting key or missing original pricing policy; 422
invalid input or specific `PROMO_*` eligibility/usage errors; 429 rate limiting.
Legacy invoices without a stored pricing-policy snapshot return 409 for changes.
Existing applied codes/no-code removals are safe no-ops. Idempotent replays return
their original result invoice; refresh active state if it has since been superseded.
[project documentation](documentation.md).

### POST /api/invoices/{invoice_id}/pay

- **API name:** Pay Invoice.
- **Screens:** Customer Invoice and checkout `/invoice/[id]`.
- **Who / authorization:** Authenticated customer who owns the invoice order; bearer access token.
- **Path, query, headers:** `invoice_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `PayInvoiceResponse` — `invoice_id: string`; `status: string`; `amount_from_wallet: string`; `amount_from_gateway: string`; `payment_url?: string | null`
- **Before:** Issued invoice and, if using wallet funds, current wallet snapshot.
- **Then / dependent API:** Refresh invoice, order, wallet, and transactions after verified settlement.

**When/how (59 words):** Ask the backend to fund an issued invoice using its own wallet/gateway split rules; there is no client-selected amount or payment-method body. A `PENDING` response may contain a URL for gateway completion, while `PAID` indicates settlement in supported flows. Production currently returns `PAYMENTS_DISABLED` because live Dhamen is not implemented. Never treat a browser redirect alone as proof of payment.

### POST /api/invoices/{invoice_id}/cancel

- **API name:** Cancel Invoice.
- **Screens:** Courier Invoice `/invoice/[id]` correction action.
- **Who / authorization:** Authenticated issuing courier; bearer access token.
- **Path, query, headers:** `invoice_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Issued, unpaid invoice on assigned order.
- **Then / dependent API:** `POST /api/orders/{order_id}/invoices` for corrected version.

**When/how (60 words):** Let the issuing courier cancel an unpaid invoice to correct item lines or fees. The backend releases its promo and pending wallet reservation, marks the invoice cancelled, and reopens the order for a new invoice. It rejects already paid or otherwise ineligible invoices. Refresh the order and invoice display after success; do not edit the immutable original invoice in place.

## Promotions

### POST /api/promos/validate

- **API name:** Validate Promo.
- **Screens:** Customer Invoice and checkout `/invoice/[id]` promo entry.
- **Who / authorization:** Authenticated customer who owns the order; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `PromoValidateRequest` — `code: string`; `order_id: string`
- **Response:** HTTP 200; `PromoPreviewResponse` — `code: string`; `discount_amount: string`; `original_total_amount: string`; `total_amount: string`
- **Before:** Active invoice for `order_id`.
- **Then / dependent API:** Apply through `POST /api/invoices/{invoice_id}/promo`; only the application response changes the payable invoice. Preview reserves nothing.

**When/how (61 words):** Preview a promo against the customer's own order and active invoice before showing a discounted total. Send the code and order UUID; the backend checks eligibility and computes the proposed discount. This endpoint does not reserve, consume, or settle the promo. Treat the response as a preview only, and continue to use the final invoice/payment response as the authoritative charged amount.

## Ratings

### POST /api/orders/{order_id}/ratings

- **API name:** Rate Order.
- **Screens:** Customer Order detail `/order/[id]` after completion.
- **Who / authorization:** Authenticated order customer; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `RatingRequest` — `score: integer`; `comment?: string | null`
- **Response:** HTTP 201; `RatingResponse` — `id: string`; `order_id: string`; `rated_user_id: string`; `score: integer`; `comment?: string | null`
- **Before:** Order completed and caller has not already rated it.
- **Then / dependent API:** `GET /api/users/{user_id}/ratings/summary`; refresh order detail.

**When/how (65 words):** After the order reaches backend status `COMPLETED`, its customer can submit one score from one to five and an optional short comment for the assigned courier. The backend derives the courier from the order; never send or select a target user ID. Use `current_actor_has_rated` to hide repeat submission. Couriers cannot rate customers through this endpoint. Display the returned rating only after the request succeeds.

### GET /api/users/{user_id}/ratings/summary

- **API name:** Courier Rating Summary.
- **Screens:** Order detail `/order/[id]`; Chat `/chat/[id]` profile header.
- **Who / authorization:** Any authenticated customer or courier; bearer access token.
- **Path, query, headers:** `user_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `RatingSummaryResponse` — `user_id: string`; `average_score: string`; `count: integer`
- **Before:** Courier UUID from an order or participant profile.
- **Then / dependent API:** Optionally participant profile or rating action on completed order.

**When/how (60 words):** Fetch a courier's aggregate received rating and count for a compact trust indicator. The average and count are computed from customer ratings on completed orders, not stored on the generic user record. This endpoint needs authentication but does not require a shared order with the courier. Use the decimal-string average for display. It does not return individual ratings or comments.

## Conversations and chat

### GET /api/conversations

- **API name:** List Conversations.
- **Screens:** Chat inbox; Customer/Courier Home `/home` optional summary.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `InboxResponse` — `items: InboxItemResponse[]`; `next_cursor?: string | null`
- **Before:** An accepted order creates a conversation.
- **Then / dependent API:** Messages page or WebSocket for selected conversation.

**When/how (59 words):** List the caller's conversations ordered by recent activity. Each row provides its order and other-user IDs, last message preview, unread count, and timestamp; the response is cursor paginated. Use the returned `conversation_id` to open chat, not the order ID as a substitute. This API contains safe inbox data only for the participant and can support a home notification badge.

### GET /api/orders/{order_id}/conversation

- **API name:** Find Order Conversation.
- **Screens:** Customer/Courier old-order detail and Chat `/chat/[id]`.
- **Who / authorization:** Authenticated customer or courier who belongs to the order; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path); no query parameters.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `ConversationResponse` — `conversation_id: string`; `order_id: string`; `other_user_id: string` (all UUID strings).
- **Before:** An order was accepted and its conversation was created.
- **Then / dependent API:** `GET /api/conversations/{conversation_id}/messages`; optional live WebSocket.

**When/how (62 words):** When a customer or courier reopens any assigned order, including a completed order, use its order ID to find the conversation ID directly. This avoids scanning every page of the inbox for an old thread. A missing, unassigned, or unrelated order returns 404. Once found, request the first REST message page, then follow its cursor for older history. Open the WebSocket only when live updates are needed.

### GET /api/conversations/{conversation_id}/messages

- **API name:** List Messages.
- **Screens:** Customer/Courier Chat `/chat/[id]`.
- **Who / authorization:** Authenticated conversation participant; bearer access token.
- **Path, query, headers:** `conversation_id: UUID string` (path); `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `MessagePage` — `items: MessageResponse[]`; `next_cursor?: string | null`
- **Before:** Conversation ID from inbox after order assignment.
- **Then / dependent API:** Same endpoint with next cursor; WebSocket or REST send.

**When/how (69 words):** Load the conversation's decrypted text and system messages, newest first, in bounded pages. Use `next_cursor` unchanged to retrieve older messages and reverse/display them as needed in chronological chat order. The backend enforces membership; a cursor from a missing or different conversation returns 404 instead of restarting history. After opening the thread, connect the WebSocket for live events and use read acknowledgement to clear unread counts when the messages are visible.

### POST /api/conversations/{conversation_id}/messages

- **API name:** Send Message.
- **Screens:** Customer/Courier Chat `/chat/[id]` composer.
- **Who / authorization:** Authenticated conversation participant; bearer access token.
- **Path, query, headers:** `conversation_id: UUID string` (path).
- **Request body:** `SendMessageRequest` — `text: string`; `client_message_id?: UUID string | null`
- **Response:** HTTP 201; `MessageResponse` — `id: string`; `conversation_id: string`; `sender_id: string`; `message_type: string`; `content: string`; `is_read: boolean`; `created_at: string`
- **Before:** Conversation exists and actor is a participant.
- **Then / dependent API:** WebSocket receives committed event; refresh messages/inbox as needed.

**When/how:** Send nonempty text of at most 4,000 characters. Generate an optional `client_message_id` UUID once per intended send and retain it across retries. Within the same conversation and sender, repeating that ID with the same text returns the persisted message with HTTP 201 and its original message ID and timestamp; different text or media payload returns `409 CONFLICT`. Omitting the ID preserves existing send behavior and provides no client retry deduplication. Render returned and live messages once per server message ID. The server commits encrypted content and a durable live-delivery intent before publication; generic push notifications omit message text.

### POST /api/conversations/{conversation_id}/read

- **API name:** Mark Read.
- **Screens:** Customer/Courier Chat `/chat/[id]` when opened/read.
- **Who / authorization:** Authenticated conversation participant; bearer access token.
- **Path, query, headers:** `conversation_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 204; No body.
- **Before:** Conversation and visible messages loaded.
- **Then / dependent API:** Refresh inbox unread count if displayed.

**When/how (61 words):** Acknowledge the caller's inbound messages as read and clear that conversation's unread count. The request has no JSON body and returns 204 without a payload. Call when the chat is genuinely visible to the user, rather than immediately on a background push event. Other participants' read state cannot be set by this caller. Refresh the inbox badge after success if necessary.

## Chat media — 2026-10-04

The customer/courier Chat screen supports private recorded voice notes and camera/
gallery images and videos. Use the full chat media handoff (see documentation.md)
for exact requests, responses, accepted MIME types, limits and error handling.

| API | Input | Output | Authorization / dependencies |
| --- | --- | --- | --- |
| `GET /api/chat/media-limits` | No body | `ChatMediaLimitsResponse` — integer size/duration/pixel limits and MIME string arrays | Eligible customer/courier bearer token; load before recording/selection. |
| `POST /api/conversations/{conversation_id}/media-upload-urls` | `ChatUploadRequest`: `media_type: IMAGE\|VIDEO\|VOICE`, `content_type: string`, `byte_size: integer` | 201 `UploadUrlResponse`: `upload_url: string`, `storage_key: string`, `expires_in: integer` | Eligible conversation participant; then direct create-only signed S3 PUT. |
| `POST /api/conversations/{conversation_id}/media-messages` | `SendChatMediaRequest`: `storage_keys: string[1..5]`, `text?: string`, `client_message_id?: UUID string | null` | 201 `MessageResponse` with `attachments: ChatAttachmentResponse[]` | Same participant; successful S3 upload required. No separate chat confirm endpoint. |
| `GET /api/chat/attachments/{attachment_id}/url` | UUID path; no body | `AttachmentUrlResponse`: `url: string`, `expires_in: integer` | Eligible conversation participant; fetch on demand for private playback. |

**When/how:** Load configured limits, record a microphone voice note or select camera/
gallery media, then issue one conversation-scoped upload grant per file. Upload exact
bytes directly to the signed S3 URL with matching content type and `If-None-Match: *`.
Send returned keys through the media-message endpoint. The backend validates real
bytes and decoded duration before atomically consuming grants. History and live events
include attachment metadata; obtain temporary playback URLs only when needed.

Images default to 10 MiB each, videos to 120 MiB/120 seconds, and voice notes to
10 MiB/120 seconds. Send five images or one video or one voice note per message.
Message `attachments` entries contain `id: UUID string`, `content_type: string`,
`byte_size: integer`, `duration_seconds: number|null`, `display_order: integer`.
Text messages have an empty attachment array. Voice microphone origin is a UI
requirement; the server cannot attest file provenance. Missing decoder/capacity returns
503, and reused grants return 409. Rebuild the image and apply migration `0015_chat_media`;
real private-storage deployment still needs staging verification.

Media sends use the same optional retry UUID, scoped to conversation and sender. Retrying the same text and ordered storage-key list returns the original persisted message with HTTP 201, including after its grants were consumed. A changed text or attachment list for that ID returns `409 CONFLICT`. Keep the retry ID and uploaded keys until acknowledgement; begin a new send with a new ID.

## Push devices

### POST /api/devices

- **API name:** Register Device.
- **Screens:** Shared app startup/login; notification permission flow.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `RegisterDeviceRequest` — `token: string`; `device_os: string`
- **Response:** HTTP 201; `DeviceResponse` — `token: string`; `device_os: string`
- **Before:** OS push token obtained after user permission.
- **Then / dependent API:** Push delivery for order and chat notices; delete on sign-out.

**When/how:** Register or refresh the current device's push token using `IOS` or `ANDROID`. The server associates that token with the authenticated account and can move a handed-down device token away from a previous owner. Register again after token rotation or a successful new login. Each account can register at most 10 device tokens. Refreshing its existing token remains allowed at the quota; adding another returns `409 DEVICE_LIMIT_REACHED`. Delete an owned registration before adding another. Do not confuse this with an in-app notification-list API; use GET /api/notifications for the separate owned notification feed.

### DELETE /api/devices

- **API name:** Unregister Device.
- **Screens:** Shared logout/device settings.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `UnregisterDeviceRequest` — `token: string`
- **Response:** HTTP 204; No body.
- **Before:** A token previously registered by this account.
- **Then / dependent API:** `POST /api/auth/logout` when signing out.

**When/how (64 words):** Unregister the current device's push token when push permission is removed, the token changes, or the user signs out. Send the exact token in the JSON body; the backend removes it only if owned by the caller. The response is 204 with no body. Logout revokes credentials, but the UI should also explicitly remove device registration when it still has a valid access token.

## Development-only diagnostics

### GET /api/dev/ping

- **API name:** Development liveness marker.
- **Screens:** None; developer diagnostics only.
- **Who / authorization:** No bearer token; development environment only.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `{"status":"dev"}` (`status: string`).
- **Before:** Development server started.
- **Then / dependent API:** None.

**When/how (62 words):** Use this endpoint only to confirm that development-only routes were registered on a local environment. It returns a static `dev` marker and is intentionally absent in production, where the same URL returns 404. It provides no user, order, payment, or service readiness data. Mobile integration should never depend on it; use the actual protected endpoints for screen data and operator readiness separately.

### POST /api/dev/simulation/simulate

- **API name:** Simulate Payment.
- **Screens:** None; developer payment testing only.
- **Who / authorization:** No bearer token; development environment only.
- **Path, query, headers:** None.
- **Request body:** `SimulatePaymentRequest` — `payment_link_id: string`; `status?: string`
- **Response:** HTTP 200; `WebhookAck` — `outcome: string`
- **Before:** Simulated payment link ID from a local fake checkout.
- **Then / dependent API:** Server callback settlement; inspect normal invoice/wallet/order reads.

**When/how (63 words):** Trigger a correctly signed local fake callback for a pending simulated payment link. This development helper looks up the server-side amount, constructs the fake event, and runs the ordinary simulation webhook handler; it is not a real checkout or Dhamen integration. Never call it from a distributed mobile build or expose it as a customer payment button. Production does not register this route.

## Simulation webhook (integration only)

### POST /api/webhooks/simulation

- **API name:** Simulation Webhook.
- **Screens:** None; integration/test harness only.
- **Who / authorization:** No bearer token; valid `X-Webhook-Signature` over raw body; absent in production.
- **Path, query, headers:** `x-webhook-signature?: string` (header).
- **Request body:** Raw signed JSON: `event_type: string`; `data.payment_link.id: string`; `data.payment.status: string`; `data.payment.amount: decimal string`. Preserve exact bytes for HMAC.
- **Response:** HTTP 200; `WebhookAck` — `outcome: string`
- **Before:** A simulated payment intent in nonproduction and gateway test signature.
- **Then / dependent API:** Server-side settlement; mobile refreshes wallet/invoice/order.

**When/how (61 words):** This callback is for the local simulated gateway, not a mobile app action and not a Dhamen endpoint. Its raw JSON body identifies a simulated payment link, status, and amount; the signature header authenticates the exact bytes. The backend handles idempotent settlement or failure in a transaction and returns an outcome. Never place the simulation signing secret in a phone app.

## Customer occasions API (2026-10-04)

Customers can now manage their own birthdays, anniversaries and other calendar
dates. All five operations require a CUSTOMER bearer token; missing or foreign
records/cursors return 404. Couriers and admins cannot use these customer APIs.

| Method | Endpoint | Input | Success |
| --- | --- | --- | --- |
| POST | `/api/occasions` | `CreateOccasionRequest` | 201 `OccasionResponse` |
| GET | `/api/occasions` | `limit` 1–100 (default 25), optional UUID `cursor`, inclusive `from_date`/`to_date` Gregorian YYYY-MM-DD | 200 `OccasionPage` |
| GET | `/api/occasions/{occasion_id}` | UUID path; no body | 200 `OccasionResponse` |
| PATCH | `/api/occasions/{occasion_id}` | `UpdateOccasionRequest`; nonempty partial update | 200 `OccasionResponse` |
| DELETE | `/api/occasions/{occasion_id}` | UUID path; no body | 204; no body |

Create fields: required `title` (nonblank string, 1–120 characters), required
`occasion_date` (YYYY-MM-DD), optional `reminder_days_before` (integer 0–365,
default 7). PATCH supports the same fields, all optional but at least one supplied;
explicit nulls are rejected. Ownership, IDs and timestamps cannot be assigned.
Response fields: `id`, `title`, `occasion_date`, `reminder_days_before`, UTC
`created_at` and `updated_at`. Lists return `items` and nullable UUID `next_cursor`,
ordered by date then ID ascending. No per-item relationship queries are needed.

Use this API when a customer saves or manages a personal gifting date from Calendar
or Home. Fetch a bounded page, display dates without timezone conversion, and pass
the returned cursor unchanged for more results. Create/edit responses provide the
record to reconcile local state. Titles remain text and must be escaped by clients.
Reminder preferences are stored only: automatic notification delivery and annual
recurrence are not implemented. These records do not create orders automatically.

[project documentation](documentation.md) for examples, errors
and UI instructions. The generated schemas are included in `mobile-openapi.json`.

## Live order status WebSocket (2026-10-04)

`/api/ws/orders/{order_id}` is a read-only authenticated stream for the owning customer
and the assigned active, verified courier. An unassigned courier cannot subscribe.
Use `wss://` in production. Native clients send `Authorization: Bearer ACCESS_TOKEN`;
browser clients offer protocols `giftly.orders` and `bearer.ACCESS_TOKEN`. The server
selects only `giftly.orders`; do not put credentials in the URL or log protocols.

The first event is `order.snapshot`; subsequent changes use `order.updated`:

```json
{"type":"order.updated","order_id":"550e8400-e29b-41d4-a716-446655440000","status":"ASSIGNED","courier_id":"550e8400-e29b-41d4-a716-446655440001","assigned_at":"2026-10-04T10:00:00Z"}
```

`order_id` is a UUID string; `status` uses the existing order status enum;
`courier_id` is a UUID string or null; `assigned_at` is a UTC datetime string or null.
No request body or client application frames are supported. Acceptance sends a Redis
hint only after commit. Other status changes and missed hints reconcile every five
seconds. Each check revalidates credentials and current participation. These checks
use short sessions, rather than a database transaction held for the socket lifetime.

Close codes: 4401 invalid/revoked/expired credentials; 4403 denied origin, role or
ownership; 4429 connection capacity; 4400 unsupported client frame; 1013 temporary
backend failure. A rejection before handshake completion may appear as a failed
handshake instead of a close code. Refresh credentials and reconnect appropriately;
use bounded backoff and reload `GET /api/orders/{order_id}` after reconnect. Close on
logout. The shared limit is eight sockets per account across chat/order streams.

For the complete acceptance integration brief, see
[project documentation](documentation.md).

## Live chat WebSocket

### WS /api/ws/conversations/{conversation_id}

- **API name:** Live conversation stream.
- **Screens:** Customer/Courier Chat `/chat/[id]` live stream.
- **Who / authorization:** Authenticated conversation participant. Prefer `Authorization: Bearer <access JWT>` where supported, or offer WebSocket subprotocols `giftly.chat` and `bearer.<access JWT>`. The server selects only `giftly.chat`, never the credential protocol. Legacy `?token=<access JWT>` remains supported.
- **Path/query:** `conversation_id: UUID string` (path); optional legacy `token: access JWT string` (query). Keep credentials out of URLs and logs when using the preferred handshake methods.
- **Credential precedence:** An Authorization header, when present, is authoritative even if malformed or empty; it never falls back to another transport. Without it, the first offered `bearer.` protocol wins. Without either, the legacy query token is used. Conflicting credentials are not combined.
- **Negotiation:** Native callers may omit subprotocols entirely; no protocol is selected. Browsers offer `["giftly.chat", "bearer." + accessToken]`; only `giftly.chat` is selected. Never place a bearer protocol in application/proxy diagnostics.
- **Handshake failures:** Before upgrade the app requests 4401 for missing/invalid/expired/revoked credentials; 4403 for a forbidden origin or inaccessible/ineligible conversation; 4429 for a connection cap; 1013 for lease-store failure. Uvicorn normally turns any close before acceptance into HTTP 403, so these app codes are not established-socket close frames. Unexpected dependency failures may produce HTTP 5xx. After upgrade, lost authorization/membership closes 4401; Redis subscription failure closes 1013; unexpected failures close 1011. Preserve the existing message/size behavior.
- **Reconnect:** Obtain a new access token through the existing authenticated refresh flow when appropriate, open a new socket, and reload authorized REST history. Do not blindly refresh/reconnect forever on HTTP 403: membership, origin and connection limits can cause it. Use bounded backoff for transient failures; honor Retry-After when provided by HTTP/gateway responses. Production browser origins must be explicitly permitted.
- **Client frame:** JSON `{"text":"message","client_message_id":"11111111-1111-4111-8111-111111111111"}`; `client_message_id` is an optional UUID. Text must be a nonempty string of at most 4,000 characters. Null, objects and unknown fields are invalid; the whole frame also has a 4,096-byte cap.
- **Server frame:** JSON `MessageResponse` — `id: string`; `conversation_id: string`; `sender_id: string`; `message_type: string`; `content: string`; `is_read: boolean`; `created_at: string`
- **Before:** Conversation ID from inbox and a valid access token.
- **Then / dependent API:** Use REST messages/read for history and unread state.

**When/how:** Open the socket while the conversation screen needs live updates. Reuse the same optional retry UUID and payload for an unacknowledged send; the server reuses its persisted message instead of creating a duplicate. Reusing that ID with different text produces an `error` frame with `code: "CONFLICT"`; HTTP sends return 409 for this conflict. Invalid JSON or request-schema fields receive an `error` frame with `code: "VALIDATION_ERROR"` and `message: "Invalid chat message."`; the connection stays open and no message is persisted. HTTP schema errors return 422. Oversized or throttled frames may be dropped. Durable live-delivery intents retry publication after transient failures or process restarts, with bounded attempts and at-least-once publication: duplicates are possible, and publication does not prove a disconnected client received it. Deduplicate by server message `id`, reconnect with a fresh access token, and fetch REST history to recover missed messages. Close on logout or revoked authentication.

## Reusable nested data types

These types appear inside the request/response shapes above. Field names marked `?` may be omitted; `| null` means nullable. An API response may still include a nullable field explicitly as null.

### CourierProfileResponse

- `city_of_residence`: `string`.
- `city_of_residence_id`: `UUID string`.
- `bio?`: `string | null`.
- `verification_status`: `string`.
- `rejection_reason?`: `string | null`.
- `avatar_url?`: `string | null`.

### OrderSummary

- `id`: `string`.
- `status`: `string`.
- `delivery_city`: `string`.
- `delivery_city_id`: `UUID string`.
- `delivery_date`: `string`.
- `description`: `string | null`.
- `created_at`: `string`.
- `current_actor_has_rated`: `boolean`.

### InvoiceLineRequest

- `title`: `string`.
- `description?`: `string | null`.
- `unit_price_amount`: `string`.
- `quantity`: `integer`.

### InvoiceItemResponse

- `position`: `integer`.
- `title`: `string`.
- `description`: `string | null`.
- `unit_price_amount`: `string`.
- `quantity`: `integer`.
- `line_net_amount`: `string`.
- `line_discount_amount`: `string`.
- `line_total_amount`: `string`.

### TransactionResponse

- `id`: `string`.
- `amount`: `string`.
- `type`: `string`.
- `status`: `string`.
- `balance_after`: `string`.
- `created_at`: `string`.

### InboxItemResponse

- `conversation_id`: `string`.
- `order_id`: `string`.
- `other_user_id`: `string`.
- `last_message_preview`: `string | null`.
- `unread_count`: `integer`.
- `last_message_timestamp`: `string`.

### MessageResponse

- `id`: `string`.
- `conversation_id`: `string`.
- `sender_id`: `string`.
- `message_type`: `string`.
- `content`: `string`.
- `is_read`: `boolean`.
- `created_at`: `string`.

## Screen coverage and current gaps

The prototype's labels and local-device data are not server contracts. The table maps each screen to implemented reads/actions and calls out missing backend support. A gap means **API to be added later**, not permission to build or guess a route now; leave that screen action pending until the backend contract exists.

| Mobile screen | Implemented calls | Integration gap or limit |
| --- | --- | --- |
| Welcome `/welcome` | None | Static content. |
| Phone login `/login`, OTP `/verify-otp` | `send-otp`, `verify-otp`, `refresh` | Demo phone-role fixtures and permissive demo OTP are not backend rules. |
| Customer registration `/register-customer` | `register`, `users/me` | Backend supports courier registration too, but mobile has no courier onboarding flow. Name length/age-16 rules from prototype are not enforced here. |
| Help `/help` | None | Static app content; no help/contact API. |
| Customer Home `/home` | `users/me`, `wallets/me`, `orders`, `conversations` | Occasions CRUD and notifications/unread-count are available; no aggregated home endpoint. |
| Create order `/request` | `media/upload-urls`, signed PUT, `media/confirm`, `orders` | Backend allows **0–3** photos, not prototype's four; no morning/evening period, recipient phone, or returned image URLs. |
| Waiting `/waiting/[id]` | `orders/{id}` | No separate push/poll status stream; refresh the order. |
| Customer/Courier Orders `/orders` | `orders`; courier `orders/available` | Customer q supports description-prefix/exactUUID search; no order number exists. Courier search remains excluded. |
| Order detail `/order/[id]` | `orders/{id}`, active invoice, participant, ratings, chat | Read actual history through orders/{id}/timeline; read authorized media through orders/{id}/media. |
| Chat `/chat/[id]` | `conversations`, messages, read, WebSocket | Conversation ID comes from inbox; WebSocket has no durable replay, so use REST history after reconnect. |
| Invoice `/invoice/[id]` | `orders/{id}/invoice`, `invoices/{id}`, promo preview/apply/remove; courier create/cancel; customer pay | Pay the returned current invoice ID after promo changes. No saved-card API. Production payment is disabled. |
| Customer Calendar `/calendar` | `/api/occasions` CRUD | Customer-owned dates support opt-in one-off reminders; annual recurrence remains deferred. |
| Courier Calendar `/calendar` | Assigned orders support inclusive delivery-date ranges for day/week/month views | No appointment CRUD or separate appointments API. |
| Customer Wallet `/wallet` | `wallets/me`, transactions; customer top-up | Couriers do not use wallet balance or withdrawal controls. |
| Courier Reports `/reports` and settings sheet | couriers/me/earnings and couriers/me/report-settings | Settled proceeds, comparisons, buckets and versioned targets are available; financial-notification settings remain deferred. |
| Courier Statement `/statement` | Wallet transactions, cursor-paged | No date-range filtering, statement totals, or downloadable statement API. |
| Profile `/profile` | `users/me`, patch, courier resubmit, logout, device registration | Date of birth and gender are writable and returned by `users/me`; theme stays local; dedicated courier-avatar and phone-change APIs are described below. |

**Important contract mismatches:** The mobile map proposes halala integers, but the backend uses decimal **strings** for money. It proposes local file URIs/upload IDs, but the backend expects confirmed server-generated `storage_key` values. It proposes a matching/accepted/preparing/delivering timeline, while the actual order status enum is `NEW`, `ASSIGNED`, `WAITING_PAYMENT`, `IN_PROGRESS`, `DELIVERED`, `COMPLETED`, `CANCELLED`, `DISPUTED`, `REFUNDED`. Translate those labels only for display; do not send prototype strings to the API. The server does not enforce the prototype's age-16 minimum. Order delivery dates are constrained by the database to today through 180 days ahead; the UI should prevent out-of-range entries.

## Recommended call sequences

1. **Existing login:** `send-otp` → `verify-otp` → store returned token pair → `users/me` → role-specific home data. **New login:** use the returned `registration_token` with `register`, then follow the same profile flow.
2. **Create order with photos:** for each of at most three images, request an `ORDER_REQUEST` upload URL → signed PUT bytes → confirm the key → create the order with confirmed keys → navigate to `/waiting/[id]` using the returned UUID.
3. **Courier fulfillment:** `orders/available` → accept → conversation inbox/chat → create invoice → customer reads invoice and attempts payment only when a supported gateway is enabled → courier uploads/ confirms `DELIVERY_PROOF` → deliver → customer approves or disputes → the customer may rate the courier after completion.
4. **Chat:** get inbox for `conversation_id` → page REST history → open WebSocket while visible → send with REST or JSON WebSocket frames → mark read. On socket reconnect, reload REST history to cover missed ephemeral events.
5. **Customer wallet:** fetch the authenticated customer's snapshot and transactions; top-up only when an enabled payment provider supports it. **Courier proceeds:** use `/api/couriers/me/summary` and `/api/couriers/me/earnings`; do not show wallet-balance, top-up, withdrawal, or IBAN-edit actions. Dhamen's automatic weekly payout is configured with the provider and is not triggered by a courier API call.

## Source and verification

Derived from `app/main.py`, `app/routers/`, `app/schemas/`, service eligibility/state checks, and an **offline** development OpenAPI build with dummy settings. No database, Redis, Docker, real storage, or payment provider was contacted for this document. The OpenAPI HTTP inventory was compared against all router registrations: **82 supported mobile HTTP operations plus two WebSockets** are represented above. `/api/admin/*` and server-rendered `/v1/admin/admin/*` are deliberately excluded. The mobile file supplied with the request was used only to name and map screens, not as authority for backend behavior.

## Invoice PDF and final item prices (2026-10-08)

GET /api/invoices/{invoice_id}/pdf returns private application/pdf bytes to the owning customer or assigned eligible courier. Use the Bearer header; there is no body. Foreign invoices return 404. Courier-entered prices are final, including supplier VAT; the platform adds no VAT. No tax fields or PDF rows remain. See [PDF operations](documentation.md#invoice-pdfs-and-paid-receipts) for deployment limits, migration safeguards and receipt behavior.

## Courier performance compatibility — 2026-10-08

This release changes no public endpoint paths, request/response schemas, role rules,
filters or cursor contracts. Mobile clients require no contract changes. Order reads
and WebSocket snapshots continue to authorize against current account/order state.
Chat push delivery is durable and asynchronous: saved-message acknowledgements do
not wait for push providers; minute-based scheduling and retries can delay pushes.
Redis live-message publication retains its existing timeout. Invoice PDF reuse occurs
only after fresh ownership/content reads; responses remain private,no-store. No
cached balance or stale payment/claim state is used to authorize writes. See
[operational performance details](documentation.md#courier-read-and-delivery-performance--2026-10-08).


## Payment sessions and branded invoice downloads — 2026-10-08

Implemented in this release; deployment must be verified separately. Order/chat
WebSockets are unchanged. Production payment creation, refresh, cancellation and
callbacks remain disabled with503 PAYMENTS_DISABLED. PAYMENT_PROVIDER=auto preserves
development simulation and production disabled selection. Explicit Dhamen testing
requires valid credentials, HTTPS origins/return/callback URLs and exact allowed
checkout hosts. Production Dhamen selection is rejected at startup.

### Owned payment operations

All operations require Bearer authentication and current customer/courier eligibility;
couriers must remain active and verified. Ownership is the payer's user_id, not merely
order participation. Couriers can recover their own top-ups, never customer checkouts.
Admin role is403. Missing/foreign sessions are404 NOT_FOUND with the existing envelope.

| Method | Path | Input | Purpose |
| --- | --- | --- | --- |
| GET | /api/orders/{order_id}/payment-session | UUID order_id; no body | Recover latest owned order attempt after a lost response/restart. |
| GET | /api/wallets/me/topup-session | No body | Recover latest owned hosted top-up, preferring unresolved attempts; simulations excluded. |
| GET | /api/payment-sessions/{intent_id} | UUID intent_id; no body | Read local owned payment state without contacting the provider. |
| POST | /api/payment-sessions/{intent_id}/refresh | UUID intent_id; no body | Verify provider reference/customer/amount, settle and return state. |
| POST | /api/payment-sessions/{intent_id}/cancel | UUID intent_id; no body | Confirm hosted closure before releasing funds and reversing pending rows. |

These single-object operations need no pagination and return Cache-Control: private,
no-store. GET never establishes external payment success. Refresh an unknown outcome
with bounded backoff; do not create another checkout or trust the return-page redirect.
On429 honor Retry-After seconds.

### Dhamen browser return

`GET https://backend.giftly.sa/payment/return` is a public HTML fallback registered as
Dhamen's customer return URL. It selects Arabic or English copy from `Accept-Language`,
attempts the fixed `giftly://payment/return` app link, and shows an `Open Giftly` button
with the same fixed target. Request query parameters are ignored. The page never reports
payment success and performs no financial or database writes. The app must call the
authenticated refresh endpoint below to obtain authoritative state.

`GET /.well-known/apple-app-site-association` serves an iOS association restricted to
`/payment/return` when `APPLE_APP_IDENTIFIER` contains the verified App ID. Android's
`GET /.well-known/assetlinks.json` requires `ANDROID_APPLICATION_ID` and
`ANDROID_SHA256_CERT_FINGERPRINTS` (comma-separated SHA-256 certificate fingerprints).
These files return 503 until valid signing information is configured; placeholder
associations are never served. Android `assetlinks.json` binds an app to the domain, while
the mobile Android intent filter must restrict matching to `/payment/return`.

The mobile project currently declares bundle/package ID `com.giftly.mobile`; the Apple
App ID prefix and Android signing fingerprints still require confirmation from the
signed mobile builds. Universal/App Links remain unavailable until those values are
verified and deployed.

`POST /api/payment-sessions/{intent_id}/refresh` is an authenticated customer/courier
operation with no request body. `intent_id` is a UUID path parameter. The server checks
current account eligibility and intent ownership, queries the configured provider for
authoritative status, validates the checkout reference and amount, then applies settlement
atomically with existing replay protection. In development/testing or when no hosted
provider is selected, it returns the existing local session state without creating a
payment. The response is `PaymentSessionResponse`: `payment_intent_id`, `provider`,
`status`, `checkout_state`, `order_id`, `invoice_id`, `currency`,
`amount_from_wallet`, `amount_from_gateway`, `expires_at`, `payment_url`, `invoice`,
`purpose`, `title`, `description`, and `use_wallet`. Amounts are decimal strings;
`payment_url` is null for inactive/terminal sessions. Existing 404 ownership concealment,
403 account/role denials, 409 unknown or review-required provider outcomes, 422 invalid
UUIDs, and 503 provider unavailability remain unchanged. A redirect or browser query never
establishes payment success.

The receiver remains `POST /api/webhooks/dhamen`. Dhamen callback authentication and
atomic event processing are unchanged. Register
`https://backend.giftly.sa/api/webhooks/dhamen` with the Dhamen testing account.

PaymentSessionResponse fields:
payment_intent_id:string UUID; provider:string; status:PENDING/PAID/FAILED/EXPIRED/CANCELLED;
checkout_state:CREATING/ACTIVE/CLOSING/CLOSED/REVIEW; order_id/invoice_id:string UUID|null;
currency:string; amount_from_wallet/amount_from_gateway:decimal strings; expires_at:
ISO8601 string; payment_url:string|null; invoice:InvoiceResponse|null; purpose:
ORDER_INVOICE/WALLET_TOPUP; title:string; description:string|null; use_wallet:boolean.
The nested invoice is an immutable pricing snapshot: read current invoice detail for its
latest status. Only an unexpired ACTIVE PENDING attempt returns a payment_url.
REVIEW requires support/reconciliation, never another charge.

Illustrative hosted top-up response (URL is a placeholder, never a configured origin):

    {"payment_intent_id":"11111111-1111-4111-8111-111111111111","provider":"DHAMEN","status":"PENDING","checkout_state":"ACTIVE","order_id":null,"invoice_id":null,"currency":"SAR","amount_from_wallet":"0.00","amount_from_gateway":"100.00","expires_at":"2026-10-10T09:00:00+00:00","payment_url":"https://checkout.example.test/pay/example","invoice":null,"purpose":"WALLET_TOPUP","title":"Top up","description":"Top up","use_wallet":false}

409 PAYMENT_SESSION_PENDING retains the attempt while the outcome is unknown.
503 PAYMENT_PROVIDER_UNAVAILABLE is neither failure nor payment confirmation.
409 CONFLICT includes changing amount or wallet use behind an open hosted checkout.

### Compatible creation contracts

POST /api/wallets/topup retains request amount:string and original response
payment_intent_id, amount, payment_url, adding status:string and session_reused:boolean.
MIN_TOPUP_AMOUNT/MAX_TOPUP_AMOUNT remain authoritative. Same-amount active hosted
attempts are reused under a payer lock; different amounts conflict until closure.
An optional UUID Idempotency-Key now provides 24-hour exact-input replay, including
development credit. See write recovery below. Development auto simulation returns
PAID with payment_url=null; simulated credit is not externally funded money.

POST /api/invoices/{invoice_id}/pay remains customer-only; omitted body preserves
use_wallet=true, or send {"use_wallet":false}. This is a strict boolean; client amounts,
owners and extra fields are rejected422. The server calculates the split. Original
response fields remain, adding payment_intent_id:string UUID|null, session_reused:boolean
and use_wallet:boolean. Fully wallet-funded payments have no intent or checkout URL.
An open hosted attempt freezes wallet choice; cancel it successfully before changing
that choice. Promo revisions cannot change unresolved checkouts.

### Invoice PDF download

GET /api/invoices/{invoice_id}/pdf remains an authenticated, participant-authorized
application/pdf attachment. UUID invoice_id; no body. Downloads and paid-email PDF
attachments share Giftly purple branding, English labels, itemized pricing, discounts,
fees/totals, invoice/order IDs and payment status. Issued/paid dates display GMT+3;
persisted and API timestamps remain UTC.

Private Redis reuse lasts3600 seconds with128 entries of256KiB maximum. Downloads
always check current ownership and read current invoice/items first. The fingerprint
covers status, issue/payment dates, amounts, line content and template version: changes
force immediate regeneration and hits do not extend TTL. Redis failure falls back to
rendering; larger PDFs are served uncached. HTTP remains private,no-store so response
caches cannot bypass authorization or freshness.

## Approved mobile completion — 2026-10-09

Source contracts below require release/migrations and separate deployment verification.
They do not establish that the configured public backend already supports them.

### Retry-safe writes and owned recovery

| Method / operation | Roles | Request and unchanged success response |
| --- | --- | --- |
| POST `/api/orders` / `order.create` | Customer | Existing CreateOrderRequest → 201 OrderDetail. |
| POST `/api/occasions` / `occasion.create` | Customer | Existing CreateOccasionRequest → 201 OccasionResponse. |
| PATCH `/api/occasions/{occasion_id}` / `occasion.update` | Customer owner | Existing UpdateOccasionRequest → 200 OccasionResponse; path UUID participates in key identity. |
| POST `/api/wallets/topup` / `wallet.topup` | Eligible customer/courier | `{"amount":"100.00"}` → 201 TopupResponse, including existing status/session_reused. |

All four accept optional `Idempotency-Key: <UUID>`; omitted keys preserve old behavior.
Generate and persist one random UUID before submission, then reuse it with identical
input after response loss. Scope is authenticated account + operation + UUID. Do not
reuse for a new action or another occasion. List order remains significant. Top-up
amounts normalize to two-place decimal strings. Same key/different input → 409
CONFLICT. Malformed UUID → 422 with the existing detail[] schema-validation response. Existing permissions and business
validation remain in force. Replay returns the original response snapshot, not current
order/payment state; fetch existing detail/session endpoints for current state.

PostgreSQL unique claims serialize concurrent retries. Resource changes and encrypted
result snapshots commit together. Media preflight remains outside the mutation
transaction. Completed results remain recoverable for 24 hours from creation; a
bounded hourly job removes expired completed snapshots. Never reuse an expired key:
the guarantee does not extend after retention/purge. Unresolved external attempts
are retained beyond this window so expiry cannot authorize a second charge.

GET `/api/operations/{operation_key}?operation=order.create|occasion.create|occasion.update|wallet.topup`
requires the same account's bearer token and current eligibility; couriers can recover
only wallet.topup. No request body, no pagination or caching; Cache-Control private,
no-store. Current resource ownership is rechecked; deleted/reassigned resources 404.

Response fields: operation_key:UUID, operation:enum above, status:COMPLETED or
OUTCOME_UNKNOWN, resource_id:UUID|null, result:object|null, created_at:UTC datetime,
expires_at:UTC datetime. COMPLETED means the original operation response committed,
not that an external payment succeeded. Its result is exactly the corresponding
OrderDetail, OccasionResponse or TopupResponse. OUTCOME_UNKNOWN has result=null and
may expose the owned payment-intent UUID; resolve it through existing payment-session
reads/refresh before deciding whether any replacement is safe.

Example:
```json
{"operation_key":"8dc024d8-0e52-43ea-a36f-49838bd2fe0f","operation":"wallet.topup","status":"COMPLETED","resource_id":"aeb8151e-38a5-4c86-aedb-7a5a430c7c6d","result":{"payment_intent_id":"aeb8151e-38a5-4c86-aedb-7a5a430c7c6d","amount":"100.00","payment_url":null,"status":"PAID","session_reused":false},"created_at":"2026-10-09T09:00:00Z","expires_at":"2026-10-10T09:00:00Z"}
```

503 OPERATION_PENDING on write replay means an existing committed attempt is
unresolved: recover it, do not create a fresh key. This error does not promise a
Retry-After delay; do not busy-loop. 404 recovery means no accessible retained result,
not proof that the write never executed. Within retention, retry the same key/input;
after expiry or an unresolved financial outcome, inspect owned resources/support.
Domain error envelope remains error:{code,message,request_id}; schema validation422
retains detail[] (do not expect a domain error code). 401 invalid session,
403 role/eligibility, 400 invalid business input, 409 conflicts, 422 schema errors,
429 RATE_LIMITED (honor Retry-After), dependency failures 503. Existing payment-session
refresh/cancel/review rules remain authoritative. No new payment-session endpoint.

### Original private media formats

Chat grants retain POST `/api/conversations/{conversation_id}/media-upload-urls`,
media submission and authorized attachment reads. Load GET `/api/chat/media-limits`
for exact configured limits. Images: JPEG, PNG, HEIC, HEIF (image/jpeg, image/png,
image/heic, image/heif); videos: MP4, WebM, original MOV (video/quicktime); voice
recordings: existing audio/mp4, audio/mpeg, audio/ogg, audio/webm, audio/wav, audio/aac.
Never rename/relabel original files. Send the original MIME, exact bytes and issued
key. HEIF must contain one primary image, at most 20 million pixels; auxiliary/depth
images and thumbnails are not decoded. MOV currently requires a first ftyp atom with QuickTime major brand qt; legacy
QuickTime files without it are rejected400. Native validation uses one HEIF decoding
thread, subprocess timeouts and bounded admission. Video retains 1080p pixel bounds,
120 MiB / 120 seconds; images 10 MiB; voice 10 MiB / 120 seconds. Config can lower caps.
Send at most five images or one video/voice note per message. Unsupported/malformed,
misdeclared or oversized bytes fail400; unavailable validation fails503.

General order-request/delivery-proof grants also accept HEIC/HEIF through existing
POST `/api/media/upload-urls` and `/api/media/confirm`, MAX_UPLOAD_BYTES and strict
server-generated keys. HEIF is fully decoded before confirmation/attachment. Original
private bytes are preserved; device display/thumbnail conversion is the client's job.
Ownership, confirmed-grant claims and expiring signed reads are unchanged; no public
caching or storage-key-as-public-URL. Historical attachments remain accessible.

### Live delivery and device switching

Keep WebSockets and existing optional client_message_id UUID retry identity for
TEXT/media. Prefer Authorization bearer header or supported subprotocol transport;
never put credentials in diagnostics. Outgoing complete serialized chat frames are
capped by WS_MAX_OUTGOING_FRAME_BYTES (32768 by default), measured in UTF-8 bytes;
oversized frames close1009. Recover committed messages with existing paginated REST
history; deduplicate by server message ID. Inbound WS_MAX_FRAME_BYTES remains4096.

New-order push adapter includes data:{"event_type":"ORDER_AVAILABLE","type":"ORDER_AVAILABLE","order_id":"<UUID>"}.
Open the existing authorized order/radar flow; push metadata does not grant ownership.
Actual vendor/device forwarding is unverified and excluded from this task.
Device-token registration deliberately transfers the same shared-device token to the
current authenticated account; deletion remains scoped to its current owner. No
blanket foreign-token409. Existing ten-device quota and logout/refresh rules remain.

## Approved mobile capabilities — 2026-10-11

Implemented in source; publication/deployment are recorded separately in tasks.md.
Exact schemas, enums and validation are in mobile-openapi.json. Existing callers remain
compatible. Money is a decimal string; timestamps UTC, calendar dates Gregorian.
Bearer authentication is required except public restoration. All responses are private/
no-store. Shared error envelope is {"error":{"code":"...","message":"...","request_id":"..."}}.
401 missing/expired/revoked credentials;403 ineligible role/account;404 missing/inaccessible
object;422 invalid input;409 state/version conflict;429 throttle with Retry-After.
Do not invent data or bypass ownership on404. Render user text as text, not HTML.

### Courier home and report settings

Active verified courier only. GET /api/couriers/me/earnings requires from_date,to_date
(YYYY-MM-DD,inclusive,max366days), granularity day(default)|week|month.
Riyadh reporting; Sunday–Saturday weeks. Ledger bounds are corresponding half-open UTC
timestamps. Actual signed SETTLED ESCROW_RELEASE courier proceeds include reimbursement;
exclude pending/reversed/unrelated movements. Wallet balance is not earnings.
At most366zero-filled daily buckets, no pagination; previous is equal-length immediately
preceding range. Zero baseline gives percentage:null; both zero UNCHANGED, otherwise
NO_BASELINE. Target refers to complete current WEEK/MONTH, may exceed100%.

~~~json
{"currency":"SAR","timezone":"Asia/Riyadh","from_date":"2026-10-04","to_date":"2026-10-04","granularity":"day","basis":"SETTLED_COURIER_PROCEEDS","settled_total":"200.00","pending_total":null,"previous":{"from_date":"2026-10-03","to_date":"2026-10-03","settled_total":"100.00","difference":"100.00","percentage":"100.00","comparison":"INCREASE"},"buckets":[{"from_date":"2026-10-04","to_date":"2026-10-04","amount":"200.00"}],"target":null}
~~~

GET /api/couriers/me/report-settings default:
{"version":0,"updated_at":null,"target_enabled":false,"target_amount":null,
"target_period":null,"progress":null}.
PATCH same path: {"version":0,"target_enabled":true,"target_amount":"1000.00",
"target_period":"WEEK"}. Required strict integer version; amount positive finite decimal
string<=1000000.00, period WEEK|MONTH; omission preserves fields. Disabled target clears
amount/period. Stale version409: refetch and let user resolve. Response increments version
and returns UTC updated_at. Enabled progress has enabled,period,amount,period_start,
period_end,settled_total,progress_percent. Indexed aggregation; no speculative Redis
cache or financial-notification switch.

### Notification inbox and actual order timeline

Active customers and verified couriers:
| Method / path | Input | Response |
| --- | --- | --- |
| GET /api/notifications | unread_only=false,limit1..100(default25),cursor opaque22characters | items,next_cursor,unread_count |
| GET /api/notifications/unread-count | None | {"unread_count":1} |
| POST /api/notifications/{notification_id}/read | UUID path,no body | Updated notification; idempotent, foreign404 |
| POST /api/notifications/read-all | No body | {"marked_count":1,"read_at":"2026-10-11T09:01:00Z"} |

~~~json
{"items":[{"id":"11111111-1111-4111-8111-111111111111","type":"ORDER_STATUS_CHANGED","title_key":"notification.order_status_changed.title","body_key":"notification.order_status_changed.body","parameters":{"status":"ASSIGNED"},"created_at":"2026-10-11T09:00:00Z","read_at":null,"navigation":{"kind":"ORDER","order_id":"22222222-2222-4222-8222-222222222222"}}],"next_cursor":null,"unread_count":1}
~~~

Types ORDER_STATUS_CHANGED,INVOICE_ISSUED,WALLET_TOPUP_COMPLETED,
WITHDRAWAL_STATUS_CHANGED,OCCASION_REMINDER. Keys notification.<lowercase_type>.title/body.
Parameters actual order/withdrawal status or{}. Navigation ORDER/order_id,
OCCASION/occasion_id, or WALLET. Localize keys; reauthorize destination on opening.
Newest(created_at,id)first; mark-all cutoff excludes later arrivals. Reset cursor on
unread filter changes and refresh unread-only lists after reads. No public content cache.

GET /api/orders/{order_id}/timeline?limit=25&cursor=... requires current participant.
Oldest(created_at,id)first, limit1..100 and opaque22character cursor.
~~~json
{"items":[{"id":"33333333-3333-4333-8333-333333333333","event_type":"STATUS_CHANGED","status":"ASSIGNED","created_at":"2026-10-11T09:00:00Z","actor":{"role":"COURIER"}}],"next_cursor":null,"history_complete":true}
~~~
Actor null or CUSTOMER|COURIER|ADMIN role. Older orders have history_complete:false;
never fabricate historical transitions. Keep existing WebSockets and REST recovery.

### Occasion reminders

Existing customer POST/PATCH /api/occasions accepts reminder_enabled:boolean,
false on create; omission preserves on edit. Response adds reminder_enabled and
timezone:"Asia/Riyadh". Existing days_before_reminder sets lead days.
Existing from_date/to_date/cursor unchanged. Opt-in one-off delivery at09:00Riyadh on
occasion_date minus lead days; actual worker/scheduler required. Durable occurrence
deduplication and inbox; push at most5attempts,60/120/240/480/960seconds backoff,
24-hour eligibility. Provider/edit races may duplicate/stale push; re-read the occasion.
No exactly-once push claim, no annual recurrence. Push permission does not hide inbox.

### Courier avatar workflow

Active verified courier only; customer excluded.
1. POST /api/media/upload-urls {"purpose":"COURIER_AVATAR","content_type":"image/jpeg",
   "byte_size":2048}; JPEG/PNG only, default10MiB plus decoded-pixel bound.
2. Response upload_url,storage_key,expires_in; upload raw bytes directly with issued
   Content-Type,Content-Length,If-None-Match:* headers.
3. POST /api/media/confirm {"storage_key":"<returned key>"} returns confirmed:true/key.
4. PUT /api/users/me/courier-avatar {"storage_key":"<confirmed owned key>"} returns
   {"avatar_url":"<private signed URL>","expires_in":300}.
5. DELETE same path,no body -> {"avatar_url":null,"expires_in":0},idempotent.

Foreign/unconfirmed/reused/wrong-purpose upload rejected. Profile/participant avatar_url
only for couriers after authorization. Refresh expired URLs through profile; never log
signed URLs or treat keys as public URLs. Replaced uploads released for existing cleanup;
active pointer remains protected. Private/no-store profile responses.

### Phone change and current-phone reauthentication

Authenticated eligible customers/couriers; banned/deleted/admin excluded.
| Method / path under /api/users/me | Body | Response |
| --- | --- | --- |
| POST /reauthentication/request | {"purpose":"PHONE_CHANGE"} (default if omitted) | 202challenge |
| POST /reauthentication/confirm | {"challenge_id":"<UUID>","otp":"12345"} | {"reauthentication_token":"<one-use proof>","expires_in":300} |
| POST /phone-change/request | {"new_phone":"+966501234567","reauthentication_token":"<proof>"} | 202new-phone challenge |
| POST /phone-change/confirm | {"challenge_id":"<UUID>","otp":"12345"} | {"phone":"+966501234567","reauthentication_required":true} |

Challenge {"challenge_id":"<UUID>","expires_in":60,"resend_after":60}; development only
adds otp_dev:int. Purpose/actor/version bound proof, scoped OTP60seconds, Saudi
normalization/uniqueness and OTP quotas enforced. Honor Retry-After. Proof may be consumed
when sending fails429/provider503: reauthenticate afresh before retry. Success revokes
old access versions,refresh sessions and push registrations; clear tokens, log in again
and register device. Never log/cache OTP/proof/session data.

### Account deletion and courier termination

Use current-phone proof purpose ACCOUNT_REQUEST, then POST /api/users/me/account-request:
Customer {"kind":"DELETE_ACCOUNT","confirmation":"DELETE","reauthentication_token":
"<proof>","reason":"Optional <=500characters"}.
Courier {"kind":"TERMINATE_CONTRACT","confirmation":"TERMINATE",
"reauthentication_token":"<proof>"}. Existing valid email required or422(update profile).
~~~json
{"id":"44444444-4444-4444-8444-444444444444","kind":"DELETE_ACCOUNT","status":"RECOVERABLE","submitted_at":"2026-10-11T09:00:00Z","updated_at":"2026-10-11T09:00:00Z","restore_deadline":"2026-10-25T09:00:00Z","administrative_review_required":true,"financial_records_retained":true,"receipt":"<restricted status credential>"}
~~~
202 submission disables account,revoke sessions/devices. Securely retain receipt only
for GET /api/users/me/account-request using X-Account-Request-Receipt header. It is not a
bearer token, grants no other access, returned only on submission, omitted on GET.
Public POST /api/users/me/account-request/restoration/request {"phone":"+966501234567"}
returns uniform202challenge without existence disclosure.
Public POST /api/users/me/account-request/restoration/confirm {"challenge_id":"<UUID>",
"otp":"12345"} returns request status RESTORED within14days, no session tokens.
Log in separately. Expiry/admin status/role/version changes prevent restoration/ban bypass.

After14days status PENDING_REVIEW; no automatic permanent erase/refund. Display deadline
and support-call coordination to resolve funds/data,orders,disputes,payments,withdrawals
before admin completion. Financial/audit records retained pending legal policy; do not
promise their destruction or automatic payment. Durable English email explains policy,
at most5bounded retry attempts; real vendor delivery remains unverified.

### Customer order search

GET /api/orders?q=gift&from_date=2026-10-01&to_date=2026-10-31&limit=25 reuses existing list.
q optional,trimmed1..100characters,no null bytes,case-insensitive literal prefix of first
100descriptioncharacters or exact UUID when parsed. No invented order number/substring
search. Customer own orders only; courier q403. Status/delivery-date/owner SQL filters
before newest-first keyset pagination. Response unchanged. Reset cursor when q/status/
dates change; every next page uses identical filters. Empty200 items:[],next_cursor:null;
foreign/stale cursor documented404 retained. New composite prefix index serves default
automatic/custom plans; forced generic plans may owner-scan. No unmeasured search cache.

### Deferred features

Independent courier appointments,saved bank profiles,annual recurrence,statement PDF
and financial-notification settings remain deferred by user. Reuse existing invoice
list/history/PDF,wallet statement,order media and owned payment-session operations.


## Mobile release verification - 2026-10-12

Authenticated customer/courier checks confirmed reports/targets, owned notification
pagination and read operations, participant timeline pagination, customer q search and
phone change with old credential revocation. Deployed and published contracts matched
on 13 inspected feature paths.

Avatar role/type/removal checks passed, but full image confirmation and attachment remain
unverified. Deployed development fake storage returns localhost URLs and cannot supply
actual uploaded bytes for decoder checks. Clients must not bypass HTTPS, ownership or
image validation to use these URLs. See tasks.md for remaining release checks.

Existing mobile source already wires reports, notifications, avatar and phone flows.
Remaining UI work: courier-home earnings, customer q search and actual historical timeline
events. Integration prompts are delivered in chat only.

## Approved mobile additions - 2026-10-13

These additions preserve existing saved-occasion CRUD, wallet statement JSON, device
registration and avatar attachment paths. Deployment is recorded separately in the review.

### City-scoped new-order push and Expo receipts

The committed order/outbox is delivered by the separate worker, not the API transaction.
Recipients are ACTIVE, verified, non-deleted couriers whose assigned city FK matches the
order city. Original recipient/device ownership is rechecked before each Expo send chunk.
Only NEW orders are advertised. A registration created after the initial recipient snapshot
does not receive that old event. Device transfers and revoked registrations are excluded.

Exact Expo data (not the HTTP response to creating an order):
```json
{"event_type":"ORDER_AVAILABLE","type":"ORDER_AVAILABLE","order_id":"11111111-1111-4111-8111-111111111111"}
```
The UUID is the actual persisted order ID. Title/body are generic, without customer details,
photos, credentials or signed URLs. Treat pushes as repeatable hints, deduplicate navigation
by type/order_id and read authorized GET /api/orders/available or the order detail. Do not
accept an order, change money or assume ownership from a push.

POST /api/devices still accepts {token:string,device_os:IOS|ANDROID}; it returns 201 with
the existing DeviceResponse. DELETE /api/devices accepts {token:string} and returns 204.
With Expo selected, mobile must register getExpoPushTokenAsync() results, not native FCM/APNs
tokens. Preserve shared-device account switching and unregister-before-logout behavior.

Send chunks are at most 100. Accepted ticket IDs are committed before later chunks.
Transport retries are bounded; the existing durable outboxes recover failures. Unknown
remote outcomes can duplicate hints; no exactly-once delivery or business action is claimed.
Receipt lookups start after 15 minutes. Missing/transient receipts use bounded backoff,
at most 32 attempts and a 24-hour lifetime. DeviceNotRegistered deletes only an unchanged
device-ID/token/last_seen_at registration; refreshed or transferred registrations survive.
Receipt updates and invalid-token removals are batched with lease fencing.
No provider delivery was tested in this release.

### Annual customer occasions

Existing POST /api/occasions and PATCH /api/occasions/{occasion_id} accept optional
recurrence: NONE|ANNUAL. Default NONE preserves existing one-off behavior. Responses add
the same recurrence field. Only active customers may access their own records.

Example create:
```json
{
  "title":"Birthday",
  "occasion_date":"2020-02-29",
  "recurrence":"ANNUAL",
  "reminder_enabled":true,
  "reminder_days_before":7
}
```
Example 201 response:
```json
{
  "id":"22222222-2222-4222-8222-222222222222",
  "title":"Birthday",
  "occasion_date":"2020-02-29",
  "recurrence":"ANNUAL",
  "reminder_enabled":true,
  "reminder_days_before":7,
  "timezone":"Asia/Riyadh",
  "created_at":"2026-10-13T10:00:00Z",
  "updated_at":"2026-10-13T10:00:00Z"
}
```
The date remains the original Gregorian anchor. Annual occurrences preserve month/day and
never precede that anchor. February 29 occurs only in leap years, including the Gregorian
century rule; it is not substituted with February 28/March 1.

GET /api/occasions retains its stored-record date filtering and UUID cursor unchanged.
Use GET /api/occasions/occurrences for expanded calendar dates:
- Required from_date/to_date: inclusive YYYY-MM-DD, 1..366 Gregorian days.
- limit: 1..100, default 25.
- cursor: returned opaque string, maximum 100 characters.
- Ordering: occurrence_date ascending, then occasion_id.
- No request body; private,no-store.

Example:
GET /api/occasions/occurrences?from_date=2028-02-01&to_date=2028-02-29&limit=25
```json
{
  "items":[{
    "occasion_id":"22222222-2222-4222-8222-222222222222",
    "title":"Birthday",
    "occurrence_date":"2028-02-29",
    "recurrence":"ANNUAL",
    "reminder_enabled":true,
    "reminder_days_before":7,
    "timezone":"Asia/Riyadh"
  }],
  "next_cursor":null
}
```
Each item identifies a virtual date of the saved occasion, not a new occasion or order.
Reset cursor when the date range changes; retain identical filters on subsequent pages.
Empty ranges return items:[] and next_cursor:null. Malformed cursor:400 BAD_REQUEST;
foreign/stale/out-of-range anchor:404 NOT_FOUND; malformed dates/reversed/oversized range:
422. Authentication/role failures retain401/403.

Reminders reuse the existing 09:00 Asia/Riyadh schedule, worker and durable inbox.
New annual schedules select the next non-past reminder; missed years are not bulk replayed.
A unique owner/occasion/occurrence-date event prevents duplicate inbox events.
Push remains at-least-once; permission/provider configuration is required for real delivery.
Changing anchor, recurrence, reminder offset or enabled state invalidates old leases.
reminder_enabled:false disables delivery only. recurrence:NONE restores the original
one-off anchor; change occasion_date separately if a different one-off date is desired.
DELETE removes future schedules; existing historical inbox entries can remain.

### Informational wallet statement PDF

GET /api/wallets/me/statement/pdf?from_date=2026-10-01&to_date=2026-10-13
No request body, cursor or limit. Active customers and eligible active verified couriers
may export their own wallet only; no client owner/wallet ID is accepted.

Inclusive Asia/Riyadh bounds and the existing 1..366-day maximum match the JSON statement.
The document includes currency, bounds, UTC-snapshot time displayed in GMT+3, exact IDs,
signed amounts and whole-range decimal-string SQL totals. SETTLED, PENDING and REVERSED
entries/totals are separate. It is a generated informational snapshot, not a legally
specified document, invoice or earnings report. It does not invent opening/closing balances.

200 returns PDF bytes:
- Content-Type: application/pdf.
- Content-Disposition: attachment; filename="giftly-statement-2026-10-01-2026-10-13.pdf".
- Cache-Control: private, no-store; X-Content-Type-Options:nosniff.

All selected entries are covered from one SQL snapshot, up to 5000 entries. Larger exports
return422 VALIDATION_ERROR requesting a narrower range, never a partial PDF. Descriptions are complete literal text, with Arabic shaping and English headings.
Characters unsupported by the bundled font are shown as lossless Unicode escape notation.
No description is interpreted as HTML/JavaScript. IDs, amounts and entry counts remain complete.
Two render slots per API process; read/release timeout25seconds, cooperative rendering
deadline20seconds, maximum8MiB/250pages and 1,000,000 description characters. Cancellation retains admission until the actual thread
finishes. 503 STATEMENT_EXPORT_UNAVAILABLE denotes busy/expired rendering; no public cache
or unauthenticated download URL is created. Existing401/403/404/422 envelopes apply.

### Courier avatars: real storage selection

Existing signed PUT -> POST /api/media/confirm -> PUT /api/users/me/courier-avatar remains
unchanged. Only eligible couriers can attach/remove avatars; customer avatars are excluded.
Use owned COURIER_AVATAR grants, JPEG/PNG, exact declared size and decoded image validation.
Upload/read expiry is300seconds. Authorized GET /api/users/me renews private avatar access.
No forged, reused, wrong-purpose or foreign object can attach.

STORAGE_PROVIDER=s3 selects reachable public HTTPS S3-compatible origins even in development
without enabling other real integrations. Configuration fails closed on missing S3 fields,
localhost/private IP/internal hostnames, credentials in URL or invalid origins. This is
configuration validation, not proof of DNS reachability or valid bucket credentials.
Fake storage remains a local/test double with auto in non-production. Hosted mobile use
must explicitly select s3 with real keys; do not use or rewrite localhost fake URLs.
Storage keys are opaque server-issued references, never public access URLs.


Deployment evidence (2026-10-13): both backend.giftly.sa and its CranL hostname expose
the occurrence/PDF additions matching the published contract. Authentication gates return401
without credentials; Alembic0034 is applied. Real storage and Expo activation remain subject
to the explicit runtime configuration above. No live vendor success claim is made.


Occasion creation retains the optional UUID Idempotency-Key. Unchanged one-off requests
can replay pre-recurrence receipts; changing the same key to annual/different input remains
409 CONFLICT. Use a new key for a new operation and retain a key for identical retries.


Contract clarification (2026-10-13): new-order data contains event_type:ORDER_AVAILABLE;
type:ORDER_AVAILABLE is retained as a compatibility alias. Both identify the same event.
Deduplicate hints using (event_type or legacy type, order_id), never the delivery ticket.
At-least-once hints only trigger authorized reads; they never accept an order.
The compatible payload and complete-description PDF follow-up are published in dadc421;
both public API contracts match. Real Expo device delivery remains unverified.


### Avatar storage availability follow-up (2026-10-14)

Existing endpoints and fields are unchanged: POST /api/media/upload-urls with
purpose=COURIER_AVATAR; signed PUT; POST /api/media/confirm; PUT or DELETE
/api/users/me/courier-avatar; GET /api/users/me renews courier_profile.avatar_url.
Only active verified couriers may upload/attach; customer avatar uploads remain forbidden.
JPEG/PNG only, at most10MiB or the lower MAX_UPLOAD_BYTES value. Actual HEAD size/type and
full isolated decoding are checked; metadata and magic headers alone cannot establish validity.

Outside ENVIRONMENT=test, fake storage cannot issue avatar grants or access URLs.
503 STORAGE_UNAVAILABLE means real storage is unconfigured or the provider is unavailable;
the safe envelope contains no provider response, key, credential or signed URL.
Real origins must match the configured public HTTPS endpoint. No HTTP/internal origin
or replacement upload endpoint is introduced. Unexpected signing origins fail closed.

PUT requires exact Content-Type, exact Content-Length and If-None-Match: *;
do not use multipart encoding or add the API Bearer header to storage requests.
An expired300-second upload URL requires a new grant. Successfully uploaded bytes can
be confirmed/attached within the existing24-hour orphan retention window. After24hours,
an unclaimed avatar grant is rejected with400 BAD_REQUEST, requesting a new upload URL.
Read URLs last300seconds; renew through an authorized profile read. Upload retries must
not overwrite existing objects. A412 conditional-write response is not proof of valid upload;
confirm the owned key or restart with a new grant as appropriate.

Failure during validation, signing or attachment leaves the current avatar intact.
Replacement/removal releases the old grant only within the successful DB transaction.
The hourly separate worker fences and deletes abandoned objects after24hours; active
avatars remain protected. Cleanup skips non-test fake storage instead of discarding real
object metadata. The worker must use the same real endpoint/bucket/credentials as the API.

## Chat authentication deployment check — 2026-10-16

**Activation verdict: NO — Safe to enable EXPO_PUBLIC_CHAT_HEADER_AUTH=true has not
been established for the configured deployment. Mobile activation remains blocked.**

The existing operation is
WS /api/ws/conversations/{conversation_id}, reached publicly at
wss://backend.giftly.sa/api/ws/conversations/{conversation_id}. Header/protocol/query
selection already exists in commit 5c2cec4; application credential-log hardening and
the compatibility test were published in 8645301. This follow-up extends verification
only. Message payloads, REST history, events, client_message_id and retry semantics
are unchanged. No EXPO_PUBLIC_CHAT_RETRY_SAFE change is requested.
Local browser-style negotiation was tested with a real TCP WebSocket client; this
was not an actual browser UI or mobile application run.

| Client | Existing connection method | Selected subprotocol |
| --- | --- | --- |
| React Native | Authorization: Bearer <access_token>; no token query and no offered subprotocol | None |
| Browser | new WebSocket(url, ["giftly.chat", "bearer." + accessToken]) | Only giftly.chat; never bearer.<credential> |
| Legacy rollout | Existing token query transport remains accepted by the application | Existing negotiation rules; do not introduce query tokens in new clients |

An Authorization header takes precedence even when malformed; otherwise the first
bearer-prefixed offered protocol wins; only then is the legacy query considered.
Credentials from conflicting transports are never combined.
Production browser clients also require their actual Origin in CORS_ALLOWED_ORIGINS.
The ingress must preserve Authorization, Sec-WebSocket-Protocol, Upgrade and
Connection handshake behavior; never log the credential-bearing values.

The existing POST /api/auth/refresh accepts application/json:
{"refresh_token":"<current refresh token>"}. On success the existing 200 response
contains access_token: string, refresh_token: string and role: string. Replace both
stored credentials atomically. Local verification used the returned rotating refresh
token for a second successful refresh, then reconnected with the successor access
token using both query-free transports. Never include these values in diagnostics.

| Check | Actual deployment | Owned local PostgreSQL/Redis + real TCP |
| --- | --- | --- |
| Readiness | HTTP 200 | Application lifespan started |
| Missing/invalid credentials | Five bounded TLS-verified attempts returned HTTP 403 | Rejected |
| Valid customer and courier members, native/browser | NOT VERIFIED: no available authorized live credentials or usable container execution | Accepted; native selects no protocol, browser selects only giftly.chat |
| Expired/revoked credentials and nonmembers | NOT VERIFIED with authenticated live fixtures | Both transports rejected |
| Refresh rotation, returned successor token and reconnect | NOT VERIFIED on live accounts | Passed |
| Banned/deleted customer, unverified courier | NOT VERIFIED on live accounts | Both transports rejected |
| Eight-account-socket cap and ninth rejection | NOT VERIFIED live | Eight concurrent sockets accepted; ninth rejected for both transports; leases released |
| Existing legacy valid client | NOT VERIFIED live | Accepted |
| Credential logging | Legacy synthetic query value was retained in CranL request history; raw header/subprotocol capture configuration is unavailable | Application scrubbing tests passed |
| Messages/history/events | No production messages sent or changed | Existing regression suite covers contracts; no application changes |

Before an upgrade, the application requests 4401 for failed authentication, 4403 for
origin/membership/eligibility rejection, 4429 for connection limits and 1013 for lease
store failure. Uvicorn normally expresses a close before acceptance as HTTP 403.
Therefore a rejected handshake is not an established WebSocket close frame, and
HTTP 403 must not be labeled universally as an expired token. It can also indicate
membership, origin or connection limits. After upgrade, lost authorization/membership
uses 4401; transient subscription failure can use 1013; unexpected failures use 1011.
Use bounded reconnect/backoff, fetch authorized history after reconnect and do not
blindly refresh forever on every 403.

CranL reported successful builds (including the audit-only 8d9ac19 build), but the
application metadata still said deploying. Read-only source-fingerprint commands failed
or returned unusable blank output, even for a harmless echo probe. The running chat
implementation therefore could not be matched to the pushed source. No production
accounts were created, no real OTPs/provider deliveries were requested, and no user
credentials were extracted to work around that limitation.

A non-secret synthetic canary in one rejected legacy URL appeared in the path field
of CranL query_requests. It did not appear in the sampled application-log records;
those records contained no detected JWT-shaped values. The available request metadata
has no headers field, so it cannot prove that raw ingress Authorization or
Sec-WebSocket-Protocol values are suppressed. This is a confirmed upstream query
capture problem, not evidence that valid tokens were leaked during this check.

Configure upstream logging to omit query strings or redact credential parameters
before storage, and omit Authorization and credential-bearing Sec-WebSocket-Protocol.
Application log scrubbing cannot sanitize separately collected ingress metadata.
The currently exposed CranL tools/docs did not provide that setting. Retain legacy
compatibility while fixing the logging boundary; do not weaken mobile HTTPS checks
or change the chat authentication contract to hide this deployment issue.

Activation requires successful authenticated native/browser upgrades, renewed-token
reconnection, restriction/cap checks and verified safe proxy logging on the target
deployment. A successful build, readiness response or local suite is insufficient.
See AUD-30 in tasks.md for the upstream logging mitigation and current limitations.

### WebSocket resource and reconnect limits — 2026-10-21

The React Native client should authenticate with `Authorization: Bearer <access_token>`;
it does not need browser subprotocol negotiation. Existing server support for bearer
subprotocol and legacy query credentials remains during rollout. After JWT signature
and expiry validation, each account is limited to 30 WebSocket handshakes per 60 seconds
across chat and order streams, using shared Redis state before database authorization.
Redis limit-store failures fail closed with a pre-upgrade 1013 request; excess attempts
request 4429. The configured Uvicorn transport caps incoming frames at 4 KiB, queues at
most one frame and disables per-message compression. Chat sends that cannot complete
within five seconds close with 1013; committed messages remain recoverable from REST
history. These changes bound reconnect pressure, per-connection buffering and slow-reader
resource use. They do not establish successful behavior on the deployed reverse proxy;
authenticated deployment checks and ingress log redaction remain unverified.

### Courier notification-center policy and chat unread counts

Courier notification-center reads include only existing `ORDER_STATUS_CHANGED`
events for these three cases:

| `parameters.status` | Meaning in the courier center | Server evidence |
| --- | --- | --- |
| `IN_PROGRESS` | The customer's invoice was paid. | A paid/refunded invoice for that order with a payment timestamp at or before the event. |
| `CANCELLED` | The customer cancelled the order. | The exact recorded timeline event attributes cancellation to a customer. |
| `COMPLETED` | The customer confirmed that the order arrived. | The exact recorded timeline event attributes completion to a customer. |

Courier-originated delivery/assignment/invoice creation, administrative or automatic
cancellation/completion, withdrawals and other inbox events are hidden from this
center. Customer notification behavior and separate city new-order/chat push delivery
are unchanged. This is a read policy over committed history; it neither deletes past
notifications nor invents missing historical events.

The same SQL policy applies to `GET /api/notifications`, its `unread_count`,
`GET /api/notifications/unread-count`, individual reads and mark-all. Ownership and
current account eligibility remain enforced. Filtering precedes keyset pagination;
the existing defaults (25, maximum 100), newest-first ordering, cursors and private
no-store responses remain. An old cursor pointing to a now-hidden event returns the
existing `404 NOT_FOUND`; restart the list without its cursor. Hidden/foreign
notification identifiers also return 404. No schema migration or new endpoint is needed.

Both eligible customers and couriers already have independent unread chat counts:

- `GET /api/conversations?limit=20` returns each conversation's `unread_count`
  for the authenticated caller, not the other participant. The existing limit is
  1–100 and pagination uses the returned opaque cursor.
- `GET /api/orders/{order_id}/conversation` resolves an accessible order to its
  conversation, including historical orders.
- `GET /api/conversations/{conversation_id}/messages?limit=30` returns
  newest-first messages with `is_read:boolean`. For a message the caller sent,
  true means the recipient has marked it read. Incoming messages become read when
  the current user acknowledges them.
- `POST /api/conversations/{conversation_id}/read` accepts no body and returns
  `204` with no response body. It marks existing incoming messages read and resets
  only the caller's counter; the other party's unread messages are unaffected.
  Sends and reads serialize on the conversation so a later send remains unread.

Opening history or connecting a WebSocket does not automatically acknowledge reads.
Refresh inbox counts after acknowledgment and when new live messages arrive.
Re-fetch authorized message history to refresh sent-message read indicators:
there is currently no separate WebSocket read-receipt event. Read state and counts
are server-owned; do not submit client-calculated counters or mark messages read
just because a background push arrived. Existing 401/403 account restrictions and
404 participant checks apply; a 429 response requires respecting Retry-After.

# Giftly API reference

## Mobile read additions — 2026-10-07

Release verification passed; PaaS deployment is UNCONFIRMED. These operations
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

Common domain errors use the existing error envelope; schema422 may use detail[].
Handle401/403/404/422/503 distinctly. On429 honor Retry-After seconds. Render all user
text as plain text; signed media links must be refreshed after expiry. No response
cache or schema migration is introduced for these read operations. PostgreSQL execution
plans/integration behavior require disposable database verification, not Docker locally.


Updated 2026-10-04. This is maintained API documentation, not an implementation prompt.

[Project documentation](documentation.md) · [Current review](codebase_review.md) · [Tasks](tasks.md)

The [non-admin OpenAPI 3.1 specification](mobile-openapi.json) inventories 62 supported
mobile HTTP operations. [Full OpenAPI](openapi.json) also includes administrative API operations.
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
- Withdrawal and withdrawal-rejection bodies now reject unknown fields with HTTP 422,
  matching the other request models. Send only the properties declared in OpenAPI.
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

**When/how (59 words):** Send only fields the user actually changed; omitted properties leave existing values untouched, while explicit null can clear optional values. Customer and courier users can edit common details; courier city and biography apply to courier profiles. Use the returned profile as the new display state. Phone changes, avatar upload, and theme preference are not offered by this mobile API.

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

## Wallet and withdrawals

### GET /api/wallets/me

- **API name:** Get My Wallet.
- **Screens:** Customer/Courier Home `/home`; Wallet `/wallet`.
- **Who / authorization:** Authenticated customer or eligible verified courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `WalletResponse` — `balance: string`; `held_balance: string`; `available: string`; `currency: string`
- **Before:** Login and role/profile restoration.
- **Then / dependent API:** `GET /api/wallets/me/transactions`; optional top-up or withdrawal.

**When/how (56 words):** Load the signed-in user's own wallet snapshot, not a wallet ID chosen by the client. Display balance, held balance, and available funds as decimal-string SAR amounts; never parse them through floating-point arithmetic for financial decisions. Refresh after payment, approval, or withdrawal activity. A courier must be active and verified before this operational wallet route is available.

### POST /api/wallets/topup

- **API name:** Start Topup.
- **Screens:** Wallet `/wallet` add-funds action.
- **Who / authorization:** Authenticated customer or eligible verified courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `TopupRequest` — `amount: string`
- **Response:** HTTP 201; `TopupResponse` — `payment_intent_id: string`; `amount: string`; `payment_url?: string | null`
- **Before:** `GET /api/wallets/me` and a user-entered positive decimal amount.
- **Then / dependent API:** Return to wallet and transaction list after settlement.

**When/how (59 words):** Start a wallet funding attempt with a positive decimal-string amount. In development the fake payment flow may settle locally and return a null payment URL; production currently returns `PAYMENTS_DISABLED` before any intent or balance change because Dhamen is not integrated. The mobile UI must not present this as a working production checkout or treat a browser return as settlement.

### POST /api/wallets/withdrawals

- **API name:** Request Withdrawal.
- **Screens:** Courier Wallet `/wallet` withdrawal action.
- **Who / authorization:** Authenticated active verified courier; bearer access token.
- **Path, query, headers:** `Idempotency-Key: string` (header).
- **Request body:** `WithdrawalRequest` — `amount: string`; `iban: secret string`
- **Response:** HTTP 201; `WithdrawalResponse` — `id: string`; `amount: string`; `iban_last4: string`; `status: string`; `rejection_reason?: string | null`
- **Before:** `GET /api/wallets/me` confirms available funds.
- **Then / dependent API:** Refresh wallet and transactions; no courier withdrawal-list API exists.

**When/how (54 words):** Request a courier withdrawal by sending a positive decimal-string amount and Saudi IBAN, plus a unique `Idempotency-Key` header. The backend encrypts the IBAN and returns only its last four characters and request status. Reuse the same key for a retry of one logical request, not for a new withdrawal. Customers cannot call this route.

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

**When/how (61 words):** Confirm an uploaded object by its server-generated storage key after the direct PUT completes. The backend checks ownership, declared type and size, actual object presence, and image magic bytes. Only confirmed keys may be attached, and each can be claimed once for its original purpose. A local file URI or an unconfirmed key is not a durable order or delivery attachment.

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

**When/how (62 words):** Claim a `NEW` order as the current verified courier. The backend checks city, assignment capacity, and current order state under locks; the first valid acceptance wins. Do not show assignment as complete until the response succeeds, because another courier may accept concurrently. The response provides the assigned order detail; use its UUID and status to open chat and later issue an invoice.

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

**When/how:** Register or refresh the current device's push token using `IOS` or `ANDROID`. The server associates that token with the authenticated account and can move a handed-down device token away from a previous owner. Register again after token rotation or a successful new login. Each account can register at most 10 device tokens. Refreshing its existing token remains allowed at the quota; adding another returns `409 DEVICE_LIMIT_REACHED`. Delete an owned registration before adding another. Do not confuse this with an in-app notification-list API; the backend has no endpoint to fetch a notification feed.

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
| Customer Home `/home` | `users/me`, `wallets/me`, `orders`, `conversations` | Occasions CRUD is available; no notification-feed or aggregated home endpoint. |
| Create order `/request` | `media/upload-urls`, signed PUT, `media/confirm`, `orders` | Backend allows **0–3** photos, not prototype's four; no morning/evening period, recipient phone, or returned image URLs. |
| Waiting `/waiting/[id]` | `orders/{id}` | No separate push/poll status stream; refresh the order. |
| Customer/Courier Orders `/orders` | `orders`; courier `orders/available` | No customer text/order-number search; no order number in responses. Courier should not show a search box. |
| Order detail `/order/[id]` | `orders/{id}`, active invoice, participant, ratings, chat | No timeline events or media/proof download/list API in the returned contract. |
| Chat `/chat/[id]` | `conversations`, messages, read, WebSocket | Conversation ID comes from inbox; WebSocket has no durable replay, so use REST history after reconnect. |
| Invoice `/invoice/[id]` | `orders/{id}/invoice`, `invoices/{id}`, promo preview/apply/remove; courier create/cancel; customer pay | Pay the returned current invoice ID after promo changes. No saved-card API. Production payment is disabled. |
| Customer Calendar `/calendar` | `/api/occasions` CRUD | Customer-owned special dates; stored reminder preferences only, no automatic reminders or annual recurrence. |
| Courier Calendar `/calendar` | Assigned orders support inclusive delivery-date ranges for day/week/month views | No appointment CRUD or separate appointments API. |
| Customer/Courier Wallet `/wallet` | `wallets/me`, transactions; top-up; courier withdrawal | No saved-card display/list API. Production top-up is disabled. No withdrawal-list API for courier. |
| Courier Reports `/reports` and settings sheet | Wallet transactions can be read as raw entries | No earnings aggregates, comparison, chart series, target settings, or financial-notification preference API. |
| Courier Statement `/statement` | Wallet transactions, cursor-paged | No date-range filtering, statement totals, or downloadable statement API. |
| Profile `/profile` | `users/me`, patch, courier resubmit, logout, device registration | Date of birth and gender are writable and returned by `users/me`; no theme preference, avatar upload/read, or phone-change API. |

**Important contract mismatches:** The mobile map proposes halala integers, but the backend uses decimal **strings** for money. It proposes local file URIs/upload IDs, but the backend expects confirmed server-generated `storage_key` values. It proposes a matching/accepted/preparing/delivering timeline, while the actual order status enum is `NEW`, `ASSIGNED`, `WAITING_PAYMENT`, `IN_PROGRESS`, `DELIVERED`, `COMPLETED`, `CANCELLED`, `DISPUTED`, `REFUNDED`. Translate those labels only for display; do not send prototype strings to the API. The server does not enforce the prototype's age-16 minimum. Order delivery dates are constrained by the database to today through 180 days ahead; the UI should prevent out-of-range entries.

## Recommended call sequences

1. **Existing login:** `send-otp` → `verify-otp` → store returned token pair → `users/me` → role-specific home data. **New login:** use the returned `registration_token` with `register`, then follow the same profile flow.
2. **Create order with photos:** for each of at most three images, request an `ORDER_REQUEST` upload URL → signed PUT bytes → confirm the key → create the order with confirmed keys → navigate to `/waiting/[id]` using the returned UUID.
3. **Courier fulfillment:** `orders/available` → accept → conversation inbox/chat → create invoice → customer reads invoice and attempts payment only when a supported gateway is enabled → courier uploads/ confirms `DELIVERY_PROOF` → deliver → customer approves or disputes → the customer may rate the courier after completion.
4. **Chat:** get inbox for `conversation_id` → page REST history → open WebSocket while visible → send with REST or JSON WebSocket frames → mark read. On socket reconnect, reload REST history to cover missed ephemeral events.
5. **Wallet:** fetch snapshot and transactions. For courier withdrawal, send `Idempotency-Key` with amount and IBAN, then refresh wallet. Do not expose top-up or invoice checkout as functional in production until the backend enables a verified Dhamen integration.

## Source and verification

Derived from `app/main.py`, `app/routers/`, `app/schemas/`, service eligibility/state checks, and an **offline** development OpenAPI build with dummy settings. No database, Redis, Docker, real storage, or payment provider was contacted for this document. The OpenAPI HTTP inventory was compared against all router registrations: **61 supported mobile HTTP operations plus two WebSockets** are represented above. `/api/admin/*` and server-rendered `/v1/admin/admin/*` are deliberately excluded. The mobile file supplied with the request was used only to name and map screens, not as authority for backend behavior.

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

New-order push adapter includes data:{"type":"ORDER_AVAILABLE","order_id":"<UUID>"}.
Open the existing authorized order/radar flow; push metadata does not grant ownership.
Actual vendor/device forwarding is unverified and excluded from this task.
Device-token registration deliberately transfers the same shared-device token to the
current authenticated account; deletion remains scoped to its current owner. No
blanket foreign-token409. Existing ten-device quota and logout/refresh rules remain.

### Remaining product capabilities

The existing calendar CRUD/date filters, order claim/realtime, chat history/media,
invoice list/history/PDF, wallet statements and hosted payment recovery are reused.
Occasion reminder delivery/annual recurrence, independent courier appointments,
ledger-based earnings/targets, notification inbox, avatar change, phone change and
customer deletion/courier termination remain pending product/security decisions;
stored preferences or schema fields are not functional mobile endpoints. No new
support operation is promised. Approved future recovery is at most14 days with an
explicit deadline, support coordination for returning funds/data and detailed email;
active liabilities and retention rules remain undecided, so do not advertise it yet.

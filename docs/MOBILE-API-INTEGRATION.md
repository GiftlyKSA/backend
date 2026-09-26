# Giftly mobile API integration catalog

**OpenAPI 3.1 contract:** [mobile-openapi.json](mobile-openapi.json) is the machine-readable specification for the 43 implemented non-admin HTTP operations. Import it into an OpenAPI viewer or client generator; its schemas define exact wire types, required fields, and status codes, while `x-mobile-screen`, `x-audience`, `x-before`, `x-dependent-api`, and `x-availability` carry integration guidance. This companion guide adds call sequences, the chat WebSocket contract, and unsupported-screen gaps.

**Verified against backend source and offline development OpenAPI on 2026-09-27.** This catalogs every implemented non-admin HTTP endpoint (43) plus the chat WebSocket. Admin dashboard and `/api/admin/*` endpoints are excluded. Screen names come from the [mobile UI handoff](../../mobile/docs/BACKEND-SCREEN-API-MAP.md); that handoff describes a prototype, so backend source is authoritative when they differ. Development-only and simulation routes are inventoried for completeness and explicitly excluded from mobile production integration.

## Integration conventions

- Base path is `/api`. Send `Authorization: Bearer <access_token>` on protected HTTP calls. Existing users receive 30-minute access and rotating 30-day refresh credentials; use `POST /api/auth/refresh` and replace both stored tokens. Logout invalidates credentials on every device. Do not derive role or ownership from the phone number, local fixture, or a client-supplied user ID.
- `UUID string`, ISO timestamp, and Gregorian `YYYY-MM-DD` are wire values. **All money and tax rates are decimal strings**, such as `"125.50"` and `"0.15"`, not JSON numbers or halala integers. Localize Arabic display text and numerals only in the UI. A question mark after a field name means the field may be omitted; `| null` means the wire value can be null.
- A successful `204` has no body. Lists use bounded `limit` (1–100) and `next_cursor`; pass that cursor unchanged to the same list route. Order, message, and transaction cursors are UUID strings. Inbox cursors are opaque `<timestamp>|<uuid>` strings. Do not use offset or invent a next page when `next_cursor` is null.
- Domain failures generally use `{"error":{"code":"...","message":"...","request_id":"..."}}`; common codes include `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `INVALID_STATE_TRANSITION`, `VALIDATION_ERROR`, `RATE_LIMITED`, and `PAYMENTS_DISABLED`. FastAPI request-schema errors may instead return `{"detail":[...]}` with HTTP 422. HTTP 429 carries `Retry-After`. Handle status and both shapes; never assume every failure has a domain envelope.
- Production wallet top-up and invoice payment currently return HTTP 503 `PAYMENTS_DISABLED`; Dhamen has no live adapter or verified callback. Do not enable checkout UI as if it works. `/api/dev/*` exists only in development; the simulation webhook is absent in production. Direct S3 upload requires a signed PUT and then confirmation before attaching a key.
- This document describes the current backend, not a proposed API. Each **When/how** paragraph is 50–100 words. **Before** is the prerequisite; **Then / dependent API** tells the UI agent which subsequent call consumes or follows this result.
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
- **Request body:** `RegisterRequest` — `registration_token: string`; `role: string [CUSTOMER, COURIER]`; `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `city?: string | null`; `national_id?: string | null`; `passport_id?: string | null`
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

**When/how (60 words):** Rotate the current refresh credential when the access JWT expires or shortly before expiry. Replace both locally stored tokens atomically with the returned pair; a used token must never be retried in parallel because replay revokes its family. If refresh fails, clear local credentials and return to login. Do not send the refresh token as a normal bearer access token.

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
- **Response:** HTTP 200; `UserMeResponse` — `id: string`; `phone: string`; `role: string`; `status: string`; `full_name?: string | null`; `email?: string | null`; `rating: string`; `rating_count: integer`; `courier_profile?: CourierProfileResponse | null`
- **Before:** Login, register, or refresh.
- **Then / dependent API:** Role-specific home, `GET /api/wallets/me`, and `GET /api/orders`.

**When/how (65 words):** Fetch the current user's authoritative profile after restoring a stored session and whenever the profile screen opens. Use the returned role and status to select the mobile experience; do not trust a saved role from prototype fixtures. Courier verification details appear only in `courier_profile`. The response does not include date of birth or theme preference, so those values cannot currently be restored from this endpoint.

### PATCH /api/users/me

- **API name:** Update Me.
- **Screens:** Profile `/profile`.
- **Who / authorization:** Authenticated customer or courier; bearer access token.
- **Path, query, headers:** None.
- **Request body:** `UserUpdateRequest` — `full_name?: string | null`; `email?: string | null`; `dob?: date (YYYY-MM-DD) | null`; `courier_city?: string | null`; `courier_bio?: string | null`
- **Response:** HTTP 200; `UserMeResponse` — `id: string`; `phone: string`; `role: string`; `status: string`; `full_name?: string | null`; `email?: string | null`; `rating: string`; `rating_count: integer`; `courier_profile?: CourierProfileResponse | null`
- **Before:** `GET /api/users/me`.
- **Then / dependent API:** Refresh `GET /api/users/me` or update local profile from response.

**When/how (59 words):** Send only fields the user actually changed; omitted properties leave existing values untouched, while explicit null can clear optional values. Customer and courier users can edit common details; courier city and biography apply to courier profiles. Use the returned profile as the new display state. Phone changes, avatar upload, and theme preference are not offered by this mobile API.

### POST /api/users/me/courier-verification/resubmit

- **API name:** Resubmit Courier Verification.
- **Screens:** Courier Profile `/profile` verification state.
- **Who / authorization:** Authenticated courier with rejected profile; bearer access token.
- **Path, query, headers:** None.
- **Request body:** No JSON body.
- **Response:** HTTP 200; `UserMeResponse` — `id: string`; `phone: string`; `role: string`; `status: string`; `full_name?: string | null`; `email?: string | null`; `rating: string`; `rating_count: integer`; `courier_profile?: CourierProfileResponse | null`
- **Before:** `GET /api/users/me` shows rejected courier verification.
- **Then / dependent API:** `GET /api/users/me` to follow review status.

**When/how (60 words):** Use this action only after a courier has corrected a rejected profile and requests another verification review. It does not verify the courier immediately; the response returns the updated account and courier status for the UI. Do not expose it as a routine profile-save button. Operational courier endpoints remain unavailable until the profile is active and verified by the backend.

### GET /api/users/{user_id}/participant

- **API name:** Get Participant Profile.
- **Screens:** Order detail `/order/[id]`; Chat `/chat/[id]`.
- **Who / authorization:** Authenticated order or conversation participant; bearer access token.
- **Path, query, headers:** `user_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `ParticipantProfile` — `id: string`; `display_name: string`; `role: string`; `rating: string`; `rating_count: integer`; `initials: string`; `avatar_url?: string | null`; `courier_city?: string | null`; `courier_bio?: string | null`
- **Before:** Obtain the other party's ID from an owned order or inbox item.
- **Then / dependent API:** Optionally `GET /api/users/{user_id}/ratings/summary`.

**When/how (61 words):** Retrieve a minimal public-facing profile for the customer or courier on an order. The backend requires proof that the caller shares an order or conversation with the target; arbitrary account browsing is not supported. Use its display name, initials, rating, and safe courier details in chat headers and order detail. Do not expect phone, email, identity documents, or full profile fields.

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
- **Request body:** `UploadUrlRequest` — `purpose: string [ORDER_REQUEST, DELIVERY_PROOF]`; `content_type: string [image/jpeg, image/png]`; `byte_size: integer`
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
- **Request body:** `CreateOrderRequest` — `description?: string | null`; `delivery_city: string`; `latitude: number`; `longitude: number`; `delivery_date: date (YYYY-MM-DD)`; `request_media_keys?: string[]`
- **Response:** HTTP 201; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_date: string`; `description: string | null`; `latitude?: number | null`; `longitude?: number | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Optional `POST /api/media/upload-urls` → direct PUT → confirm for 0–3 photos.
- **Then / dependent API:** `GET /api/orders/{order_id}` and `/waiting/[id]`; courier `GET /api/orders/available`.

**When/how (57 words):** Create a gift request for a city, drop-off coordinates, and Gregorian delivery date. Send at most three previously confirmed `ORDER_REQUEST` storage keys, not local file URIs. The server returns a `NEW` order and generated UUID; it does not return the prototype's order number, period, recipient phone, or media URLs. Use the UUID for waiting and detail navigation.

### GET /api/orders

- **API name:** List Orders.
- **Screens:** Customer/Courier Home `/home`; Orders `/orders`; Courier Calendar `/calendar` partial.
- **Who / authorization:** Authenticated customer or eligible verified courier; bearer access token.
- **Path, query, headers:** `status?: string | null` (query); `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderListResponse` — `items: OrderSummary[]`; `next_cursor?: string | null`
- **Before:** Login and profile restoration.
- **Then / dependent API:** `GET /api/orders/{order_id}`; same endpoint with next cursor.

**When/how (64 words):** List only orders owned by the customer or assigned to the courier, newest first. Use optional status filtering and cursor pagination to build current and history tabs; the server does not provide text search, order-number search, or a date-range filter. Courier availability is a separate route. Keep server status strings authoritative and derive display grouping locally without assuming the prototype's labels are API values.

### GET /api/orders/available

- **API name:** Available Orders.
- **Screens:** Courier Orders `/orders` available tab; Home `/home` radar.
- **Who / authorization:** Authenticated active verified courier; bearer access token.
- **Path, query, headers:** `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderListResponse` — `items: OrderSummary[]`; `next_cursor?: string | null`
- **Before:** Courier verification complete; matching city stored on profile.
- **Then / dependent API:** `POST /api/orders/{order_id}/accept`.

**When/how (63 words):** Load a paginated radar of `NEW` orders in the courier's city. Summaries intentionally omit exact drop-off coordinates, customer identity, and full media, so use this list for selection rather than a full private detail screen. The courier cannot read an unassigned order's participant detail through the normal detail route. Accepting is an explicit action and may fail if another courier wins the race.

### GET /api/orders/{order_id}

- **API name:** Get Order.
- **Screens:** Waiting `/waiting/[id]`; Order detail `/order/[id]`; Delivery `/delivery/[id]`.
- **Who / authorization:** Authenticated customer or assigned eligible courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_date: string`; `description: string | null`; `latitude?: number | null`; `longitude?: number | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Order UUID from create, owned list, or accepted order.
- **Then / dependent API:** Active invoice, participant profile, chat, rating, or state transition.

**When/how (63 words):** Fetch the authoritative state of an order the caller participates in. Customers can inspect their own order; couriers gain participant access once assigned. The response includes status, city, date, amount, assignment time, and coordinates only when the service allows them. It does not include a full timeline, attached image URLs, delivery proof gallery, or invoice items; fetch related resources separately where endpoints exist.

### POST /api/orders/{order_id}/accept

- **API name:** Accept Order.
- **Screens:** Courier Orders `/orders` available tab; Order detail `/order/[id]`.
- **Who / authorization:** Authenticated active verified courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_date: string`; `description: string | null`; `latitude?: number | null`; `longitude?: number | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** `GET /api/orders/available` supplies a `NEW` order UUID.
- **Then / dependent API:** `GET /api/orders/{order_id}`, chat, then invoice creation.

**When/how (62 words):** Claim a `NEW` order as the current verified courier. The backend checks city, assignment capacity, and current order state under locks; the first valid acceptance wins. Do not show assignment as complete until the response succeeds, because another courier may accept concurrently. The response provides the assigned order detail; use its UUID and status to open chat and later issue an invoice.

### POST /api/orders/{order_id}/cancel

- **API name:** Cancel Order.
- **Screens:** Waiting `/waiting/[id]`; Order detail `/order/[id]`.
- **Who / authorization:** Authenticated customer or assigned eligible courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `CancelOrderRequest` — `reason?: string | null`
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_date: string`; `description: string | null`; `latitude?: number | null`; `longitude?: number | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Participant order still in a cancellable pre-progress state.
- **Then / dependent API:** Refresh `GET /api/orders/{order_id}` and owned list.

**When/how (65 words):** Cancel a participant order before work is in progress, optionally recording a short reason. The service rechecks the locked state, so a concurrent acceptance or payment transition may cause a rejection; display the server result rather than optimistic finality. A known backend gap can leave an issued invoice and wallet reservation pending when cancelling from `WAITING_PAYMENT`; do not promise immediate funds release in that state.

### POST /api/orders/{order_id}/deliver

- **API name:** Deliver Order.
- **Screens:** Courier Delivery `/delivery/[id]`.
- **Who / authorization:** Authenticated active verified assigned courier; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `DeliverRequest` — `latitude: number`; `longitude: number`; `proof_media_keys: string[]`; `note?: string | null`
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_date: string`; `description: string | null`; `latitude?: number | null`; `longitude?: number | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
- **Before:** Order `IN_PROGRESS`; 1–5 confirmed `DELIVERY_PROOF` image keys.
- **Then / dependent API:** Customer `POST /api/orders/{order_id}/approve` or dispute.

**When/how (58 words):** Submit current courier coordinates and confirmed delivery-proof photo keys after reaching the drop-off location. The backend checks assignment, state, radius, ownership, and purpose of each image; it then marks the order delivered. Send the actual current location in decimal degrees and do not substitute a cached order location. This is a state transition, not a generic photo-upload call.

### POST /api/orders/{order_id}/approve

- **API name:** Approve Order.
- **Screens:** Customer Order detail `/order/[id]` after delivery.
- **Who / authorization:** Authenticated customer who owns the order; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `OrderDetail` — `id: string`; `status: string`; `customer_id: string`; `courier_id: string | null`; `delivery_city: string`; `delivery_date: string`; `description: string | null`; `latitude?: number | null`; `longitude?: number | null`; `total_amount: string`; `assigned_at: string | null`; `created_at: string`; `current_actor_has_rated: boolean`
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
- **Response:** HTTP 201; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `tax_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Accepted `ASSIGNED` order; collect 1–20 line items.
- **Then / dependent API:** Customer `GET /api/orders/{order_id}/invoice` and promo/payment flow.

**When/how (59 words):** Issue an itemized invoice for an assigned order. Send line titles, net unit prices, quantities, tax fractions, optional descriptions, courier fee, and optional promo code as decimal strings where applicable. The server computes service fees, discounts, tax, totals, revision-backed storage, and expiry. Use the returned invoice amounts; the mobile app must not send or calculate a trusted final total.

### GET /api/orders/{order_id}/invoice

- **API name:** Get Order Invoice.
- **Screens:** Customer/Courier Order detail `/order/[id]`; Invoice `/invoice/[id]`.
- **Who / authorization:** Authenticated customer or assigned courier participant; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `tax_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Owned/assigned order with an active invoice.
- **Then / dependent API:** `GET /api/invoices/{invoice_id}`, promo preview, pay, or courier cancel.

**When/how (64 words):** Fetch the current active invoice for a participant order when the UI knows the order UUID but not the invoice UUID. Render the server's itemized amounts and status as decimal strings. This route is for the active invoice, not an invoice history list; a cancelled or expired invoice may no longer be the active result. Use the returned ID for payment or invoice-specific navigation.

### GET /api/invoices/{invoice_id}

- **API name:** Get Invoice.
- **Screens:** Customer/Courier Invoice `/invoice/[id]`; Order detail `/order/[id]`.
- **Who / authorization:** Authenticated customer or assigned courier participant; bearer access token.
- **Path, query, headers:** `invoice_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `tax_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
- **Before:** Invoice UUID from creation or active-order invoice.
- **Then / dependent API:** Customer payment or courier cancellation when eligible.

**When/how (63 words):** Read one invoice by its UUID after participant ownership checks. Unlike the order-scoped active-invoice route, this address can identify the particular invoice the screen is showing, including one whose status has changed. Display item lines, fee breakdown, promo snapshot, issue and expiry times, and total directly from the response. The backend does not expose saved card details or a collection of invoice revisions.

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
- **Response:** HTTP 200; `InvoiceResponse` — `id: string`; `order_id: string`; `status: string`; `currency: string`; `items_net_amount: string`; `courier_fee_amount: string`; `service_fee_amount: string`; `discount_amount: string`; `net_after_discount_amount: string`; `tax_amount: string`; `total_amount: string`; `promo_code: string | null`; `issued_at: string | null`; `expires_at: string | null`; `items: InvoiceItemResponse[]`
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
- **Then / dependent API:** Review discount; payment or courier invoice creation owns actual reservation.

**When/how (61 words):** Preview a promo against the customer's own order and active invoice before showing a discounted total. Send the code and order UUID; the backend checks eligibility and computes the proposed discount. This endpoint does not reserve, consume, or settle the promo. Treat the response as a preview only, and continue to use the final invoice/payment response as the authoritative charged amount.

## Ratings

### POST /api/orders/{order_id}/ratings

- **API name:** Rate Order.
- **Screens:** Customer/Courier Order detail `/order/[id]` after completion.
- **Who / authorization:** Authenticated customer or assigned courier participant; bearer access token.
- **Path, query, headers:** `order_id: UUID string` (path).
- **Request body:** `RatingRequest` — `score: integer`; `comment?: string | null`
- **Response:** HTTP 201; `RatingResponse` — `id: string`; `order_id: string`; `rated_user_id: string`; `score: integer`; `comment?: string | null`
- **Before:** Order completed and caller has not already rated it.
- **Then / dependent API:** `GET /api/users/{user_id}/ratings/summary`; refresh order detail.

**When/how (63 words):** Submit a score from one to five and an optional short comment for the other participant on a completed order. The backend derives the rated user from the order; do not send or select a target user ID. Each participant can rate once, so use `current_actor_has_rated` in order responses to decide whether to show the action. Display the returned rating only after success.

### GET /api/users/{user_id}/ratings/summary

- **API name:** User Rating Summary.
- **Screens:** Order detail `/order/[id]`; Chat `/chat/[id]` profile header.
- **Who / authorization:** Any authenticated customer or courier; bearer access token.
- **Path, query, headers:** `user_id: UUID string` (path).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `RatingSummaryResponse` — `user_id: string`; `average_score: string`; `count: integer`
- **Before:** User UUID from an order or participant profile.
- **Then / dependent API:** Optionally participant profile or rating action on completed order.

**When/how (59 words):** Fetch a user's aggregate received rating and count for a compact trust indicator. This endpoint needs authentication but does not require the caller to share an order with the target, unlike the participant-profile endpoint. Use the decimal-string average directly for display and avoid treating it as a detailed review list. The API does not return individual ratings or comments.

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

### GET /api/conversations/{conversation_id}/messages

- **API name:** List Messages.
- **Screens:** Customer/Courier Chat `/chat/[id]`.
- **Who / authorization:** Authenticated conversation participant; bearer access token.
- **Path, query, headers:** `conversation_id: UUID string` (path); `cursor?: string | null` (query); `limit?: integer` (query).
- **Request body:** No JSON body.
- **Response:** HTTP 200; `MessagePage` — `items: MessageResponse[]`; `next_cursor?: string | null`
- **Before:** Conversation ID from inbox after order assignment.
- **Then / dependent API:** Same endpoint with next cursor; WebSocket or REST send.

**When/how (61 words):** Load the conversation's decrypted text and system messages, newest first, in bounded pages. Use `next_cursor` to retrieve older messages and reverse/display them as needed in chronological chat order. The backend enforces membership, so a guessed conversation ID will not expose another user's content. After opening the thread, connect the WebSocket for live events and use read acknowledgement to clear unread counts.

### POST /api/conversations/{conversation_id}/messages

- **API name:** Send Message.
- **Screens:** Customer/Courier Chat `/chat/[id]` composer.
- **Who / authorization:** Authenticated conversation participant; bearer access token.
- **Path, query, headers:** `conversation_id: UUID string` (path).
- **Request body:** `SendMessageRequest` — `text: string`
- **Response:** HTTP 201; `MessageResponse` — `id: string`; `conversation_id: string`; `sender_id: string`; `message_type: string`; `content: string`; `is_read: boolean`; `created_at: string`
- **Before:** Conversation exists and actor is a participant.
- **Then / dependent API:** WebSocket receives committed event; refresh messages/inbox as needed.

**When/how (62 words):** Send a text message with a nonempty `text` field, at most 4,000 characters. The server encrypts it at rest, commits it, publishes a live event, and sends a generic push notification without message text. Show the returned message after success and reconcile it with a matching WebSocket event to avoid duplicates. This route does not accept media attachments or arbitrary recipient IDs.

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

**When/how (62 words):** Register or refresh the current device's push token using `IOS` or `ANDROID`. The server associates that token with the authenticated account and can move a handed-down device token away from a previous owner. Register again after token rotation or a successful new login. Do not confuse this with an in-app notification-list API; the backend has no endpoint to fetch a notification feed.

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

## Live chat WebSocket

### WS /api/ws/conversations/{conversation_id}

- **API name:** Live conversation stream.
- **Screens:** Customer/Courier Chat `/chat/[id]` live stream.
- **Who / authorization:** Authenticated conversation participant; `?token=<access JWT>` at handshake.
- **Path/query:** `conversation_id: UUID string` (path); `token: access JWT string` (query).
- **Client frame:** JSON `{"text":"message"}` (`text: string`; keep it within 4,000 characters for REST parity; the socket itself enforces a 4,096-byte frame cap).
- **Server frame:** JSON `MessageResponse` — `id: string`; `conversation_id: string`; `sender_id: string`; `message_type: string`; `content: string`; `is_read: boolean`; `created_at: string`
- **Before:** Conversation ID from inbox and a valid access token.
- **Then / dependent API:** Use REST messages/read for history and unread state.

**When/how (63 words):** Open this WebSocket only while the conversation screen needs live updates. Send JSON text frames shaped as `{"text":"..."}`; the server persists accepted messages before publishing message-shaped JSON events to participants. Invalid, oversized, or throttled frames may be dropped. Reconnect with a fresh access token after refresh, close on logout, and fetch REST history after reconnection because pub/sub is not a durable replay log.

## Reusable nested data types

These types appear inside the request/response shapes above. Field names marked `?` may be omitted; `| null` means nullable. An API response may still include a nullable field explicitly as null.

### CourierProfileResponse

- `city_of_residence`: `string`.
- `bio?`: `string | null`.
- `verification_status`: `string`.
- `rejection_reason?`: `string | null`.
- `avatar_url?`: `string | null`.

### OrderSummary

- `id`: `string`.
- `status`: `string`.
- `delivery_city`: `string`.
- `delivery_date`: `string`.
- `description`: `string | null`.
- `created_at`: `string`.
- `current_actor_has_rated`: `boolean`.

### InvoiceLineRequest

- `title`: `string`.
- `description?`: `string | null`.
- `unit_price_amount`: `string`.
- `quantity`: `integer`.
- `tax_rate?`: `string`.

### InvoiceItemResponse

- `position`: `integer`.
- `title`: `string`.
- `description`: `string | null`.
- `unit_price_amount`: `string`.
- `quantity`: `integer`.
- `tax_rate`: `string`.
- `line_net_amount`: `string`.
- `line_discount_amount`: `string`.
- `line_taxable_amount`: `string`.
- `line_tax_amount`: `string`.
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

## Screen-to-API handoff and current gaps

The prototype's labels and local-device data are not server contracts. The table maps each screen to implemented reads/actions and calls out missing backend support. A gap means **API to be added later**, not permission to build or guess a route now; leave that screen action pending until the backend contract exists.

| Mobile screen | Implemented calls | Integration gap or limit |
| --- | --- | --- |
| Welcome `/welcome` | None | Static content. |
| Phone login `/login`, OTP `/verify-otp` | `send-otp`, `verify-otp`, `refresh` | Demo phone-role fixtures and permissive demo OTP are not backend rules. |
| Customer registration `/register-customer` | `register`, `users/me` | Backend supports courier registration too, but mobile has no courier onboarding flow. Name length/age-16 rules from prototype are not enforced here. |
| Help `/help` | None | Static app content; no help/contact API. |
| Customer Home `/home` | `users/me`, `wallets/me`, `orders`, `conversations` | No occasions, notification-feed, or aggregated home endpoint. |
| Create order `/request` | `media/upload-urls`, signed PUT, `media/confirm`, `orders` | Backend allows **0–3** photos, not prototype's four; no morning/evening period, recipient phone, city-choices endpoint, or returned image URLs. |
| Waiting `/waiting/[id]` | `orders/{id}` | No separate push/poll status stream; refresh the order. |
| Customer/Courier Orders `/orders` | `orders`; courier `orders/available` | No customer text/order-number search; no order number in responses. Courier should not show a search box. |
| Order detail `/order/[id]` | `orders/{id}`, active invoice, participant, ratings, chat | No timeline events or media/proof download/list API in the returned contract. |
| Chat `/chat/[id]` | `conversations`, messages, read, WebSocket | Conversation ID comes from inbox; WebSocket has no durable replay, so use REST history after reconnect. |
| Invoice `/invoice/[id]` | `orders/{id}/invoice`, `invoices/{id}`, promo preview; courier create/cancel; customer pay | No saved-card API or exposed invoice revision field. Production payment is disabled. |
| Customer Calendar `/calendar` | None for occasions | Occasion CRUD/calendar APIs are absent. |
| Courier Calendar `/calendar` | `orders` can supply assigned delivery dates | No appointment CRUD, day/month range filter, or one-year appointments API. |
| Customer/Courier Wallet `/wallet` | `wallets/me`, transactions; top-up; courier withdrawal | No saved-card display/list API. Production top-up is disabled. No withdrawal-list API for courier. |
| Courier Reports `/reports` and settings sheet | Wallet transactions can be read as raw entries | No earnings aggregates, comparison, chart series, target settings, or financial-notification preference API. |
| Courier Statement `/statement` | Wallet transactions, cursor-paged | No date-range filtering, statement totals, or downloadable statement API. |
| Profile `/profile` | `users/me`, patch, courier resubmit, logout, device registration | Date of birth is writeable but absent from `users/me` response; no theme preference, avatar upload/read, or phone-change API. |

**Important contract mismatches:** The mobile map proposes halala integers, but the backend uses decimal **strings** for money. It proposes local file URIs/upload IDs, but the backend expects confirmed server-generated `storage_key` values. It proposes a matching/accepted/preparing/delivering timeline, while the actual order status enum is `NEW`, `ASSIGNED`, `WAITING_PAYMENT`, `IN_PROGRESS`, `DELIVERED`, `COMPLETED`, `CANCELLED`, `DISPUTED`, `REFUNDED`. Translate those labels only for display; do not send prototype strings to the API. The server does not enforce the prototype's age-16 minimum. Order delivery dates are constrained by the database to today through 180 days ahead; the UI should prevent out-of-range entries.

## Recommended call sequences

1. **Existing login:** `send-otp` → `verify-otp` → store returned token pair → `users/me` → role-specific home data. **New login:** use the returned `registration_token` with `register`, then follow the same profile flow.
2. **Create order with photos:** for each of at most three images, request an `ORDER_REQUEST` upload URL → signed PUT bytes → confirm the key → create the order with confirmed keys → navigate to `/waiting/[id]` using the returned UUID.
3. **Courier fulfillment:** `orders/available` → accept → conversation inbox/chat → create invoice → customer reads invoice and attempts payment only when a supported gateway is enabled → courier uploads/ confirms `DELIVERY_PROOF` → deliver → customer approves or disputes → each participant may rate after completion.
4. **Chat:** get inbox for `conversation_id` → page REST history → open WebSocket while visible → send with REST or JSON WebSocket frames → mark read. On socket reconnect, reload REST history to cover missed ephemeral events.
5. **Wallet:** fetch snapshot and transactions. For courier withdrawal, send `Idempotency-Key` with amount and IBAN, then refresh wallet. Do not expose top-up or invoice checkout as functional in production until the backend enables a verified Dhamen integration.

## Source and verification

Derived from `app/main.py`, `app/routers/`, `app/schemas/`, service eligibility/state checks, and an **offline** development OpenAPI build with dummy settings. No database, Redis, Docker, real storage, or payment provider was contacted for this document. The OpenAPI HTTP inventory was compared against all router registrations: **43 non-admin HTTP operations plus one WebSocket** are represented above. `/api/admin/*` and server-rendered `/admin/*` are deliberately excluded. The mobile file supplied with the request was used only to name and map screens, not as authority for backend behavior.

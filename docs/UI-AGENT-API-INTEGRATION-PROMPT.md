# Prompt for the Giftly mobile UI agent

Work in the Giftly **mobile** repository. Update the existing screens and API client to
match the implemented backend; keep the current visual design unless a contract mismatch
requires a small UI change. Read the mobile repository's instructions and current code
before editing. Use these backend files as the source of truth:

- `../backend/docs/mobile-openapi.json` — OpenAPI 3.1 request/response types and all 45
  implemented non-admin HTTP operations.
- `../backend/docs/MOBILE-API-INTEGRATION.md` — screen mapping, call order, WebSocket,
  environment behavior, and unsupported features.
- `docs/BACKEND-SCREEN-API-MAP.md` in the mobile repository — prototype screen
  inventory only. Where it conflicts with the backend contract, follow the backend
  and record the mismatch.

The backend source and contract were reviewed on 2026-09-30. For the latest order-chat,
profile, city, map-link, and rating changes, follow
`../backend/docs/UI-AGENT-ORDER-CHAT-UPDATE-PROMPT.md` as an additional implementation brief.
Do not implement server-rendered `/v1/admin/admin` pages or admin TOTP in the mobile app.
Do not invent routes for unsupported screens; mark those actions as waiting for a later
backend API and keep their UI honest about the limit.

Check and update these flows in the app:

1. **Login and sessions:** Normalize Saudi mobile input; call `send-otp`, use its
   `expires_in` value (currently 60 seconds) for countdown, and submit the OTP as a
   string. Development can return a numeric five-digit `otp_dev`; production does not
   return it and uses a six-digit SMS code. Existing users receive access/refresh tokens;
   new users receive a registration token and must call `register`. Choose customer or
   courier screens from the backend role, never demo phone fixtures. On refresh, replace
   both tokens atomically and avoid parallel use of one refresh token. On logout, clear
   protected local state, close chat sockets, and return to login; backend logout revokes
   all account sessions.
2. **Cities and orders:** Load `GET /api/cities` for city selectors, display `name_ar` in
   Arabic, and submit selected UUIDs. The HTTPS Google Maps URL is optional; do not send or
   compute latitude, longitude, radius, or distance. Create orders with zero to three
   confirmed request-photo storage keys. Use the backend's actual order statuses and
   delivery-date bounds, translating labels only for display. Courier available orders
   and push notifications are hints: refresh the appropriate order/list before showing
   authoritative state. Deduplicate repeated push events by order ID.
3. **Money, invoice, and wallet:** Parse and send money as decimal strings, not integer
   halalas or floating-point numbers. Never send a client-calculated payment amount.
   Production top-up and invoice payment return `PAYMENTS_DISABLED` until Dhamen is
   implemented; do not present checkout as functional or treat a browser redirect as
   payment success. Use the specified `Idempotency-Key` on withdrawal requests.
4. **Media, chat, and notifications:** Follow upload URL → signed PUT → confirm → attach
   storage key. For an old order, use `GET /api/orders/{order_id}/conversation` to find its
   conversation ID directly. Page REST history and reconnect the WebSocket with a fresh
   access token after refresh; load REST history again because
   socket events are not replayed. Register/unregister device push tokens when appropriate.
   There is no in-app notification-feed endpoint.
5. **Screen gaps:** Compare every customer and courier screen to the handoff table in
   `MOBILE-API-INTEGRATION.md`. Leave unsupported occasions, saved cards, courier report
   aggregates/targets, date-filtered statements, theme/avatar/phone changes, and other
   listed gaps pending. Do not fabricate backend data or silently rely on prototype-only
   fixtures for these features.

Keep the API client typed against the OpenAPI schemas. Handle 204 empty responses,
cursor pagination, 422 validation errors, the documented domain error envelope, 429
`Retry-After`, and 503 payment-disabled states. Add or update focused tests for any
integration changes. When finished, report which screens you changed, which implemented
endpoints each uses, what remains blocked by missing backend APIs, and which tests ran.

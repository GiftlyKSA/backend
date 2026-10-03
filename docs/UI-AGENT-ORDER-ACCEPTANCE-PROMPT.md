# Courier acceptance and customer live order updates

Updated 2026-10-03. Apply this contract to the courier Available Orders screen and
customer Waiting / Order Detail screens. Preserve unrelated behavior.

```text
Implement Giftly courier order acceptance and customer live updates:

1. List claimable orders: GET /api/orders/available
   Authorization: Bearer <courier_access_token>
   Who: authenticated ACTIVE, verified COURIER.
   Only NEW orders in the courier profile's assigned city are returned.
   No request body. Use returned items[].id (UUID string) for acceptance.
   Pagination: limit integer 1..100 (default 20); cursor optional string;
   response: {"items": [OrderSummary...], "next_cursor": string|null}.

2. Accept: POST /api/orders/{order_id}/accept
   Authorization: Bearer <courier_access_token>
   Path order_id: UUID string. No query parameters or JSON request body.
   Who: authenticated ACTIVE, verified COURIER in this order's city.
   Courier identity comes from the token; never send courier_id or customer_id.
   The server serializes assignment with database row locks and a Redis lock.
   Only one acceptance wins; existing assignment cannot be overwritten.
   Maximum active courier assignments: 3.

   Success HTTP 200 example:
   {
     "id": "550e8400-e29b-41d4-a716-446655440000",
     "status": "ASSIGNED",
     "customer_id": "550e8400-e29b-41d4-a716-446655440002",
     "courier_id": "550e8400-e29b-41d4-a716-446655440001",
     "delivery_city": "Riyadh",
     "delivery_city_id": "550e8400-e29b-41d4-a716-446655440003",
     "delivery_date": "2026-10-10",
     "description": "Gift request",
     "total_amount": "0.00",
     "assigned_at": "2026-10-03T10:00:00+00:00",
     "created_at": "2026-10-03T09:00:00+00:00",
     "current_actor_has_rated": false
   }
   Types: IDs UUID strings; status string; delivery_city string;
   delivery_date YYYY-MM-DD string; description string|null;
   total_amount decimal string in SAR; assigned_at UTC datetime string|null;
   created_at UTC datetime string; current_actor_has_rated boolean.
   Accepted order statuses throughout API: NEW, ASSIGNED, WAITING_PAYMENT,
   IN_PROGRESS, DELIVERED, COMPLETED, CANCELLED, DISPUTED, REFUNDED.

   Errors use {"error":{"code":string,"message":string,"request_id":string}}.
   401: missing/invalid/expired/revoked token; refresh or sign in.
   403: wrong role, inactive/unverified courier, or active-assignment limit.
   404 NOT_FOUND: nonexistent order or courier from a different city.
   409 ORDER_ALREADY_ASSIGNED: another claimant is accepting, already assigned,
       or order no longer NEW. Refresh Available Orders; do not auto retry claim.
   422: invalid UUID/input. 429 RATE_LIMITED: respect Retry-After when present.
   Backend outages fail closed; show an error, reload state before retrying.

   Disable the Accept control while a request is pending. Update local state only
   after 200; remove that order from available results and open assigned detail.
   For chat use GET /api/chat/conversations to find the order's conversation_id;
   acceptance creates the conversation automatically. Then use existing chat
   REST pagination and chat WebSocket. Invoice creation remains a separate API.

3. Customer instant acceptance updates:
   Connect wss://<backend-host>/api/ws/orders/{order_id}
   Who: owning CUSTOMER, or assigned ACTIVE verified COURIER.
   Unassigned couriers and unrelated users cannot subscribe.
   Native clients: Authorization: Bearer <access_token> in handshake headers.
   Browser JavaScript:
     new WebSocket(url, ["giftly.orders", "bearer." + accessToken])
   Never put the token in query strings or logs. Server negotiates giftly.orders.
   No JSON request body. Send no application messages on this read-only socket.

   First event example:
   {"type":"order.snapshot","order_id":"550e8400-e29b-41d4-a716-446655440000",
    "status":"NEW","courier_id":null,"assigned_at":null}
   Acceptance event example:
   {"type":"order.updated","order_id":"550e8400-e29b-41d4-a716-446655440000",
    "status":"ASSIGNED","courier_id":"550e8400-e29b-41d4-a716-446655440001",
    "assigned_at":"2026-10-03T10:00:00Z"}
   type: order.snapshot|order.updated; order_id UUID string;
   status: existing status enum above; courier_id UUID string|null;
   assigned_at: UTC ISO datetime string|null. Display time in user's timezone.

   Apply snapshot/update by order_id and status. On ASSIGNED update customer
   Waiting screen immediately; fetch GET /api/orders/{order_id} with the owning
   customer's token for full current detail, then find its chat conversation.
   Acceptance broadcasts after database commit. Other transitions and missed
   hints reconcile every five seconds. Redis pub/sub has no durable replay.
   On reconnect fetch current detail; do not expect every intermediate state.
   Close codes: 4401 credentials, 4403 forbidden, 4429 capacity,
   4400 unsupported client message, 1013 temporary backend failure.
   Pre-handshake rejection may instead appear as a generic handshake failure.
   Reconnect with refreshed token and bounded backoff; never endlessly retry
   forbidden orders. Close sockets on logout/navigation and avoid duplicate
   connections (shared account ceiling: 8 chat/order sockets).

4. Never implement client-side authorization as the security boundary. Server
   checks current account, city and ownership. Render description as plain text.
   Do not invent delivery-location/map/coordinate fields or API endpoints.
```

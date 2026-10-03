# Customer occasions integration — 2026-10-03

```text
Implement customer Calendar /calendar and Add/Edit Occasion screens using the
existing Giftly backend. Occasions represent birthdays, anniversaries or other
special dates. Titles are free text, including Arabic. Render titles as plain
text, never HTML. No automatic reminders or annual recurrence exist yet.

AUTHENTICATION AND OWNERSHIP
Every endpoint requires Authorization: Bearer <customer_access_token>.
Only CUSTOMER accounts can use these endpoints. Ownership comes from the token;
never submit user_id. Couriers/admins receive 403. Other customers' records and
missing records both return 404. IDs and record timestamps are read-only.

CREATE
POST /api/occasions
Request example:
{"title":"Wedding anniversary","occasion_date":"2026-12-15","reminder_days_before":7}

Input:
- title: required string, 1–120 characters, not whitespace-only.
- occasion_date: required calendar date string, YYYY-MM-DD.
- reminder_days_before: optional integer 0–365, default 7; not boolean/string.
Additional properties are rejected. Past dates may be stored.
Do not send recurrence, featured_gift_id, user_id or notification fields.

HTTP 201 response example:
{
  "id":"550e8400-e29b-41d4-a716-446655440000",
  "title":"Wedding anniversary",
  "occasion_date":"2026-12-15",
  "reminder_days_before":7,
  "created_at":"2026-10-03T10:00:00Z",
  "updated_at":"2026-10-03T10:00:00Z"
}
Output types: id UUID string; title string; occasion_date YYYY-MM-DD string;
reminder_days_before integer; created_at/updated_at UTC ISO datetime strings.

LIST
GET /api/occasions?limit=25&from_date=2026-10-03
No body. Optional query parameters:
- limit: integer 1–100, default 25 (UI choices 25/50/100).
- from_date: YYYY-MM-DD; inclusive lower date bound. Omit to include past dates.
- cursor: UUID string from previous next_cursor; omit for the first page.
Response HTTP 200:
{"items":[OccasionResponse...],"next_cursor":UUID-string|null}
Sorted by occasion_date ascending then id ascending. Send returned cursor
unchanged; keep filters consistent. Null means no more pages. Reset cursor when
changing filters. Deleted, foreign or out-of-range anchors return 404; reload
the first page. Refresh pagination after edits that move a record's date.

VIEW
GET /api/occasions/{occasion_id}
Path occasion_id: UUID string. No body. HTTP 200 OccasionResponse above.

EDIT
PATCH /api/occasions/{occasion_id}
Example body: {"title":"Our anniversary","reminder_days_before":0}
Supply at least one of title, occasion_date, reminder_days_before. Same validation
as create. Omitted fields stay unchanged. Explicit nulls and empty bodies are
rejected. HTTP 200 returns the updated OccasionResponse.

DELETE
DELETE /api/occasions/{occasion_id}
No body. HTTP 204 with no response body. Remove the item from local state after
success; do not call response.json() for 204. Repeating deletion returns 404.

ERRORS
Standard envelope: {"error":{"code":string,"message":string,"request_id":string}}.
401 missing/invalid/expired/revoked credentials: refresh or sign in.
403 wrong role: do not show customer calendar for courier/admin accounts.
404 NOT_FOUND: record/cursor missing or inaccessible; refresh the list.
422 invalid fields, unknown properties, invalid date/UUID, null or empty PATCH.
429 RATE_LIMITED: respect Retry-After when present. Handle temporary errors with
bounded retry and preserve unsaved form content. Do not blindly retry create:
creation has no client idempotency key and could otherwise duplicate records.

UI REQUIREMENTS
- Show customers' saved special dates on Calendar and optionally Home.
- Add, edit and delete controls, loading/error/empty states and pagination.
- Use a DATE picker: this is a day, not a moment or delivery timestamp.
  Send YYYY-MM-DD without converting it through UTC (avoid shifting the day).
- Record timestamps are UTC; display them in the UI's chosen timezone.
- An occasion is one specific date; saving a past anniversary does not create
  next year's occurrence. Let users edit the date for future occurrences.
- reminder_days_before saves a preference only. If exposing this input, clearly
  explain that automatic notifications are coming later; do not promise delivery.
- Disable submission while pending. Preserve user-entered title text safely.
- Do not invent notification scheduling, recurrence or gift-discovery APIs.

Machine-readable request and response schemas: docs/mobile-openapi.json.
```

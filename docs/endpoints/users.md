# Users

## GET /api/users/me
Return the authenticated user's own profile. **Auth**: Bearer JWT.
### Success 200 — UserMeResponse
```json
{
  "id": "uuid", "phone": "+966501234567", "role": "CUSTOMER", "status": "ACTIVE",
  "full_name": "Nora", "email": "nora@example.com", "rating": "5.0", "rating_count": 0,
  "courier_profile": null
}
```
Courier owners receive a typed `courier_profile` containing only city, bio, account
verification status, an optional rejection reason visible to that owner, and an
optional signed avatar URL. Identity, contact, payment, and storage-key fields are not
part of this nested response.
The client reads profile data here, not from the JWT (which carries only ids), so edits
take effect immediately.

## PATCH /api/users/me
Update editable fields. **Auth**: Bearer JWT.
### Body (all optional, `extra="forbid"`)
| field | type | constraints |
| full_name | string | ≤120 |
| email | string | basic email shape, ≤255 |
| dob | date | |
| courier_city | string | courier accounts only, 1–100 |
| courier_bio | string \| null | courier accounts only, ≤1000 |
### Success 200 — UserMeResponse (the updated profile)

The actor is always the token subject; there is no path/body user id to tamper with.

## GET /api/users/{user_id}/participant
Return a compact participant profile only when the authenticated actor shares an order
or conversation with that user. Unauthorized and unknown users both return 404. The
response contains only display name, role, rating/count, initials, optional signed
avatar URL, and optional courier city/bio. A courier actor must still be ACTIVE and
verified; rejected, pending, banned, and unverified couriers receive 403.

## POST /api/users/me/courier-verification/resubmit
A `REJECTED` courier may request another review. The audited transition returns the
owner profile with `PENDING_VERIFICATION` and clears the prior owner-visible reason.
`REJECTED` couriers may authenticate only to inspect/update their owner profile and
resubmit; courier operational and financial endpoints return 403 until approval.

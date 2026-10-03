# Giftly chat media integration — 2026-10-03

Copy the following block to the mobile UI agent. Backend source and
`mobile-openapi.json` define the implemented contract. No new admin UI is required.

```text
Update Giftly's existing customer and courier Chat screens and old-order chat history.
Read your repository instructions first. Use ../backend/docs/mobile-openapi.json and
../backend/docs/MOBILE-API-INTEGRATION.md as the backend contract. Preserve the current
design and existing text messaging. Do not invent routes or show fake upload success.

WHO CAN USE THESE ENDPOINTS
All HTTP endpoints below require Authorization: Bearer <access_token>.
Only active, currently eligible CUSTOMER or COURIER accounts may use chat; courier
verification and other existing eligibility checks still apply. Conversation operations
and attachment playback are restricted to that order's customer and assigned courier.
Admin tokens and unrelated users cannot use these endpoints. Tokens and private URLs
must never be logged. Authentication is rechecked after media validation before saving.

SCREENS AND INPUT
Add microphone recording controls (record, stop/cancel, preview, send). Voice notes are
recorded through the microphone: do not expose an audio-file picker/import button.
The server validates audio bytes and limits; it cannot prove whether an audio file was
recorded through the microphone. Enforce this recorder-only product flow in the UI.
Add image/video camera and gallery selection. Request OS permissions when needed.
Send at most five images OR one video OR one voice note per message; do not mix types.
Keep text-only messages on the existing text endpoint. Render text/captions literally
with safe text bindings, never HTML injection or evaluation. Do not download media
through the API: uploads go directly to private S3, playback uses short-lived signed URLs.

1. GET /api/chat/media-limits
No request body or query. Fetch once when opening chat and use these live configuration
values for size checks, recorder countdown and camera settings. HTTP 200 example:
{
  "image_max_bytes": 10485760,
  "video_max_bytes": 125829120,
  "voice_max_bytes": 10485760,
  "video_max_duration_seconds": 120,
  "voice_max_duration_seconds": 120,
  "image_content_types": ["image/jpeg", "image/png"],
  "video_content_types": ["video/mp4", "video/webm"],
  "voice_content_types": ["audio/mp4", "audio/mpeg", "audio/ogg", "audio/webm", "audio/wav", "audio/aac"],
  "images_per_message": 5,
  "video_max_pixels": 2073600,
  "image_max_pixels": 20000000
}
Numeric properties are integers; content type lists contain strings. Byte limits use
exact bytes (10 MiB / 120 MiB defaults). Images must be JPEG/PNG and at most 20 million
pixels. Videos must fit 1080p: width and height each <=1920 and width*height <=2073600,
including portrait recordings. Voice/video duration is >0 and <= the configured limit.
Compress/resize before requesting a grant; convert HEIC to JPEG/PNG and export
QuickTime/MOV camera videos as MP4. Prefer H.264/AAC for mobile MP4 compatibility.
Strip MIME codec
parameters when declaring content_type (audio/webm;codecs=opus -> audio/webm).
Use an actual supported recorder/container, not a renamed extension. A voice note
must contain one audio stream and no video. A video must have one video stream and
optionally one audio stream. Unknown container duration in recorded WebM is supported;
the backend decodes the recording to check its actual duration.

2. GET /api/orders/{order_id}/conversation
Use an existing assigned order's UUID to open its current or historical chat.
No request body. HTTP 200:
{"conversation_id":"11111111-1111-4111-8111-111111111111","order_id":"22222222-2222-4222-8222-222222222222","other_user_id":"33333333-3333-4333-8333-333333333333"}
All three values are UUID strings. Missing/unassigned/foreign orders return 404.
This endpoint finds an existing conversation; it does not create one.

3. POST /api/conversations/{conversation_id}/media-upload-urls
Request Content-Type: application/json. Issue a separate grant for each attachment.
Body example for a microphone-recorded voice note:
{"media_type":"VOICE","content_type":"audio/mp4","byte_size":245760}
Fields: media_type:string, exactly IMAGE|VIDEO|VOICE; content_type:string, one matching
type from media-limits; byte_size:integer, actual encoded file size >0 and <= its limit.
For a photo use IMAGE/image/jpeg or IMAGE/image/png. For video use VIDEO/video/mp4
or VIDEO/video/webm. Extra body fields (including caller duration or sender ID) return 422.
HTTP 201:
{"upload_url":"https://<private-s3-signed-put-url>","storage_key":"chat/11111111-1111-4111-8111-111111111111/44444444-4444-4444-8444-444444444444.m4a","expires_in":300}
upload_url:string; storage_key:string opaque server-generated value; expires_in:integer
seconds. Use the returned key unchanged; never generate keys from user filenames.

4. PUT <upload_url> DIRECTLY TO S3
This is a signed S3 URL, not a Giftly API endpoint. Send raw file/recording bytes, not
JSON, base64 or multipart. Headers: Content-Type equal to the declared content_type;
If-None-Match: *. The actual content length must equal byte_size; native clients may
send Content-Length explicitly, browsers set it from the Blob. Do NOT send the Giftly
Authorization header to S3. Wait for successful S3 200/204 before the next step.
The upload is create-only; S3 412 means that key already exists, not a new upload.
Request a fresh grant if the URL expires. Show progress/cancel states and keep uploads
out of local persistent logs. There is NO separate /api/media/confirm step for chat.

5. POST /api/conversations/{conversation_id}/media-messages
Content-Type: application/json. After all selected objects upload successfully:
{"storage_keys":["chat/11111111-1111-4111-8111-111111111111/44444444-4444-4444-8444-444444444444.m4a"],"text":"Optional caption"}
storage_keys:required array of 1–5 unique strings, each <=512 characters, issued to
this account for this conversation. text:optional string <=4000 characters, default "".
The server checks real stored bytes, MIME/container, size, decoded duration and current
ownership. A message and all its attachments are saved in one atomic transaction.
Each grant can be consumed once. Validation can take several seconds: show sending
state and allow a request timeout above 60 seconds; do not send parallel duplicates.
HTTP 201 MessageResponse example:
{
  "id":"55555555-5555-4555-8555-555555555555",
  "conversation_id":"11111111-1111-4111-8111-111111111111",
  "sender_id":"66666666-6666-4666-8666-666666666666",
  "message_type":"VOICE",
  "content":"Optional caption",
  "is_read":false,
  "created_at":"2026-10-03T12:00:00+00:00",
  "attachments":[{
    "id":"77777777-7777-4777-8777-777777777777",
    "content_type":"audio/mp4",
    "byte_size":245760,
    "duration_seconds":12.5,
    "display_order":0
  }]
}
Message fields: id/conversation_id/sender_id UUID strings; message_type:string
(TEXT, IMAGE, VIDEO, VOICE; existing SYSTEM/MIXED values may occur); content:string;
is_read:boolean; created_at:ISO8601 UTC datetime string; attachments:array.
Attachment fields: id:UUID string; content_type:string; byte_size:integer;
duration_seconds:number|null (null for images, actual verified seconds for recordings);
display_order:integer 0–4, ascending display order. TEXT messages have attachments: [].
Do not expect storage keys or permanent playback URLs in message/history/WS responses.

6. GET /api/chat/attachments/{attachment_id}/url
Fetch on demand for a visible image or user-initiated voice/video playback.
Path attachment_id:UUID from message.attachments[].id. No request body/query.
HTTP 200: {"url":"https://<private-cloudfront-signed-url>","expires_in":300}
url:string; expires_in:integer seconds. Use URL directly in the image/audio/video
component; signed URL includes its own authentication. Refresh this endpoint when it
expires; never make the S3 bucket public. Do not share or indefinitely persist URLs.

7. GET /api/conversations/{conversation_id}/messages?limit=30&cursor=<next_cursor>
No request body. limit:integer 1–100, default 30. Omit cursor on first page; subsequently
send returned next_cursor unchanged (message UUID string). HTTP 200:
{"items":[<MessageResponse from step 5>],"next_cursor":"55555555-5555-4555-8555-555555555555"}
items:MessageResponse[] newest first; next_cursor:UUID string|null. Stop at null.
Old messages and attachments are available to eligible participants after completion.
Reverse pages for chronological display, merge by message.id, preserve attachment
display_order, and fetch playback URLs only for visible media. Do not fetch all pages
or all signed URLs eagerly. Missing/foreign pagination anchors return 404.

8. GET /api/conversations?limit=20&cursor=<opaque_cursor>
Existing inbox. limit:integer 1–100 (default 20); cursor:optional returned opaque string.
HTTP 200: {"items":[{"conversation_id":"<UUID>","order_id":"<UUID>","other_user_id":"<UUID>","last_message_preview":"Voice note","unread_count":1,"last_message_timestamp":"2026-10-03T12:00:00+00:00"}],"next_cursor":null}
Preview:string|null; unread_count:integer; timestamp:UTC ISO8601 datetime; IDs:UUID
strings. Media without captions gets an inbox preview label rather than an empty row.

9. POST /api/conversations/{conversation_id}/messages
Existing text-only send. JSON {"text":"Hello"}; required nonempty string <=4000 chars.
HTTP 201 MessageResponse, message_type TEXT, attachments []. Do not put media here.

10. POST /api/conversations/{conversation_id}/read
No JSON body. HTTP 204 with NO response body. Call when inbound messages are actually
visible; update inbox unread count. Do not parse an empty 204 body as JSON.

11. LIVE CHAT WEBSOCKET
wss://<backend>/api/ws/conversations/{conversation_id}?token=<access_token>
Use ws:// only on local HTTP development. This existing socket checks authentication,
membership, account eligibility, token expiry/revocation and shared connection limits.
Receive MessageResponse-shaped JSON events including attachments. New media sends
publish after database commit; reconcile HTTP and WS copies by message.id. Binary
media does NOT go through the WebSocket. Existing text frames: {"text":"Hello"}.
Refresh access tokens and reconnect on expiry, close sockets on logout. On reconnect,
fetch REST history; Redis live delivery is not a durable replay mechanism. Also refetch
after the app resumes because a saved message can miss its live notification.
Socket close codes include 4401 unauthenticated/revoked, 4403 foreign conversation,
4429 connection quota, 1013 temporary dependency/capacity failure, 1011 server failure.

ERRORS AND RETRIES
Domain errors use {"error":{"code":"BAD_REQUEST","message":"...","request_id":"..."}}.
400 BAD_REQUEST: mismatched/unsupported MIME or container, malformed bytes, size or
duration exceeded, duplicate/mixed keys, or a foreign/unavailable upload.
401 UNAUTHORIZED: missing/expired/revoked token; perform the established refresh/login.
403 FORBIDDEN: role or current account/courier eligibility disallows the action.
404 NOT_FOUND: missing/foreign conversation, order, attachment or history cursor.
409 CONFLICT: used/deleting upload or outstanding grant quota exceeded (20 grants /
250 MiB total per account). Do not loop or silently bypass the quota.
422: invalid request schema/UUID/range or undeclared properties.
429 RATE_LIMITED: respect Retry-After; authenticated HTTP defaults 60 requests/minute
per user, with additional existing WebSocket message limits.
503 MEDIA_VALIDATION_UNAVAILABLE: decoder unavailable, validation capacity busy or
validation deadline exceeded. Keep the upload and allow a deliberate later retry.
Other dependency/server errors: display a safe error, do not mark unsaved messages sent.
After an ambiguous network failure, refresh history before retrying. If the upload
was already consumed, recover the saved message from history instead of reuploading
automatically. Playback failures/expired URLs require a fresh authorized URL.

DEPLOYMENT AND VERIFICATION
Backend deployment must migrate through 0015_chat_media and rebuild its image to include
FFmpeg/ffprobe. Private S3 and CloudFront must allow the chat/ prefix; browser S3 CORS
must permit the app origin, PUT, Content-Type and If-None-Match. Native mobile clients
do not use browser CORS. Development/test use storage fakes, not a functional S3 upload
server; use UI mocks for local flow tests and a real private-storage deployment for
end-to-end tests. Never pretend the fake upload URL stores real recording bytes.
Backend env defaults: CHAT_AUDIO_MAX_DURATION_SECONDS=120,
CHAT_VIDEO_MAX_DURATION_SECONDS=120, CHAT_AUDIO_MAX_UPLOAD_BYTES=10485760,
CHAT_IMAGE_MAX_UPLOAD_BYTES=10485760, CHAT_VIDEO_MAX_UPLOAD_BYTES=125829120.
These values may be lowered; GET media-limits is authoritative for the UI.
Add focused tests for recorder-only input, camera/gallery, supported formats, oversize
and overlong media, expired URLs, history paging, forbidden access, WS deduplication,
failed/retried sends and refresh/logout. Report changed screens, endpoints and tests.
```

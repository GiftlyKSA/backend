# Private chat media implementation

Last update: 2026-10-03. Scope: recorded voice notes, camera/gallery images and videos
for existing order conversations. See [the UI handoff](UI-AGENT-CHAT-MEDIA-PROMPT.md).

| Task | Status | Urgency | Note |
| --- | --- | --- | --- |
| Private conversation-scoped upload and playback | Implemented | High | Existing S3 create-only grants, actor/conversation ownership, short-lived signed reads. |
| Voice notes | Implemented | High | Recorder UI contract, audio-only stream, configurable 120-second/10-MiB defaults. Backend cannot attest microphone origin. |
| Images and video | Implemented | High | Five JPEG/PNG images or one MP4/WebM video; 10-MiB image and 120-MiB/120-second video defaults. |
| Server byte/container/duration validation | Implemented; native deployment check pending | High | Full Pillow image decode; restricted local FFmpeg decode, bounded output/deadlines, two shared validation slots. |
| Atomic attachment claims | Implemented; PostgreSQL race validation pending | High | Grant locks in stable order, one-time atomic claims and conversation lock; message and attachments roll back together. |
| History and live delivery | Implemented | High | One batched attachment query per message page; metadata in REST and WS. Media sends preserve HTTP success if post-commit live/push delivery fails. |
| OpenAPI/UI handoff | Updated | High | New routes, expanded message schema, exact byte limits and full integration prompt. |
| Disposable PostgreSQL/Redis and real S3/CloudFront | Unverified locally | High | No Docker started. Run CI integration tests and private-storage staging checks before release. |

## Security and performance boundaries

The API accepts small metadata bodies; file bytes travel directly to S3. Grant URLs
bind exact content type, size and create-only headers. Chat send rechecks current
authentication after validation and eligible conversation membership under a lock.
Playback also checks current eligibility and scoped attachment ownership. Captions
remain encrypted at rest and are returned literally; clients must use safe text rendering.
No public permanent media URLs or user-supplied outbound URLs are accepted.

Private validation reads at most the declared byte size plus one byte, verifies actual
type/container/streams and rejects malformed files. Image decoding is limited to 20
million pixels; video must fit 1080p, including portrait. Native decoding disables
network protocols and allowlists demuxers, with a 64-MiB per-allocation limit, single
decoder threads, bounded process output and 20-second process deadlines. Missing
decoders fail closed. Recorded WebM without a duration header uses decoded timestamps.
The shared Redis admission limit is two validators for the deployment, each with a
60-second lease and a 45-second validation deadline; capacity exhaustion returns 503.
Network work occurs after closing the read transaction, before taking write locks.
The API materializes bounded files in memory and recordings in temporary files. Budget
memory and writable temporary storage for concurrent 120-MiB videos; validation is
deliberately conservative and is not a production capacity measurement.

The client microphone-only restriction is a product rule, not a provable security
property. Native codecs remain dependency attack surfaces: keep the image updated
and run its security checks. This change does not certify the codebase vulnerability-free.
Live publication is best-effort; REST history remains authoritative. Existing text-send
retry/durable-event limitations are not claimed fixed by this media change.

## Migration, deployment and rollback

Deploy the rebuilt image (FFmpeg/ffprobe included), then the existing bootstrap/migration
owner applies `0015_chat_media` before serving API workers. It adds `VOICE` to the
PostgreSQL message enum, nullable attachment duration, chat upload purpose and MIME/
size checks. It does not rewrite applied migrations or existing messages. S3/CloudFront
policies must permit the private `chat/` prefix; browser CORS must permit create-only
PUT headers. Development/test storage fakes do not provide a real upload server.

For rollback, stop new media writes and preserve a database/object backup. Prefer a
forward corrective migration. The downgrade intentionally fails if chat uploads or
non-image attachments remain; review and migrate that data before attempting it. It
leaves the unused `VOICE` enum label because removing a PostgreSQL enum value requires
rebuilding its type and is outside the safe downgrade. Rolling back old application
code while VOICE messages remain is incompatible and requires a reviewed data plan.

## Verification

Focused policy/service tests cover boundaries, disguised markup, invalid duration,
audio-only voice notes, WebM duration fallback, decoder absence, foreign/reused grants,
capacity admission and closing the database transaction before storage validation.
The image/history/playback/replay integration test requires PostgreSQL and Redis.
The native WAV test skips when FFmpeg/ffprobe are unavailable locally. Final command
results are recorded below; mocks do not establish live S3 or
production decoder integration success.

- `uv run --locked ruff check .`: passed.
- `uv run --locked ruff format --check .`: passed (294 Python files).
- `uv run --locked mypy app`: passed (182 source files).
- Both `pre-commit run --all-files` and its `--hook-stage pre-push` checks: passed.
- Alembic offline upgrade `0014_overdue_order_index:head` and downgrade
  `0015_chat_media:0014_overdue_order_index`: SQL generation passed; no database changed.
- Mobile OpenAPI drift check passed; contract inventories 56 non-admin HTTP operations.
- Complete suite `uv run --locked pytest -n 4 -p no:cacheprovider`: 603 passed,
  196 skipped, four existing Starlette/httpx deprecation warnings. The new native
  decoder check and database/service-dependent tests were skipped locally.
- A subsequent corrupt-PNG checksum regression reproduced a Pillow `SyntaxError`;
  the targeted fix maps it to the stable 400 client error. All focused chat-media
  validation/service/route checks then passed, with the native decoder test skipped.
- Native image decoding and bounded S3-read tests passed locally. Native recording,
  PostgreSQL/Redis integration, real private S3/CloudFront and CI image checks remain
  unverified locally. No Docker was run. The deployment image decoder test is added
  to CI, including acceptance of a short recording and rejection of an overlong one.

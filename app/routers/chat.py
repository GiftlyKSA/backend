"""REST messaging and a live WebSocket per conversation.

Message content is encrypted at rest and decrypted only for participants. The WebSocket
prefers an access-token header or subprotocol, retaining legacy query authentication.
Tokens are verified against the same denylist as
the REST API, and only a conversation's two members may connect. Live delivery rides a
Redis pub/sub channel, so a message sent on any instance reaches every open socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit_context import mark_request_transaction, set_audit_actor
from app.core.config import Settings
from app.core.deps import Actor, get_db, get_redis, get_settings, require_auth, require_role
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.jwt import JwtError, decode_access_token
from app.core.ratelimit import RateLimiter
from app.core.ws_connections import WebSocketLease
from app.models.enums import UserRole
from app.repositories.chat_repository import ChatRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.user_repository import UserRepository
from app.schemas.chat import (
    AttachmentUrlResponse,
    ChatAttachmentResponse,
    ChatMediaLimitsResponse,
    ChatUploadRequest,
    ConversationResponse,
    InboxItemResponse,
    InboxResponse,
    MessagePage,
    MessageResponse,
    SendChatMediaRequest,
    SendMessageRequest,
)
from app.schemas.media import UploadUrlResponse
from app.services.auth_service import validate_access_claims, validate_account_claims
from app.services.chat_media_service import ChatMediaService
from app.services.chat_media_validation import MEDIA_TYPES
from app.services.chat_service import ChatMessage, ChatService, conversation_channel
from app.services.courier_eligibility_service import CourierEligibilityService

router = APIRouter(prefix="/api", tags=["chat"])
_logger = logging.getLogger(__name__)

DbDep = Annotated[AsyncSession, Depends(get_db)]
_Participant = require_role(UserRole.CUSTOMER, UserRole.COURIER)


def _service(request: Request, db: AsyncSession) -> ChatService:
    return _session_service(db, get_redis(request), get_settings(request))


def _session_service(db: AsyncSession, redis: Redis, settings: Settings) -> ChatService:
    """Build chat operations with the shared current-state eligibility boundary."""
    return ChatService(
        chat=ChatRepository(db),
        redis=redis,
        settings=settings,
        eligibility=CourierEligibilityService(
            users=UserRepository(db), couriers=CourierRepository(db)
        ),
    )


def _message(dto: ChatMessage) -> MessageResponse:
    return MessageResponse(
        id=dto.id,
        conversation_id=dto.conversation_id,
        sender_id=dto.sender_id,
        message_type=dto.message_type,
        content=dto.content,
        is_read=dto.is_read,
        created_at=dto.created_at,
        attachments=[ChatAttachmentResponse(**vars(attachment)) for attachment in dto.attachments],
    )


def _media_service(request: Request, db: AsyncSession) -> ChatMediaService:
    return ChatMediaService(
        session=db,
        chat=_service(request, db),
        repository=ChatRepository(db),
        uploads=MediaRepository(db),
        storage=request.app.state.clients.storage,
        settings=get_settings(request),
        redis=get_redis(request),
        eligibility=CourierEligibilityService(
            users=UserRepository(db),
            couriers=CourierRepository(db),
        ),
    )


@router.get("/chat/media-limits", response_model=ChatMediaLimitsResponse)
async def chat_media_limits(
    request: Request,
    db: DbDep,
    actor: Annotated[Actor, Depends(_Participant)],
) -> ChatMediaLimitsResponse:
    """Return configured bounds to an eligible authenticated chat user."""
    await CourierEligibilityService(
        users=UserRepository(db),
        couriers=CourierRepository(db),
    ).require_eligible_actor(actor.id)
    settings = get_settings(request)
    return ChatMediaLimitsResponse(
        image_max_bytes=settings.CHAT_IMAGE_MAX_UPLOAD_BYTES,
        video_max_bytes=settings.CHAT_VIDEO_MAX_UPLOAD_BYTES,
        voice_max_bytes=settings.CHAT_AUDIO_MAX_UPLOAD_BYTES,
        video_max_duration_seconds=settings.CHAT_VIDEO_MAX_DURATION_SECONDS,
        voice_max_duration_seconds=settings.CHAT_AUDIO_MAX_DURATION_SECONDS,
        image_content_types=list(MEDIA_TYPES["IMAGE"]),
        video_content_types=list(MEDIA_TYPES["VIDEO"]),
        voice_content_types=list(MEDIA_TYPES["VOICE"]),
    )


@router.post(
    "/conversations/{conversation_id}/media-upload-urls",
    response_model=UploadUrlResponse,
    status_code=201,
)
async def request_chat_upload(
    request: Request,
    db: DbDep,
    conversation_id: uuid.UUID,
    body: ChatUploadRequest,
    actor: Annotated[Actor, Depends(_Participant)],
) -> UploadUrlResponse:
    """Issue a private upload grant for a participant's chat attachment."""

    async def resume_writes() -> None:
        await mark_request_transaction(db)
        if await require_auth(request, db) != actor:
            raise UnauthorizedError("This session is no longer valid.")

    url, key, expires = await _media_service(request, db).request_upload(
        conversation_id=conversation_id,
        actor_id=actor.id,
        kind=body.media_type,
        mime=body.content_type,
        size=body.byte_size,
        release_reads=db.commit,
        resume_writes=resume_writes,
    )
    return UploadUrlResponse(upload_url=url, storage_key=key, expires_in=expires)


@router.post(
    "/conversations/{conversation_id}/media-messages",
    response_model=MessageResponse,
    status_code=201,
)
async def send_chat_media(
    request: Request,
    db: DbDep,
    conversation_id: uuid.UUID,
    body: SendChatMediaRequest,
    actor: Annotated[Actor, Depends(_Participant)],
) -> MessageResponse:
    """Validate uploaded bytes, then atomically attach them to a new chat message."""
    service = _media_service(request, db)
    if body.client_message_id is not None:
        replay = await _service(request, db).replay_message(
            conversation_id=conversation_id,
            sender_id=actor.id,
            text=body.text,
            client_message_id=body.client_message_id,
            storage_keys=body.storage_keys,
        )
        if replay is not None:
            await db.commit()
            await _deliver_chat_message(_service(request, db), replay)
            return _message(replay)
    try:
        attachments = await service.prepare(
            conversation_id=conversation_id,
            actor_id=actor.id,
            keys=body.storage_keys,
        )
    except (BadRequestError, ConflictError):
        if body.client_message_id is None:
            raise
        await mark_request_transaction(db)
        if await require_auth(request, db) != actor:
            raise UnauthorizedError("This session is no longer valid.") from None
        replay = await _service(request, db).replay_message(
            conversation_id=conversation_id,
            sender_id=actor.id,
            text=body.text,
            client_message_id=body.client_message_id,
            storage_keys=body.storage_keys,
        )
        if replay is None:
            raise
        await db.commit()
        await _deliver_chat_message(_service(request, db), replay)
        return _message(replay)
    await mark_request_transaction(db)
    current_actor = await require_auth(request, db)
    if current_actor != actor:
        raise UnauthorizedError("This session is no longer valid.")
    dto = await service.send(
        conversation_id=conversation_id,
        actor_id=actor.id,
        attachments=attachments,
        text=body.text,
        client_message_id=body.client_message_id,
    )
    await db.commit()
    await _deliver_chat_message(_service(request, db), dto)
    return _message(dto)


async def _deliver_chat_message(
    service: ChatService,
    dto: ChatMessage,
) -> bool:
    """Keep optional delivery failures from changing the result of a committed send."""
    delivered = False
    try:
        async with asyncio.timeout(5):
            published = await service.publish_message(dto)
        delivered = published is not False
    except Exception:
        _logger.exception("The chat message was saved, but live delivery failed.")
    return delivered


@router.get("/chat/attachments/{attachment_id}/url", response_model=AttachmentUrlResponse)
async def chat_attachment_url(
    request: Request,
    db: DbDep,
    attachment_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
) -> AttachmentUrlResponse:
    """Sign private playback after checking current participant permissions."""
    url = await _media_service(request, db).playback(attachment_id=attachment_id, actor_id=actor.id)
    return AttachmentUrlResponse(url=url, expires_in=300)


@router.get("/conversations", response_model=InboxResponse)
async def list_conversations(
    request: Request,
    db: DbDep,
    actor: Annotated[Actor, Depends(_Participant)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> InboxResponse:
    """List the caller's conversations, most-recent activity first (keyset paged)."""
    items = await _service(request, db).list_inbox(
        user_id=actor.id, limit=limit, before=_parse_cursor(cursor)
    )
    rows = [
        InboxItemResponse(
            conversation_id=i.conversation_id,
            order_id=i.order_id,
            other_user_id=i.other_user_id,
            last_message_preview=i.last_message_preview,
            unread_count=i.unread_count,
            last_message_timestamp=i.last_message_timestamp,
        )
        for i in items
    ]
    next_cursor = (
        f"{items[-1].last_message_timestamp}|{items[-1].conversation_id}"
        if len(items) == limit
        else None
    )
    return InboxResponse(items=rows, next_cursor=next_cursor)


@router.get("/orders/{order_id}/conversation", response_model=ConversationResponse)
async def get_order_conversation(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
) -> ConversationResponse:
    """Find an order's existing conversation, including after order completion."""
    conversation = await _service(request, db).get_conversation_for_order(
        order_id=order_id, actor_id=actor.id
    )
    other_user_id = (
        conversation.courier_id
        if actor.id == conversation.customer_id
        else conversation.customer_id
    )
    return ConversationResponse(
        conversation_id=str(conversation.id),
        order_id=str(conversation.order_id),
        other_user_id=str(other_user_id),
    )


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
async def list_messages(
    request: Request,
    db: DbDep,
    conversation_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
    cursor: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> MessagePage:
    """Return decrypted messages the caller can see (participant only)."""
    items = await _service(request, db).list_messages(
        conversation_id=conversation_id,
        actor_id=actor.id,
        limit=limit,
        before_id=cursor,
    )
    next_cursor = items[-1].id if len(items) == limit else None
    return MessagePage(items=[_message(m) for m in items], next_cursor=next_cursor)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=201,
)
async def send_message(
    request: Request,
    db: DbDep,
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    actor: Annotated[Actor, Depends(_Participant)],
) -> MessageResponse:
    """Send a text message; participants receive it live over the WebSocket."""
    service = _service(request, db)
    dto = await service.send_message(
        conversation_id=conversation_id,
        sender_id=actor.id,
        text=body.text,
        client_message_id=body.client_message_id,
    )
    # A live event must never race ahead of the durable row it announces.
    await db.commit()
    await _deliver_chat_message(service, dto)
    return _message(dto)


@router.post("/conversations/{conversation_id}/read", status_code=204)
async def mark_read(
    request: Request,
    db: DbDep,
    conversation_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
) -> None:
    """Mark the caller's inbound messages read and clear their unread count."""
    await _service(request, db).mark_read(conversation_id=conversation_id, actor_id=actor.id)


@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_ws(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """Live chat socket: pushes new messages and accepts sent messages.

    Authenticated by ``?token=`` (same verification as the REST API). Only the
    conversation's members may connect; anyone else is closed with policy violation.
    """
    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    if settings.is_production and origin is not None and origin not in settings.cors_origins:
        await websocket.close(code=4403)
        return
    actor = await _authenticate_ws(websocket)
    if actor is None:
        await websocket.close(code=4401)  # unauthenticated
        return

    factory = websocket.app.state.session_factory
    async with factory() as session:
        try:
            conversation = await _session_service(
                session, websocket.app.state.redis, websocket.app.state.settings
            ).get_conversation_for_actor(conversation_id=conversation_id, actor_id=actor.id)
        except (ForbiddenError, NotFoundError):
            conversation = None
    if conversation is None:
        await websocket.close(code=4403)  # not a participant
        return
    recipient_id = (
        conversation.courier_id
        if actor.id == conversation.customer_id
        else conversation.customer_id
    )

    redis: Redis = websocket.app.state.redis
    lease = WebSocketLease(redis, actor.id)
    try:
        admitted = await lease.acquire()
    except Exception:
        await websocket.close(code=1013)
        return
    if not admitted:
        await websocket.close(code=4429)
        return
    try:
        await _run_admitted_ws(
            websocket, conversation_id, actor, recipient_id, factory, redis, lease
        )
    finally:
        with contextlib.suppress(Exception):
            await lease.release()


async def _run_admitted_ws(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    actor: Actor,
    recipient_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    lease: WebSocketLease,
) -> None:
    """Serve an admitted socket and release its subscription and tasks on exit."""
    pubsub = websocket.app.state.subscription_redis.pubsub()
    tasks: list[asyncio.Task[None]] = []
    try:
        async with asyncio.timeout(5):
            await pubsub.subscribe(conversation_channel(conversation_id))
        protocols = websocket.scope.get("subprotocols", [])
        await websocket.accept(subprotocol="giftly.chat" if "giftly.chat" in protocols else None)
        tasks = [
            asyncio.create_task(_pump_pubsub_to_socket(pubsub, websocket, conversation_id)),
            asyncio.create_task(
                _pump_socket_to_chat(
                    websocket, conversation_id, actor, recipient_id, factory, redis
                )
            ),
            asyncio.create_task(_monitor_authorization(websocket, conversation_id, lease)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except (UnauthorizedError, ForbiddenError, NotFoundError):
        await websocket.close(code=4401)
    except RedisConnectionError:
        await websocket.close(code=1013)
    except Exception:
        await websocket.close(code=1011)
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            async with asyncio.timeout(3):
                await pubsub.unsubscribe(conversation_channel(conversation_id))
        with contextlib.suppress(Exception):
            async with asyncio.timeout(3):
                await pubsub.aclose()


async def _pump_pubsub_to_socket(
    pubsub: PubSub, websocket: WebSocket, conversation_id: uuid.UUID
) -> None:
    async for event in pubsub.listen():
        if event.get("type") == "message":
            await _require_live_authorization(websocket, conversation_id)
            data = event["data"]
            await _send_socket_frame(
                websocket, data.decode() if isinstance(data, bytes) else str(data)
            )


async def _pump_socket_to_chat(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    actor: Actor,
    recipient_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> None:
    """Read inbound frames, guard them, persist the message, and push the recipient.

    Guards (audit SEC-4/LOG-2/LOG-3): frames over ``WS_MAX_FRAME_BYTES`` are dropped;
    each sender is throttled through the shared Redis rate limiter; a mid-connection
    ban closes the socket. Both transports persist push intents atomically with messages
    for the scheduled worker to deliver.
    """
    settings = websocket.app.state.settings
    limiter = RateLimiter(
        redis,
        max_requests=settings.WS_RATE_LIMIT_MAX_MESSAGES,
        window_seconds=settings.WS_RATE_LIMIT_WINDOW_SECONDS,
    )
    while True:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > settings.WS_MAX_FRAME_BYTES:
            continue  # oversized frame: dropped before any decrypt/persist work
        decision = await limiter.check_guarded(
            f"ws:{actor.id}", blocked_key=f"jwt:denylist:{actor.jti}"
        )
        if decision.blocked:
            raise UnauthorizedError("This session is no longer valid.")
        if not decision.allowed:
            continue  # over the per-user message ceiling: dropped
        await _require_live_authorization(websocket, conversation_id)
        body = _extract_send_request(raw)
        if body is None:
            await _send_socket_frame(
                websocket,
                json.dumps(
                    {"error": {"code": "VALIDATION_ERROR", "message": "Invalid chat message."}}
                ),
            )
            continue
        async with factory() as session:
            await mark_request_transaction(session)
            await set_audit_actor(session, category=actor.role.value, actor_user_id=actor.id)
            service = _session_service(session, redis, settings)
            try:
                dto = await service.send_message(
                    conversation_id=conversation_id,
                    sender_id=actor.id,
                    text=body.text,
                    client_message_id=body.client_message_id,
                )
            except ConflictError as exc:
                await session.rollback()
                await _require_live_authorization(websocket, conversation_id)
                await _send_socket_frame(
                    websocket, json.dumps({"error": {"code": exc.code, "message": exc.message}})
                )
                continue
            # Commit before any external side effect or live event can expose the row.
            await session.commit()
            delivered = await _deliver_chat_message(service, dto)
            if not delivered:
                await _require_live_authorization(websocket, conversation_id)
                await _send_socket_frame(websocket, _message(dto).model_dump_json())


def _extract_send_request(raw: str) -> SendMessageRequest | None:
    """Validate REST-compatible text and optional retry identity from a socket frame."""
    try:
        return SendMessageRequest.model_validate_json(raw)
    except ValidationError:
        return None


def _extract_text(raw: str) -> str:
    """Return validated text for callers that only need the text boundary."""
    body = _extract_send_request(raw)
    return body.text if body is not None else ""


async def _authenticate_ws(
    websocket: WebSocket, conversation_id: uuid.UUID | None = None
) -> Actor | None:
    token = _socket_token(websocket)
    if not token or len(token) > 8192:
        return None
    settings = websocket.app.state.settings
    try:
        claims = decode_access_token(settings, token)
    except JwtError:
        return None
    redis: Redis = websocket.app.state.redis
    try:
        async with websocket.app.state.session_factory() as session:
            if conversation_id is None:
                await validate_access_claims(claims, redis=redis, users=UserRepository(session))
            else:
                if await redis.get(f"jwt:denylist:{claims.jti}"):
                    raise UnauthorizedError("This session has been revoked.")
                state = await ChatRepository(session).get_live_state(
                    conversation_id, uuid.UUID(claims.sub)
                )
                validate_account_claims(claims, state.user if state else None)
                assert state is not None
                CourierEligibilityService.validate_eligible_actor(
                    state.user, state.courier_verified
                )
                if state.conversation_id is None:
                    raise NotFoundError("Conversation not found.")
    except UnauthorizedError:
        return None
    if claims.exp <= datetime.now(UTC).timestamp():
        return None
    try:
        role = UserRole(claims.role)
    except ValueError:
        return None
    if role not in (UserRole.CUSTOMER, UserRole.COURIER):
        return None
    return Actor(id=uuid.UUID(claims.sub), role=role, jti=claims.jti)


def _socket_token(websocket: WebSocket) -> str:
    """Prefer credential headers/protocols; retain query transport for existing clients."""
    header = websocket.headers.get("authorization")
    if header is not None:
        scheme, _, value = header.partition(" ")
        return value.strip() if scheme.lower() == "bearer" else ""
    for protocol in websocket.scope.get("subprotocols", []):
        if protocol.startswith("bearer."):
            return str(protocol[7:])
    return websocket.query_params.get("token", "")


async def _require_live_authorization(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """Check expiry, revocation, account state, and current conversation membership."""
    async with asyncio.timeout(5):
        actor = await _authenticate_ws(websocket, conversation_id)
    if actor is None:
        raise UnauthorizedError("This session is no longer valid.")


async def _monitor_authorization(
    websocket: WebSocket, conversation_id: uuid.UUID, lease: WebSocketLease
) -> None:
    """Recheck idle connections every five seconds, with a five-second dependency deadline."""
    while True:
        await _require_live_authorization(websocket, conversation_id)
        if not await lease.renew():
            raise UnauthorizedError("This connection is no longer active.")
        await asyncio.sleep(5)


def _parse_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    if len(cursor) > 128 or "|" not in cursor:
        raise BadRequestError("Invalid conversation pagination cursor.")
    ts_raw, _, id_raw = cursor.partition("|")
    try:
        timestamp = datetime.fromisoformat(ts_raw)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("The cursor timestamp must include its timezone.")
        return timestamp, uuid.UUID(id_raw)
    except ValueError as exc:
        raise BadRequestError("Invalid conversation pagination cursor.") from exc


async def _send_socket_frame(websocket: WebSocket, payload: str) -> None:
    """Bound the complete encoded frame; committed messages remain available by REST."""
    if len(payload.encode("utf-8")) > websocket.app.state.settings.WS_MAX_OUTGOING_FRAME_BYTES:
        await websocket.close(code=1009)
        raise WebSocketDisconnect(code=1009)
    await websocket.send_text(payload)

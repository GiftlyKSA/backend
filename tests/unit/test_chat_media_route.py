"""Post-validation authentication and post-commit media delivery behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.deps import Actor
from app.core.exceptions import UnauthorizedError
from app.models.enums import UserRole
from app.routers.chat import _pump_socket_to_chat, send_chat_media, send_message
from app.schemas.chat import SendChatMediaRequest, SendMessageRequest
from app.services.chat_service import ChatMessage

from tests.conftest import make_test_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["publish", "devices", "rollback"])
async def test_committed_text_returns_success_despite_delivery_failure(failure):
    actor = Actor(uuid4(), UserRole.CUSTOMER, "session")
    conversation = uuid4()
    chat, db = AsyncMock(), AsyncMock()
    dto = ChatMessage(
        str(uuid4()),
        str(conversation),
        str(actor.id),
        "TEXT",
        "Hello",
        False,
        "2026-10-04T00:00:00+00:00",
    )
    chat.send_message.return_value = dto
    if failure == "publish":
        chat.publish_message.side_effect = RuntimeError("Redis unavailable")
    if failure == "rollback":
        db.rollback.side_effect = RuntimeError("Database unavailable")
    notify = AsyncMock(
        side_effect=RuntimeError("Devices unavailable") if failure != "publish" else None
    )
    with (
        patch("app.routers.chat._service", return_value=chat),
        patch("app.routers.chat.ChatRepository") as repository,
        patch("app.routers.chat.NotificationService") as notifications,
    ):
        repository.return_value.get_for_actor = AsyncMock(
            return_value=SimpleNamespace(customer_id=actor.id, courier_id=uuid4())
        )
        notifications.return_value.notify_user = notify
        response = await send_message(
            SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(clients=SimpleNamespace(push=AsyncMock()))
                )
            ),
            db,
            conversation,
            SendMessageRequest(text="Hello"),
            actor,
        )
    assert response.id == dto.id
    assert response.content == "Hello"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_acknowledges_saved_message_when_delivery_fails():
    actor = Actor(uuid4(), UserRole.CUSTOMER, "session")
    conversation = uuid4()
    chat, db, redis = AsyncMock(), AsyncMock(), AsyncMock()
    dto = ChatMessage(
        str(uuid4()),
        str(conversation),
        str(actor.id),
        "TEXT",
        "Hello",
        False,
        "2026-10-04T00:00:00+00:00",
    )
    chat.send_message.return_value = dto
    chat.publish_message.side_effect = RuntimeError("Redis unavailable")
    db.__aenter__.return_value = db
    websocket = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=make_test_settings(), clients=SimpleNamespace(push=AsyncMock())
            )
        ),
        receive_text=AsyncMock(side_effect=['{"text":"Hello"}', StopAsyncIteration]),
        send_text=AsyncMock(),
    )
    with (
        patch("app.routers.chat._session_service", return_value=chat),
        patch("app.routers.chat.mark_request_transaction", new=AsyncMock()),
        patch("app.routers.chat.set_audit_actor", new=AsyncMock()),
        patch("app.routers.chat._require_live_authorization", new=AsyncMock()),
        patch("app.routers.chat.RateLimiter") as limiter,
        patch(
            "app.routers.chat.NotificationService", side_effect=RuntimeError("Devices unavailable")
        ),
        patch("app.routers.chat._notify_chat_recipient", new=AsyncMock(side_effect=RuntimeError)),
    ):
        limiter.return_value.check_guarded = AsyncMock(
            return_value=SimpleNamespace(blocked=False, allowed=True)
        )
        with pytest.raises(StopAsyncIteration):
            await _pump_socket_to_chat(websocket, conversation, actor, uuid4(), lambda: db, redis)
    assert dto.id in websocket.send_text.await_args.args[0]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_committed_media_returns_success_when_live_and_push_fail():
    actor = Actor(uuid4(), UserRole.CUSTOMER, "session")
    conversation = uuid4()
    media, chat, db = AsyncMock(), AsyncMock(), AsyncMock()
    dto = ChatMessage(
        str(uuid4()),
        str(conversation),
        str(actor.id),
        "VOICE",
        "",
        False,
        "2026-10-03T00:00:00+00:00",
    )
    media.send.return_value = dto
    chat.publish_message.side_effect = RuntimeError("Redis unavailable")
    with (
        patch("app.routers.chat._media_service", return_value=media),
        patch("app.routers.chat._service", return_value=chat),
        patch("app.routers.chat.require_auth", new=AsyncMock(return_value=actor)),
        patch("app.routers.chat.mark_request_transaction", new=AsyncMock()),
        patch("app.routers.chat._notify_chat_recipient", new=AsyncMock(side_effect=RuntimeError)),
    ):
        response = await send_chat_media(
            SimpleNamespace(), db, conversation, SendChatMediaRequest(storage_keys=["key"]), actor
        )
    assert response.id == dto.id
    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_authentication_after_validation_prevents_message_write():
    actor = Actor(uuid4(), UserRole.CUSTOMER, "session")
    media, db = AsyncMock(), AsyncMock()
    with (
        patch("app.routers.chat._media_service", return_value=media),
        patch("app.routers.chat.require_auth", new=AsyncMock(side_effect=UnauthorizedError)),
        patch("app.routers.chat.mark_request_transaction", new=AsyncMock()),
    ):
        with pytest.raises(UnauthorizedError):
            await send_chat_media(
                SimpleNamespace(), db, uuid4(), SendChatMediaRequest(storage_keys=["key"]), actor
            )
    media.prepare.assert_awaited_once()
    media.send.assert_not_awaited()
    db.commit.assert_not_awaited()

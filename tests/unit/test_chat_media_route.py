"""Post-validation authentication and post-commit media delivery behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.deps import Actor
from app.core.exceptions import UnauthorizedError
from app.models.enums import UserRole
from app.routers.chat import send_chat_media
from app.schemas.chat import SendChatMediaRequest
from app.services.chat_service import ChatMessage


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

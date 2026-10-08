"""PostgreSQL regression checks for atomic intent and reclaimed delivery leases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from app.models import ChatNotification, Message
from app.repositories.chat_notification_repository import ChatNotificationRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_chat_service import _conversation, _service


async def test_chat_message_and_intent_rollback_together(db_session: AsyncSession) -> None:
    customer, courier, conversation = await _conversation(db_session)
    message_id = uuid.uuid4()
    with pytest.raises(RuntimeError, match="abort send"):
        async with db_session.begin_nested():
            dto = await _service(db_session, AsyncMock()).send_message(
                conversation_id=conversation.id,
                sender_id=customer.id,
                text="test",
            )
            message_id = uuid.UUID(dto.id)
            intent = await db_session.get(ChatNotification, message_id)
            assert intent is not None
            assert intent.recipient_id == courier.id
            raise RuntimeError("abort send")
    assert await db_session.get(Message, message_id) is None
    assert (
        await db_session.scalar(
            select(ChatNotification.message_id).where(ChatNotification.message_id == message_id)
        )
        is None
    )


async def test_expired_delivery_is_reclaimed_and_old_worker_cannot_complete(
    db_session: AsyncSession,
) -> None:
    customer, _courier, conversation = await _conversation(db_session)
    dto = await _service(db_session, AsyncMock()).send_message(
        conversation_id=conversation.id,
        sender_id=customer.id,
        text="test",
    )
    intent = await db_session.get(ChatNotification, uuid.UUID(dto.id))
    assert intent is not None
    now = datetime(2000, 1, 1, tzinfo=UTC)
    intent.available_at = now
    await db_session.flush()
    repository = ChatNotificationRepository(db_session)
    claims = (await repository.claim_pending(now=now, limit=1, lease_seconds=60)).claims
    assert len(claims) == 1
    assert claims[0].message_id == uuid.UUID(dto.id)
    assert (await repository.claim_pending(now=now, limit=1, lease_seconds=60)).claims == []
    reclaimed_at = now + timedelta(seconds=61)
    reclaimed = (await repository.claim_pending(now=reclaimed_at, limit=1, lease_seconds=60)).claims
    assert len(reclaimed) == 1
    assert reclaimed[0].lease_id != claims[0].lease_id
    assert not await repository.advance(claims[0], now=reclaimed_at, cursor=None, complete=True)
    assert await repository.advance(reclaimed[0], now=reclaimed_at, cursor=None, complete=True)
    assert (
        await repository.claim_pending(now=reclaimed_at, limit=1, lease_seconds=60)
    ).claims == []

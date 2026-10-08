"""Bounded at-least-once live delivery from encrypted durable message references."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.crypto import build_aad, build_cipher
from app.core.db import build_engine, build_session_factory
from app.core.redis import build_redis
from app.repositories.chat_live_delivery_repository import ChatLiveDeliveryRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository
from app.services.chat_service import ChatService, attachment_dto, conversation_channel
from app.services.courier_eligibility_service import CourierEligibilityService

_logger = logging.getLogger(__name__)


async def send_pending_chat_live_deliveries(
    *,
    limit: int = 20,
    message_id: uuid.UUID | None = None,
    redis: Redis | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> int:
    """Claim at most twenty rows per tick, publishing within each fenced lease."""
    if not 1 <= limit <= 100:
        raise ValueError("Live delivery limit must be between one and one hundred.")
    settings = settings or get_settings()
    own_redis = redis is None
    redis = redis or build_redis(settings)
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)
    cipher = build_cipher(settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION)
    sent = 0
    try:
        for _ in range(limit):
            async with factory() as session:
                batch = await ChatLiveDeliveryRepository(session).claim_pending(
                    now=datetime.now(UTC),
                    limit=1,
                    lease_seconds=30,
                    message_id=message_id,
                )
                await session.commit()
            if not batch.claims:
                if batch.retired_count:
                    continue
                break
            claim = batch.claims[0]
            try:
                async with asyncio.timeout(5):
                    async with factory() as session:
                        repository = ChatRepository(session)
                        message = await repository.delivery_message(claim.message_id)
                        if message is None:
                            raise ValueError("Live delivery message missing.")
                        service = ChatService(
                            chat=repository,
                            redis=redis,
                            settings=settings,
                            eligibility=CourierEligibilityService(
                                users=UserRepository(session),
                                couriers=CourierRepository(session),
                            ),
                        )
                        await service.get_conversation_for_actor(
                            conversation_id=message.conversation_id,
                            actor_id=message.sender_id,
                        )
                        attachments = await repository.attachments_for_messages([message.id])
                        content = cipher.decrypt(
                            message.content_encrypted,
                            build_aad("messages", "content", str(message.conversation_id)),
                        )
                        dto = ChatService._to_dto(message, content)
                        payload = asdict(dto)
                        payload["attachments"] = [asdict(attachment_dto(a)) for a in attachments]
                    await redis.publish(
                        conversation_channel(uuid.UUID(dto.conversation_id)),
                        json.dumps(payload),
                    )
                async with factory() as session:
                    saved = await ChatLiveDeliveryRepository(session).advance(
                        claim,
                        now=datetime.now(UTC),
                    )
                    await session.commit()
                sent += int(saved)
            except Exception as exc:  # noqa: BLE001 - durable bounded retry on delivery failure
                _logger.error(
                    "Live chat delivery failed for %s (%s)", claim.message_id, type(exc).__name__
                )
                async with factory() as session:
                    await ChatLiveDeliveryRepository(session).retry(claim, now=datetime.now(UTC))
                    await session.commit()
    finally:
        try:
            if own_redis:
                await redis.aclose()
        finally:
            if own_engine is not None:
                await own_engine.dispose()
    return sent

"""Send committed chat push intents outside request paths and database transactions."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_clients
from app.integrations.push.base import PushClient
from app.repositories.chat_notification_repository import (
    ChatNotificationRepository,
    ClaimedChatNotification,
)

_logger = logging.getLogger(__name__)
_PAGE_SIZE = 500
_LEASE_SECONDS = 60
_DELIVERY_TIMEOUT_SECONDS = 20


async def _deliver_claim(
    claim: ClaimedChatNotification,
    *,
    factory: async_sessionmaker[AsyncSession],
    push: PushClient,
) -> bool:
    """Deliver a bounded page, fencing progress and retaining failed intents."""
    try:
        async with asyncio.timeout(_DELIVERY_TIMEOUT_SECONDS):
            async with factory() as session:
                page = await ChatNotificationRepository(session).token_page(
                    recipient_id=claim.recipient_id,
                    after=claim.cursor_token_id,
                    limit=_PAGE_SIZE,
                )
            if page:
                await push.send_push(
                    [token for _, token in page],
                    "New message",
                    "You have a new message about your order.",
                )
        async with factory() as session:
            saved = await ChatNotificationRepository(session).advance(
                claim,
                now=datetime.now(UTC),
                cursor=page[-1][0] if page else claim.cursor_token_id,
                complete=len(page) < _PAGE_SIZE,
            )
            await session.commit()
        if not saved:
            _logger.warning("chat notification lease expired for message %s", claim.message_id)
        return saved
    except Exception as exc:  # noqa: BLE001 - retain failed calls in durable retry state
        _logger.error(
            "chat notification delivery failed for message %s (%s)",
            claim.message_id,
            type(exc).__name__,
        )
        async with factory() as session:
            await ChatNotificationRepository(session).retry(claim, now=datetime.now(UTC))
            await session.commit()
        return False


async def send_pending_chat_notifications(
    *,
    limit: int = 20,
    push: PushClient | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> int:
    """Drain at most twenty recipient pages per scheduled run by default."""
    settings = settings or get_settings()
    owned_clients = build_clients(settings) if push is None else None
    if owned_clients is not None:
        push = owned_clients.push
    assert push is not None
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)
    sent = 0
    try:
        for _ in range(limit):
            async with factory() as session:
                batch = await ChatNotificationRepository(session).claim_pending(
                    now=datetime.now(UTC),
                    limit=1,
                    lease_seconds=_LEASE_SECONDS,
                )
                await session.commit()
            if not batch.claims and not batch.retired_count:
                break
            if batch.claims and await _deliver_claim(batch.claims[0], factory=factory, push=push):
                sent += 1
    finally:
        try:
            if owned_clients is not None:
                for client in (
                    owned_clients.gateway,
                    owned_clients.email,
                    owned_clients.sms,
                    owned_clients.push,
                    owned_clients.storage,
                ):
                    aclose = getattr(client, "aclose", None)
                    if aclose is not None:
                        await aclose()
        finally:
            if own_engine is not None:
                await own_engine.dispose()
    return sent

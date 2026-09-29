"""Deliver committed new-order pushes from a durable, paged outbox."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_clients
from app.integrations.push.base import PushClient
from app.repositories.order_notification_repository import (
    ClaimedNotification,
    OrderNotificationRepository,
)

_logger = logging.getLogger("app.workers.order_notifications")
_PAGE_SIZE = 500
_LEASE_SECONDS = 60
_PUSH_TIMEOUT_SECONDS = 20
_TITLE = "New gift request nearby"
_BODY = "A customer just posted a new order in your city."


async def _deliver_claim(
    claim: ClaimedNotification,
    *,
    factory: async_sessionmaker[AsyncSession],
    push: PushClient,
) -> bool:
    """Read one page, send outside a transaction, and fence the cursor update."""
    try:
        async with factory() as session:
            repo = OrderNotificationRepository(session)
            open_order = await repo.order_is_open(claim.order_id)
            page = (
                await repo.token_page(
                    city_id=claim.city_id,
                    after=claim.cursor_token_id,
                    limit=_PAGE_SIZE,
                )
                if open_order
                else []
            )
        if page:
            await asyncio.wait_for(
                push.send_push([token for _, token in page], _TITLE, _BODY),
                timeout=_PUSH_TIMEOUT_SECONDS,
            )
        async with factory() as session:
            saved = await OrderNotificationRepository(session).advance(
                claim,
                now=datetime.now(UTC),
                cursor=page[-1][0] if page else claim.cursor_token_id,
                complete=not open_order or len(page) < _PAGE_SIZE,
            )
            await session.commit()
        if not saved:
            _logger.warning("notification lease expired for order %s", claim.order_id)
        return saved
    except Exception:  # noqa: BLE001 - a failed push remains queued for retry
        _logger.exception("order notification failed for order %s", claim.order_id)
        async with factory() as session:
            await OrderNotificationRepository(session).retry(claim, now=datetime.now(UTC))
            await session.commit()
        return False


async def send_pending_order_notifications(
    *,
    limit: int = 20,
    push: PushClient | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> int:
    """Send at most one bounded recipient page per claimed order."""
    settings = settings or get_settings()
    owned_clients = build_clients(settings) if push is None else None
    if owned_clients is not None:
        push = owned_clients.push
    assert push is not None
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)

    completed_pages = 0
    try:
        for _ in range(limit):
            async with factory() as session:
                claims = await OrderNotificationRepository(session).claim_pending(
                    now=datetime.now(UTC), limit=1, lease_seconds=_LEASE_SECONDS
                )
                await session.commit()
            if not claims:
                break
            if await _deliver_claim(claims[0], factory=factory, push=push):
                completed_pages += 1
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
    return completed_pages

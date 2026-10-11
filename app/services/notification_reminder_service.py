"""One-off reminder delivery with durable inbox and bounded optional push retries."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_clients
from app.integrations.push.base import PushClient
from app.repositories.notification_reminder_repository import (
    NotificationReminderRepository,
    ReminderClaim,
)

_logger = logging.getLogger(__name__)


async def _deliver_claim(
    claim: ReminderClaim,
    *,
    factory: async_sessionmaker[AsyncSession],
    push: PushClient,
) -> bool:
    """Send one bounded device page and fence success or retry progress."""
    try:
        async with asyncio.timeout(20):
            async with factory() as session:
                page = await NotificationReminderRepository(session).token_page(claim)
            if page:
                await push.send_push(
                    [token for _, token in page],
                    "Occasion reminder",
                    "You have an upcoming saved occasion.",
                    data={
                        "type": "OCCASION_REMINDER",
                        "kind": "OCCASION",
                        "occasion_id": str(claim.occasion_id),
                    },
                )
        async with factory() as session:
            await NotificationReminderRepository(session).finish(
                claim,
                now=datetime.now(UTC),
                cursor=page[-1][0] if page else claim.cursor_token_id,
                complete=page is None or len(page) < 500,
            )
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - retain durable finite retry state
        _logger.warning("occasion reminder delivery failed (%s)", type(exc).__name__)
        async with factory() as session:
            await NotificationReminderRepository(session).finish(
                claim,
                now=datetime.now(UTC),
                cursor=claim.cursor_token_id,
                complete=False,
                failed=True,
            )
            await session.commit()
    return False


async def send_occasion_reminders(
    *,
    limit: int = 20,
    factory: async_sessionmaker[AsyncSession] | None = None,
    push: PushClient | None = None,
    settings: Settings | None = None,
) -> int:
    """Materialize at most 100 dates and process twenty bounded device pages per run."""
    limit = min(max(limit, 1), 20)
    settings = settings or get_settings()
    owned_clients = build_clients(settings) if push is None else None
    if owned_clients is not None:
        push = owned_clients.push
    assert push is not None
    owned_engine = None
    if factory is None:
        owned_engine = build_engine(settings)
        factory = build_session_factory(owned_engine)
    sent = 0
    try:
        async with factory() as session:
            repository = NotificationReminderRepository(session)
            now = datetime.now(UTC)
            await repository.materialize_due(now=now)
            await repository.retire_pending(now=now)
            await session.commit()
        for _ in range(limit):
            async with factory() as session:
                claim = await NotificationReminderRepository(session).claim(now=datetime.now(UTC))
                await session.commit()
            if claim is None:
                break
            if await _deliver_claim(claim, factory=factory, push=push):
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
            if owned_engine is not None:
                await owned_engine.dispose()
    return sent

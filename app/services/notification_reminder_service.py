"""One-off reminder delivery with durable inbox and bounded optional push retries."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_worker_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_push_client
from app.integrations.push.base import PushClient
from app.repositories.notification_reminder_repository import (
    NotificationReminderRepository,
    ReminderClaim,
)

_logger = logging.getLogger(__name__)
_PUSH_TIMEOUT_SECONDS = 20


async def _deliver_claim(
    claim: ReminderClaim,
    *,
    factory: async_sessionmaker[AsyncSession],
    push: PushClient,
) -> bool:
    """Send one bounded device page and fence success or retry progress."""
    try:
        async with asyncio.timeout(_PUSH_TIMEOUT_SECONDS):
            async with factory() as session:
                page = await NotificationReminderRepository(session).token_page(claim)
            if page is None:
                return False
            if page:
                await push.send_push(
                    [token for _, token in page],
                    "Occasion reminder",
                    "You have an upcoming saved occasion.",
                    data={
                        "event_id": str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"giftly:occasion:{claim.occasion_id}:{claim.version}:{claim.scheduled_at.isoformat()}",
                            )
                        ),
                        "event_type": "OCCASION_REMINDER",
                        "reminder_version": str(claim.version),
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
                complete=len(page) < 500,
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
    deadline: float | None = None,
) -> int:
    """Materialize at most 100 dates and process twenty bounded device pages per run."""
    limit = min(max(limit, 1), 20)
    settings = settings or get_worker_settings()
    owned_client = build_push_client(settings) if push is None else None
    if owned_client is not None:
        push = owned_client
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
            await repository.advance_annual(now=now)
            await repository.materialize_due(now=now)
            await repository.retire_pending(now=now)
            await session.commit()
        for _ in range(limit):
            if deadline is not None and (
                asyncio.get_running_loop().time() + _PUSH_TIMEOUT_SECONDS >= deadline
            ):
                break
            async with factory() as session:
                claim = await NotificationReminderRepository(session).claim(now=datetime.now(UTC))
                await session.commit()
            if claim is None:
                break
            if await _deliver_claim(claim, factory=factory, push=push):
                sent += 1
    finally:
        try:
            if owned_client is not None:
                aclose = getattr(owned_client, "aclose", None)
                if aclose is not None:
                    await aclose()
        finally:
            if owned_engine is not None:
                await owned_engine.dispose()
    return sent

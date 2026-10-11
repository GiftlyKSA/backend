"""Durable account-review notifications and non-destructive deadline progression."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.email.base import EmailClient
from app.integrations.factory import build_clients
from app.models.account_change import AccountRequestEmail
from app.repositories.account_change_repository import AccountChangeRepository
from app.workers.broker import broker

_logger = logging.getLogger("app.workers.account_requests")


async def _deliver_email(
    row: AccountRequestEmail, *, email: EmailClient, factory: async_sessionmaker[AsyncSession]
) -> bool:
    """Send outside a transaction and fence the delivery result against its lease."""
    assert row.lease_id is not None
    sent = False
    try:
        await asyncio.wait_for(
            email.send_transactional(
                row.to_email,
                "account-request",
                {"request_id": str(row.request_id), "body": row.body},
            ),
            timeout=20,
        )
        sent = True
    except Exception:  # noqa: BLE001 - durable retries retain failed notifications.
        _logger.warning(
            "Account-request email failed; request=%s attempt=%d", row.request_id, row.attempts
        )
    async with factory() as session:
        await AccountChangeRepository(session).finish_email(
            row.request_id, row.lease_id, now=datetime.now(UTC), sent=sent, attempts=row.attempts
        )
        await session.commit()
    if not sent and row.attempts >= 5:
        _logger.error("Account-request email requires operator action; request=%s", row.request_id)
    return sent


async def process_account_requests(
    *,
    limit: int = 20,
    email: EmailClient | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> int:
    """Advance up to 100 recovery deadlines and send a bounded page of leased emails."""
    settings = settings or get_settings()
    clients = build_clients(settings) if email is None else None
    if clients is not None:
        email = clients.email
    assert email is not None
    engine = None
    if factory is None:
        engine = build_engine(settings)
        factory = build_session_factory(engine)
    sent_count = 0
    try:
        async with factory() as session:
            repo = AccountChangeRepository(session)
            now = datetime.now(UTC)
            await repo.advance_review(now)
            await repo.purge_expired_challenges(now)
            await session.commit()
        for _ in range(min(max(limit, 0), 100)):
            async with factory() as session:
                row = await AccountChangeRepository(session).claim_email(datetime.now(UTC))
                await session.commit()
            if row is None:
                break
            if await _deliver_email(row, email=email, factory=factory):
                sent_count += 1

    finally:
        try:
            if clients is not None:
                for client in (
                    clients.gateway,
                    clients.email,
                    clients.sms,
                    clients.push,
                    clients.storage,
                ):
                    close = getattr(client, "aclose", None)
                    if close is not None:
                        await close()
        finally:
            if engine is not None:
                await engine.dispose()
    return sent_count


@broker.task(schedule=[{"cron": "* * * * *"}])
async def deliver_account_request_emails() -> None:
    """Retry notices and advance expired windows to review, never erase or settle funds."""
    await process_account_requests()

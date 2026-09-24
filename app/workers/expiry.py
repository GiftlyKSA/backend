"""Expiry sweeper for unpaid invoices and stale payment intents (SPEC SECTION 13, 21).

Two kinds of stale gateway state are cleaned up:

* An ISSUED invoice whose payment window lapsed is EXPIRED — its held wallet funds are
  released, its open gateway intent is EXPIRED, its promo reservation is returned, and the
  order reopens to ASSIGNED so the courier can re-issue.
* A NEW wallet-top-up intent past its expiry is simply EXPIRED (no money was held).

Each item settles in its own transaction (one failure never blocks the rest); the
scheduled task holds a Redis lock so two workers never sweep at once. Money moves only
through the ledger; releasing a hold is a reservation change, not a ledger movement.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.core.locks import LockNotAcquiredError, redis_lock
from app.core.redis import build_redis
from app.models.enums import PaymentPurpose
from app.repositories.auth_repository import AuthRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.payment_repository import PaymentRepository
from app.services.expiry_service import ExpiryService
from app.workers.broker import broker

_logger = logging.getLogger("app.workers.expiry")
_LOCK_KEY = "job:expire_stale"
_LOCK_TTL_SECONDS = 300


async def expire_stale(
    *,
    limit: int = 200,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> tuple[int, int]:
    """Expire lapsed invoices and top-up intents; returns (invoices, intents) expired."""
    settings = settings or get_settings()
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)

    now = datetime.now(UTC)
    invoices_expired = 0
    intents_expired = 0
    try:
        async with factory() as session:
            invoice_ids = [
                inv.id
                for inv in await InvoiceRepository(session).list_expired_issued(
                    now=now, limit=limit
                )
            ]
            topup_ids = [
                i.id
                for i in await PaymentRepository(session).list_expired_new(now=now, limit=limit)
                if i.purpose is PaymentPurpose.WALLET_TOPUP
            ]

        for invoice_id in invoice_ids:
            async with factory() as session:
                if await _expire_invoice(session, invoice_id):
                    invoices_expired += 1
        for intent_id in topup_ids:
            async with factory() as session:
                if await _expire_topup_intent(session, intent_id):
                    intents_expired += 1
    finally:
        if own_engine is not None:
            await own_engine.dispose()

    _logger.info(
        "expiry sweep: %d invoice(s), %d top-up intent(s)", invoices_expired, intents_expired
    )
    return invoices_expired, intents_expired


async def _expire_invoice(session: AsyncSession, invoice_id: uuid.UUID) -> bool:
    try:
        expired = await ExpiryService(session).expire_invoice(invoice_id)
        if expired:
            await session.commit()
        return expired
    except Exception:  # noqa: BLE001 - one bad invoice must not stall the sweep
        await session.rollback()
        _logger.exception("failed to expire invoice %s", invoice_id)
        return False


async def _expire_topup_intent(session: AsyncSession, intent_id: uuid.UUID) -> bool:
    try:
        expired = await ExpiryService(session).expire_topup_intent(intent_id)
        if expired:
            await session.commit()
        return expired
    except Exception:  # noqa: BLE001 - one bad intent must not stall the sweep
        await session.rollback()
        _logger.exception("failed to expire intent %s", intent_id)
        return False


@broker.task(schedule=[{"cron": "*/10 * * * *"}])
async def run_expire_stale() -> None:
    """Scheduled task: acquire a lock and expire lapsed invoices/intents."""
    settings = get_settings()
    redis = build_redis(settings)
    try:
        # Lua compare-and-delete release (audit SEC-5): if this run outlives the TTL
        # and a peer re-acquires, releasing must not free the peer's lock.
        async with redis_lock(redis, _LOCK_KEY, ttl_seconds=_LOCK_TTL_SECONDS):
            await expire_stale()
    except LockNotAcquiredError:
        _logger.info("expiry sweep already running elsewhere; skipping")
    finally:
        await redis.aclose()


async def purge_refresh_tokens(
    *, factory: async_sessionmaker[AsyncSession] | None = None, settings: Settings | None = None
) -> int:
    """Delete refresh tokens expired for longer than the retention window (audit PERF-3).

    Rotation inserts a row per refresh and nothing else ever deletes them; without this
    sweep the table grows without bound. Rows are only removed once they have been
    expired for ``REFRESH_TOKEN_RETENTION_DAYS``, so reuse-detection forensics keep a
    full window of history.

    Returns:
        The number of rows deleted.
    """
    settings = settings or get_settings()
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)
    cutoff = datetime.now(UTC) - timedelta(days=settings.REFRESH_TOKEN_RETENTION_DAYS)
    try:
        async with factory() as session:
            deleted = await AuthRepository(session).purge_expired(before=cutoff)
            await session.commit()
        if deleted:
            _logger.info("purged %d expired refresh tokens", deleted)
        return deleted
    finally:
        if own_engine is not None:
            await own_engine.dispose()


@broker.task(schedule=[{"cron": "0 4 * * *"}])
async def run_purge_refresh_tokens() -> None:
    """Nightly task: acquire a lock and purge long-expired refresh tokens."""
    settings = get_settings()
    redis = build_redis(settings)
    try:
        async with redis_lock(redis, "job:purge_refresh_tokens", ttl_seconds=120):
            await purge_refresh_tokens()
    except LockNotAcquiredError:
        _logger.info("refresh-token purge already running elsewhere; skipping")
    finally:
        await redis.aclose()

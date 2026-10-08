"""Recover outstanding hosted payments and confirm closure before local expiry."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.core.locks import LockNotAcquiredError, redis_lock
from app.core.redis import build_redis
from app.integrations.factory import build_payment_client
from app.integrations.payments.base import PaymentClient
from app.models.enums import PaymentIntentStatus
from app.repositories.payment_repository import PaymentRepository
from app.services.expiry_service import ExpiryService
from app.services.payment_service import build_payment_service
from app.workers.broker import broker

_logger = logging.getLogger(__name__)


async def reconcile_gateway_payments(
    *,
    settings: Settings | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    limit: int = 20,
) -> int:
    """Verify a bounded batch; production awaits confirmed vendor authentication."""
    settings = settings or get_settings()
    if settings.is_production or settings.payment_provider != "dhamen":
        return 0
    if not 1 <= limit <= 20:
        raise ValueError("Gateway reconciliation batch size must be from 1 to 20.")
    engine = None
    if factory is None:
        engine = build_engine(settings)
        factory = build_session_factory(engine)
    gateway = build_payment_client(settings)
    redis = build_redis(settings)
    checked = 0
    try:
        async with factory() as session:
            pending = await PaymentRepository(session).list_pending_hosted(
                provider=gateway.provider, limit=limit
            )
            intent_ids = [intent.id for intent in pending]
        for intent_id in intent_ids:
            async with factory() as session:
                try:
                    await _reconcile_one(session, intent_id, settings, gateway, redis)
                    await session.commit()
                    checked += 1
                except Exception as exc:  # noqa: BLE001 - isolated attempts; do not log provider payloads
                    await session.rollback()
                    _logger.warning(
                        "Gateway verification unresolved for intent %s (%s)",
                        intent_id,
                        type(exc).__name__,
                    )
    finally:
        aclose = getattr(gateway, "aclose", None)
        if aclose is not None:
            await aclose()
        await redis.aclose()
        if engine is not None:
            await engine.dispose()
    return checked


async def _reconcile_one(
    session: AsyncSession,
    intent_id: UUID,
    settings: Settings,
    gateway: PaymentClient,
    redis: Redis,
) -> None:
    repository = PaymentRepository(session)
    await repository.resume_actor()
    intent = await repository.get_intent(intent_id)
    if intent is None:
        return
    service = build_payment_service(
        session=session, gateway=gateway, redis=redis, settings=settings
    )
    current = await service.refresh_payment_session(intent_id=intent_id, user_id=intent.user_id)
    if current.status is PaymentIntentStatus.NEW and (
        current.checkout_state == "CLOSING" or current.expires_at <= datetime.now(UTC)
    ):
        current = await service.cancel_payment_session(intent_id=intent_id, user_id=intent.user_id)
    if current.status is PaymentIntentStatus.CANCELLED and current.reference_invoice_id is not None:
        await ExpiryService(session).expire_invoice(current.reference_invoice_id)


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def run_gateway_reconciliation() -> None:
    """Recover checkout outcomes with a bounded deadline and shared scheduler lock."""
    settings = get_settings()
    if settings.is_production or settings.payment_provider != "dhamen":
        return
    redis = build_redis(settings)
    try:
        async with redis_lock(redis, "job:gateway_reconciliation", ttl_seconds=330):
            async with asyncio.timeout(300):
                await reconcile_gateway_payments(settings=settings)
    except LockNotAcquiredError:
        _logger.info("Gateway reconciliation is already running.")
    finally:
        await redis.aclose()

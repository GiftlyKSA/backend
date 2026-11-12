"""Bounded cleanup of upload grants abandoned for at least one day."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Environment, Settings, get_worker_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_storage_client
from app.integrations.storage.base import StorageClient
from app.repositories.media_repository import MediaCleanupReservation, MediaRepository

_logger = logging.getLogger("app.services.media_cleanup")
_RETENTION = timedelta(days=1)
_BATCH_SIZE = 100
_STORAGE_BUDGET_SECONDS = 45
_OBJECT_TIMEOUT_SECONDS = 15


async def _delete_objects(
    storage: StorageClient, reservations: list[MediaCleanupReservation]
) -> list[MediaCleanupReservation]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _STORAGE_BUDGET_SECONDS
    deleted = []
    for index, reservation in enumerate(reservations):
        remaining = deadline - loop.time()
        if remaining <= 0:
            _logger.warning(
                "Media cleanup deferred %d grants at deadline.", len(reservations) - index
            )
            break
        try:
            async with asyncio.timeout(min(_OBJECT_TIMEOUT_SECONDS, remaining)):
                await storage.delete_object(reservation.storage_key)
        except Exception as exc:  # noqa: BLE001 - leave the fenced grant for a later retry
            _logger.warning(
                "Media cleanup failed for grant %s (%s).", reservation.grant_id, type(exc).__name__
            )
        else:
            deleted.append(reservation)
    return deleted


async def clean_abandoned_uploads(
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    storage: StorageClient | None = None,
    settings: Settings | None = None,
) -> int:
    """Fence old grants, delete objects, then remove grant rows; retry failures later."""
    settings = settings or get_worker_settings()
    if settings.storage_provider == "fake" and settings.ENVIRONMENT is not Environment.TEST:
        _logger.info("Private media cleanup skipped: real storage is not configured.")
        return 0
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)
    owned_client = build_storage_client(settings) if storage is None else None
    if owned_client is not None:
        storage = owned_client
    assert storage is not None
    removed = 0
    try:
        async with factory() as session:
            reservations = await MediaRepository(session).reserve_expired(
                before=datetime.now(UTC) - _RETENTION, limit=_BATCH_SIZE
            )
            await session.commit()
        deleted = await _delete_objects(storage, reservations)
        if deleted:
            async with factory() as session:
                removed = await MediaRepository(session).remove_reserved(deleted)
                await session.commit()
    finally:
        try:
            if owned_client is not None:
                aclose = getattr(owned_client, "aclose", None)
                if aclose is not None:
                    await aclose()
        finally:
            if own_engine is not None:
                await own_engine.dispose()
    return removed

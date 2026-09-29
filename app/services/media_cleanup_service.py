"""Bounded cleanup of upload grants abandoned for at least one day."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_clients
from app.integrations.storage.base import StorageClient
from app.repositories.media_repository import MediaRepository

_logger = logging.getLogger("app.services.media_cleanup")
_RETENTION = timedelta(days=1)
_BATCH_SIZE = 100


async def clean_abandoned_uploads(
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    storage: StorageClient | None = None,
    settings: Settings | None = None,
) -> int:
    """Fence old grants, delete objects, then remove grant rows; retry failures later."""
    settings = settings or get_settings()
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)
    owned_clients = build_clients(settings) if storage is None else None
    if owned_clients is not None:
        storage = owned_clients.storage
    assert storage is not None
    removed = 0
    try:
        async with factory() as session:
            keys = await MediaRepository(session).reserve_expired(
                before=datetime.now(UTC) - _RETENTION, limit=_BATCH_SIZE
            )
            await session.commit()
        for key in keys:
            try:
                await storage.delete_object(key)
                async with factory() as session:
                    await MediaRepository(session).remove_reserved(key)
                    await session.commit()
                removed += 1
            except Exception:  # noqa: BLE001 - retry the fenced grant on the next run
                _logger.exception("failed to clean abandoned upload %s", key)
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
    return removed

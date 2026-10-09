"""Bounded retention cleanup for completed retry snapshots."""

import logging
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.core.locks import LockNotAcquiredError, redis_lock
from app.core.redis import build_redis
from app.repositories.operation_repository import OperationRepository
from app.workers.broker import broker


@broker.task(schedule=[{"cron": "15 * * * *"}])
async def purge_write_operations() -> None:
    """Expire completed results without removing unresolved payment protection."""
    settings = get_settings()
    redis = build_redis(settings)
    engine = build_engine(settings)
    try:
        async with redis_lock(redis, "job:write-operations", ttl_seconds=120):
            async with build_session_factory(engine)() as session:
                count = await OperationRepository(session).purge(datetime.now(UTC))
                await session.commit()
                logging.getLogger(__name__).info("Expired %s completed operation records.", count)
    except LockNotAcquiredError:
        return
    finally:
        await redis.aclose()
        await engine.dispose()

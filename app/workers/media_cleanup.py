"""Scheduled abandoned media cleanup."""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.locks import LockNotAcquiredError, redis_lock
from app.core.redis import build_redis
from app.services.media_cleanup_service import clean_abandoned_uploads
from app.workers.audit import audited_system_job
from app.workers.broker import broker

_logger = logging.getLogger("app.workers.media_cleanup")


@broker.task(schedule=[{"cron": "0 * * * *"}])
@audited_system_job("run_media_cleanup")
async def run_media_cleanup() -> None:
    """Run one bounded cleanup batch under a shared lock."""
    settings = get_settings()
    redis = build_redis(settings)
    try:
        async with redis_lock(redis, "job:media_cleanup", ttl_seconds=300):
            await clean_abandoned_uploads(settings=settings)
    except LockNotAcquiredError:
        _logger.info("media cleanup already running elsewhere; skipping")
    finally:
        await redis.aclose()

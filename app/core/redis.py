"""Async Redis client factory (SPEC SECTION 16.7).

Redis backs OTP TTLs, rate limits, distributed locks, and the admin login throttle.
The client is created once from settings and shared; callers never construct their own.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

from app.core.config import Settings

REDIS_CONNECT_TIMEOUT_SECONDS = 3
REDIS_OPERATION_TIMEOUT_SECONDS = 3
REDIS_MAX_CONNECTIONS = 100


def build_redis(settings: Settings) -> Redis:
    """Create an async Redis client from the configured URL."""
    pool = ConnectionPool.from_url(
        settings.REDIS_URL.get_secret_value(),
        encoding="utf-8",
        decode_responses=True,
        max_connections=REDIS_MAX_CONNECTIONS,
    )
    # URL query parameters override factory keywords in redis-py. Enforce the
    # request/job budget even when deployment supplies timeout URL options.
    pool.connection_kwargs.update(
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=REDIS_OPERATION_TIMEOUT_SECONDS,
        retry_on_timeout=False,
    )
    return Redis(connection_pool=pool)

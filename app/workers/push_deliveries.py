"""Schedule recovery of committed per-device pushes outside the public API."""

from app.core.config import get_worker_settings
from app.core.db import build_engine, build_session_factory
from app.core.redis import build_redis
from app.integrations.factory import build_expo_client
from app.services.push_delivery_service import deliver_pushes
from app.workers.broker import broker


@broker.task(schedule=[{"cron": "* * * * *"}])
async def deliver_push_notifications() -> None:
    """Drain bounded due batches; source outboxes never need to resend accepted devices."""
    settings = get_worker_settings()
    if settings.push_provider != "expo":
        return
    engine = build_engine(settings.model_copy(update={"DB_POOL_SIZE": 1, "DB_MAX_OVERFLOW": 0}))
    redis = build_redis(settings)
    client = build_expo_client(settings)
    try:
        await deliver_pushes(build_session_factory(engine), client, redis)
    finally:
        try:
            await client.aclose()
        finally:
            await redis.aclose()
            await engine.dispose()

"""Scheduled bounded recovery of Expo delivery receipts."""

from app.core.config import get_worker_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.factory import build_expo_client
from app.services.expo_receipt_service import ExpoReceiptService
from app.workers.broker import broker
from app.workers.delivery_budget import run_delivery_drainer


@broker.task(schedule=[{"cron": "* * * * *"}])
async def check_expo_receipts() -> None:
    """Check accepted tickets after their durable fifteen-minute waiting period."""
    await run_delivery_drainer("Expo receipts", _drain)


async def _drain(deadline: float) -> None:
    settings = get_worker_settings()
    if settings.push_provider != "expo":
        return
    client = build_expo_client(settings)
    engine = build_engine(settings.model_copy(update={"DB_POOL_SIZE": 1, "DB_MAX_OVERFLOW": 0}))
    service = ExpoReceiptService(build_session_factory(engine), owned_engine=engine)
    try:
        await service.drain(client, deadline=deadline)
    finally:
        try:
            await client.aclose()
        finally:
            await service.aclose()

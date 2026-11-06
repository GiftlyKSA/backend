"""Persist device targets; only the dedicated delivery worker contacts Expo."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.worker_dispatch import WorkerTask, dispatch_worker_wakeups, request_worker_wakeup
from app.integrations.push.base import PushClient
from app.repositories.push_delivery_repository import PushDeliveryRepository
from app.schemas.push_event import PushEvent


class QueuedExpoPushClient(PushClient):
    """Bridge the existing source outboxes to independently retryable devices."""

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, engine: AsyncEngine) -> None:
        """Own one small database pool and no provider HTTP connection."""
        self._factory = factory
        self._engine = engine

    async def aclose(self) -> None:
        """Release the owned pool."""
        await self._engine.dispose()

    async def send_push(
        self, tokens: list[str], title: str, body: str, *, data: dict[str, str] | None = None
    ) -> None:
        """Commit idempotent targets before allowing the source cursor to advance."""
        event = PushEvent.model_validate(data)
        if len(tokens) > 500:
            raise ValueError("Push source pages cannot exceed 500 devices.")
        if not tokens:
            return
        async with self._factory() as session:
            for start in range(0, len(tokens), 100):
                await PushDeliveryRepository(session).enqueue(
                    tokens[start : start + 100], event, now=datetime.now(UTC)
                )
            await session.commit()
            request_worker_wakeup(session, WorkerTask.PUSH_DELIVERIES)
            await dispatch_worker_wakeups(session)

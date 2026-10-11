"""Participant-scoped dedicated order status history."""

from uuid import UUID

from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_models import OrderTimelineEvent, OrderTimelineOrigin


class OrderTimelineRepository:
    """Load status events with deterministic SQL pagination."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the transaction."""
        self._session = session

    async def complete(self, order_id: UUID) -> bool:
        """Historical orders intentionally have no completeness marker."""
        return bool(
            await self._session.scalar(
                select(exists().where(OrderTimelineOrigin.order_id == order_id))
            )
        )

    async def get(self, order_id: UUID, identifier: UUID) -> OrderTimelineEvent | None:
        """Scope every anchor to the authorized order."""
        result: OrderTimelineEvent | None = await self._session.scalar(
            select(OrderTimelineEvent).where(
                OrderTimelineEvent.order_id == order_id, OrderTimelineEvent.id == identifier
            )
        )

        return result

    async def page(
        self, order_id: UUID, *, limit: int, after: OrderTimelineEvent | None
    ) -> list[OrderTimelineEvent]:
        """Return actual events, including reversals and admin corrections."""
        query = select(OrderTimelineEvent).where(OrderTimelineEvent.order_id == order_id)
        if after is not None:
            query = query.where(
                tuple_(OrderTimelineEvent.created_at, OrderTimelineEvent.id)
                > (after.created_at, after.id)
            )
        return list(
            await self._session.scalars(
                query.order_by(OrderTimelineEvent.created_at, OrderTimelineEvent.id).limit(limit)
            )
        )

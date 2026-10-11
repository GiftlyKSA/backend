"""Authorize current participants before projecting dedicated status events."""

from uuid import UUID

from app.core.exceptions import NotFoundError
from app.repositories.order_repository import OrderRepository
from app.repositories.order_timeline_repository import OrderTimelineRepository
from app.schemas.order_timeline import OrderTimelinePage, TimelineActor, TimelineEventResponse
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.notification_cursor import decode_cursor, encode_cursor


class OrderTimelineService:
    """Expose actual statuses without synthesizing historical transitions."""

    def __init__(
        self,
        repository: OrderTimelineRepository,
        orders: OrderRepository,
        eligibility: CourierEligibilityService,
    ) -> None:
        """Bind domain authorization and dedicated event persistence."""
        self._repository = repository
        self._orders = orders
        self._eligibility = eligibility

    async def list(
        self, actor_id: UUID, order_id: UUID, *, limit: int, cursor: str | None
    ) -> OrderTimelinePage:
        """Reject removed and foreign participants before any history read."""
        await self._eligibility.require_marketplace_actor(actor_id)
        if await self._orders.get_for_actor(order_id, actor_id) is None:
            raise NotFoundError("Order not found.")
        after = await self._repository.get(order_id, decode_cursor(cursor)) if cursor else None
        if cursor and after is None:
            raise NotFoundError("Order timeline cursor not found.")
        rows = await self._repository.page(order_id, limit=limit + 1, after=after)
        return OrderTimelinePage(
            items=[
                TimelineEventResponse.model_validate(
                    {
                        "id": row.id,
                        "status": row.status,
                        "created_at": row.created_at,
                        "actor": TimelineActor.model_validate({"role": row.actor_role})
                        if row.actor_role
                        else None,
                    }
                )
                for row in rows[:limit]
            ],
            next_cursor=encode_cursor(rows[limit - 1].id) if len(rows) > limit else None,
            history_complete=await self._repository.complete(order_id),
        )

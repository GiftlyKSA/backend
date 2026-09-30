"""Customer ratings of couriers after completed orders."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Rating
from app.models.enums import OrderStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.rating_repository import RatingRepository
from app.services.courier_eligibility_service import CourierEligibilityService


class RatingService:
    """Creates ratings and reports a user's average score."""

    def __init__(
        self,
        *,
        orders: OrderRepository,
        ratings: RatingRepository,
        eligibility: CourierEligibilityService,
    ) -> None:
        """Wire the order and rating repositories."""
        self._orders = orders
        self._ratings = ratings
        self._eligibility = eligibility

    async def rate(
        self,
        *,
        order_id: uuid.UUID,
        rater_id: uuid.UUID,
        score: int,
        comment: str | None,
    ) -> Rating:
        """Rate the assigned courier of a completed customer order.

        Raises:
            NotFoundError: Not a participant's order.
            ConflictError: The order is not completed, or the rater already rated it.
        """
        await self._eligibility.require_eligible_actor(rater_id)
        order = await self._orders.lock_for_actor(order_id, rater_id)
        if order is None or order.customer_id != rater_id:
            raise NotFoundError("Order not found.")
        if order.status is not OrderStatus.COMPLETED:
            raise ConflictError("You can only rate a completed order.")

        rated_user_id = order.courier_id
        if rated_user_id is None:  # pragma: no cover - a completed order always has a courier
            raise ConflictError("This order has no courier to rate.")
        if await self._ratings.exists_for_rater(order_id, rater_id):
            raise ConflictError("You have already rated this order.")

        return await self._ratings.create(
            order_id=order_id,
            rater_id=rater_id,
            rated_user_id=rated_user_id,
            score=score,
            comment=comment,
        )

    async def summary_for_user(self, user_id: uuid.UUID) -> tuple[Decimal, int]:
        """Return courier ratings received from customers only."""
        return await self._ratings.summary_for_user(user_id)

    async def current_actor_has_rated(self, order_id: uuid.UUID, actor_id: uuid.UUID) -> bool:
        """Return DB-authoritative rating state for an order participant."""
        return await self._ratings.actor_has_rated(order_id, actor_id)

    async def current_actor_rating_states(
        self, order_ids: list[uuid.UUID], actor_id: uuid.UUID
    ) -> dict[uuid.UUID, bool]:
        """Map per-order rating state from one repository query."""
        rated_ids = await self._ratings.rated_order_ids_for_actor(order_ids, actor_id)
        return {order_id: order_id in rated_ids for order_id in order_ids}

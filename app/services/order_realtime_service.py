"""Current authorization and post-commit hints for order subscribers."""

import logging
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.jwt import JwtError, decode_access_token
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.user_repository import UserRepository
from app.schemas.order_events import OrderStatusEvent
from app.services.auth_service import validate_access_claims
from app.services.courier_eligibility_service import CourierEligibilityService

logger = logging.getLogger(__name__)


def order_channel(order_id: UUID) -> str:
    """Return the private Redis hint channel for one order."""
    return f"order:status:{order_id}"


async def publish_order_change(redis: Redis, order_id: UUID) -> None:
    """Publish after commit without misreporting an already successful write."""
    try:
        await redis.publish(order_channel(order_id), "changed")
    except Exception:
        logger.warning("Order saved, but its live update could not be sent", exc_info=True)


class OrderRealtimeService:
    """Revalidate credentials and ownership for each delivered state."""

    def __init__(
        self, settings: Settings, redis: Redis, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Bind shared infrastructure without keeping an open database transaction."""
        self.settings = settings
        self.redis = redis
        self.factory = factory

    async def snapshot(self, order_id: UUID, token: str) -> OrderStatusEvent:
        """Return current state only to a live customer/assigned eligible courier."""
        try:
            claims = decode_access_token(self.settings, token)
        except JwtError as exc:
            raise UnauthorizedError("Invalid or expired token.") from exc
        if claims.role not in (UserRole.CUSTOMER.value, UserRole.COURIER.value):
            raise ForbiddenError()
        async with self.factory() as session:
            users = UserRepository(session)
            await validate_access_claims(claims, redis=self.redis, users=users)
            actor_id = UUID(claims.sub)
            await CourierEligibilityService(
                users=users, couriers=CourierRepository(session)
            ).require_eligible_actor(actor_id)
            order = await OrderRepository(session).get_for_actor(order_id, actor_id)
            if order is None:
                raise NotFoundError("Order not found.")
            return OrderStatusEvent(
                order_id=order.id,
                status=order.status.value,
                courier_id=order.courier_id,
                assigned_at=order.assigned_at,
            )

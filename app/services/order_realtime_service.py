"""Current authorization and post-commit hints for order subscribers."""

import logging
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.jwt import JwtError, decode_access_token
from app.models.enums import UserRole, UserStatus
from app.repositories.order_repository import OrderRepository
from app.schemas.order_events import OrderStatusEvent
from app.services.auth_service import validate_account_claims

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
        if await self.redis.get(f"jwt:denylist:{claims.jti}"):
            raise UnauthorizedError("This session has been revoked.")
        async with self.factory() as session:
            actor_id = UUID(claims.sub)
            state = await OrderRepository(session).get_live_state(order_id, actor_id)
            validate_account_claims(claims, state.user if state else None)
            assert state is not None
            if state.user.role is UserRole.COURIER and (
                state.user.status is not UserStatus.ACTIVE or not state.courier_verified
            ):
                raise ForbiddenError("This courier account is not eligible for this action.")
            if state.order_id is None:
                raise NotFoundError("Order not found.")
            assert state.status is not None
            return OrderStatusEvent(
                order_id=state.order_id,
                status=state.status.value,
                courier_id=state.courier_id,
                assigned_at=state.assigned_at,
            )

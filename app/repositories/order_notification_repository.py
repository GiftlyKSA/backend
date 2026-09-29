"""Durable claims and paged recipients for new-order city pushes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CourierProfile, DeviceToken, Order, OrderNotification, User
from app.models.enums import OrderStatus, UserRole, UserStatus


@dataclass(frozen=True)
class ClaimedNotification:
    """A leased notification whose claim token fences later updates."""

    order_id: uuid.UUID
    city_id: uuid.UUID
    cursor_token_id: uuid.UUID | None
    lease_id: uuid.UUID
    attempts: int


class OrderNotificationRepository:
    """Claim short batches without holding database locks over push delivery."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind a database session."""
        self._session = session

    async def claim_pending(
        self, *, now: datetime, limit: int, lease_seconds: int
    ) -> list[ClaimedNotification]:
        """Atomically lease pending rows; caller commits before network I/O."""
        rows = list(
            await self._session.scalars(
                select(OrderNotification)
                .where(
                    OrderNotification.completed_at.is_(None),
                    OrderNotification.available_at <= now,
                    or_(
                        OrderNotification.leased_until.is_(None),
                        OrderNotification.leased_until <= now,
                    ),
                )
                .order_by(OrderNotification.available_at, OrderNotification.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        claimed = []
        for row in rows:
            lease_id = uuid.uuid4()
            row.lease_id = lease_id
            row.leased_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            row.updated_at = now
            claimed.append(
                ClaimedNotification(
                    order_id=row.order_id,
                    city_id=row.city_id,
                    cursor_token_id=row.cursor_token_id,
                    lease_id=lease_id,
                    attempts=row.attempts,
                )
            )
        await self._session.flush()
        return claimed

    async def order_is_open(self, order_id: uuid.UUID) -> bool:
        """Skip a queued notification if its order was cancelled or removed."""
        status = await self._session.scalar(select(Order.status).where(Order.id == order_id))
        return status is OrderStatus.NEW

    async def token_page(
        self, *, city_id: uuid.UUID, after: uuid.UUID | None, limit: int
    ) -> list[tuple[uuid.UUID, str]]:
        """Page active courier tokens in stable primary-key order."""
        query = (
            select(DeviceToken.id, DeviceToken.token)
            .join(User, User.id == DeviceToken.user_id)
            .join(CourierProfile, CourierProfile.user_id == User.id)
            .where(
                User.role == UserRole.COURIER,
                User.status == UserStatus.ACTIVE,
                CourierProfile.is_verified.is_(True),
                CourierProfile.city_of_residence_id == city_id,
            )
            .order_by(DeviceToken.id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(DeviceToken.id > after)
        return [(row.id, row.token) for row in (await self._session.execute(query)).all()]

    async def advance(
        self,
        claim: ClaimedNotification,
        *,
        now: datetime,
        cursor: uuid.UUID | None,
        complete: bool,
    ) -> bool:
        """Save a sent page only if this worker still owns the lease."""
        result = await self._session.execute(
            update(OrderNotification)
            .where(
                OrderNotification.order_id == claim.order_id,
                OrderNotification.lease_id == claim.lease_id,
                OrderNotification.leased_until > now,
                OrderNotification.completed_at.is_(None),
            )
            .values(
                cursor_token_id=cursor,
                completed_at=now if complete else None,
                available_at=now,
                lease_id=None,
                leased_until=None,
                updated_at=now,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def retry(self, claim: ClaimedNotification, *, now: datetime) -> None:
        """Release a failed claim with bounded exponential backoff."""
        delay = min(2 ** min(claim.attempts, 10), 3600)
        await self._session.execute(
            update(OrderNotification)
            .where(
                OrderNotification.order_id == claim.order_id,
                OrderNotification.lease_id == claim.lease_id,
                OrderNotification.completed_at.is_(None),
            )
            .values(
                available_at=now + timedelta(seconds=delay),
                lease_id=None,
                leased_until=None,
                updated_at=now,
            )
        )

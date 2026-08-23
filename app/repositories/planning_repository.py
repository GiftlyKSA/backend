"""Persistence for featured-gift discovery and customer-owned occasions."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FeaturedGift, Occasion


class PlanningRepository:
    """Reads public gifts and manages occasions with ownership in every query."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def list_active_featured_gifts(self, *, limit: int) -> list[FeaturedGift]:
        """Return active featured gifts in administrator-defined display order."""
        return list(
            await self._session.scalars(
                select(FeaturedGift)
                .where(FeaturedGift.is_active.is_(True))
                .order_by(FeaturedGift.display_order, FeaturedGift.id)
                .limit(limit)
            )
        )

    async def create_occasion(
        self,
        *,
        user_id: uuid.UUID,
        title: str,
        occasion_date: date,
        reminder_days_before: int,
        featured_gift_id: uuid.UUID | None,
    ) -> Occasion:
        """Create an occasion owned by the authenticated user ID supplied by the service."""
        occasion = Occasion(
            user_id=user_id,
            title=title,
            occasion_date=occasion_date,
            reminder_days_before=reminder_days_before,
            featured_gift_id=featured_gift_id,
        )
        self._session.add(occasion)
        await self._session.flush()
        return occasion

    async def get_occasion_for_actor(
        self, occasion_id: uuid.UUID, actor_id: uuid.UUID
    ) -> Occasion | None:
        """Return an occasion only when it belongs to the actor."""
        result: Occasion | None = await self._session.scalar(
            select(Occasion).where(Occasion.id == occasion_id, Occasion.user_id == actor_id)
        )
        return result

    async def list_occasions_for_actor(
        self, actor_id: uuid.UUID, *, limit: int, from_date: date | None = None
    ) -> list[Occasion]:
        """Return only the actor's occasions, soonest first."""
        query = select(Occasion).where(Occasion.user_id == actor_id)
        if from_date is not None:
            query = query.where(Occasion.occasion_date >= from_date)
        return list(
            await self._session.scalars(
                query.order_by(Occasion.occasion_date, Occasion.id).limit(limit)
            )
        )

    async def delete_occasion_for_actor(self, occasion_id: uuid.UUID, actor_id: uuid.UUID) -> bool:
        """Delete an occasion only through an ownership-scoped statement."""
        result = await self._session.execute(
            delete(Occasion)
            .where(Occasion.id == occasion_id, Occasion.user_id == actor_id)
            .returning(Occasion.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

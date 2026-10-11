"""Owned SQL keyset paging and idempotent read operations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_models import InboxNotification


class NotificationInboxRepository:
    """Keep inbox access and mutation scoped to the current owner."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the transaction."""
        self._session = session

    async def get(self, user_id: UUID, identifier: UUID) -> InboxNotification | None:
        """Read an anchor or item only for its owner."""
        result: InboxNotification | None = await self._session.scalar(
            select(InboxNotification).where(
                InboxNotification.user_id == user_id, InboxNotification.id == identifier
            )
        )

        return result

    async def page(
        self, user_id: UUID, *, unread_only: bool, limit: int, after: InboxNotification | None
    ) -> list[InboxNotification]:
        """Filter and bound records before loading them."""
        query = select(InboxNotification).where(InboxNotification.user_id == user_id)
        if unread_only:
            query = query.where(InboxNotification.read_at.is_(None))
        if after is not None:
            query = query.where(
                tuple_(InboxNotification.created_at, InboxNotification.id)
                < (after.created_at, after.id)
            )
        return list(
            await self._session.scalars(
                query.order_by(
                    InboxNotification.created_at.desc(), InboxNotification.id.desc()
                ).limit(limit)
            )
        )

    async def unread_count(self, user_id: UUID) -> int:
        """Count through the owner/unread partial index."""
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(InboxNotification)
                .where(InboxNotification.user_id == user_id, InboxNotification.read_at.is_(None))
            )
            or 0
        )

    async def mark_read(self, user_id: UUID, identifier: UUID) -> InboxNotification | None:
        """Preserve the original timestamp when retrying a read."""
        await self._session.execute(
            update(InboxNotification)
            .where(
                InboxNotification.user_id == user_id,
                InboxNotification.id == identifier,
                InboxNotification.read_at.is_(None),
            )
            .values(read_at=func.clock_timestamp())
        )
        return await self.get(user_id, identifier)

    async def mark_all(self, user_id: UUID) -> tuple[int, datetime]:
        """One SQL update; concurrent arrivals outside the statement snapshot remain unread."""
        cutoff = await self._session.scalar(select(func.clock_timestamp()))
        assert isinstance(cutoff, datetime)
        statement = (
            update(InboxNotification)
            .where(
                InboxNotification.user_id == user_id,
                InboxNotification.read_at.is_(None),
                InboxNotification.created_at <= cutoff,
            )
            .values(read_at=cutoff)
            .returning(InboxNotification.id)
            .cte("marked")
        )
        count = await self._session.scalar(select(func.count()).select_from(statement))
        return int(count or 0), cutoff

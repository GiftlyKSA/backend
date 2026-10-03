"""Ownership rules for saved customer occasions."""

from datetime import date
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.models import Occasion
from app.repositories.planning_repository import PlanningRepository


class OccasionService:
    """Manage saved dates without implying recurrence or reminder delivery."""

    def __init__(self, repository: PlanningRepository) -> None:
        """Bind the ownership-scoped repository."""
        self._repository = repository

    async def create(
        self, actor_id: UUID, *, title: str, occasion_date: date, reminder_days_before: int
    ) -> Occasion:
        """Always derive ownership from the authenticated customer."""
        return await self._repository.create_occasion(
            user_id=actor_id,
            title=title,
            occasion_date=occasion_date,
            reminder_days_before=reminder_days_before,
            featured_gift_id=None,
        )

    async def get(self, actor_id: UUID, occasion_id: UUID, *, lock: bool = False) -> Occasion:
        """Hide missing and other customers' records identically."""
        occasion = await self._repository.get_occasion_for_actor(occasion_id, actor_id, lock=lock)
        if occasion is None:
            raise NotFoundError("Occasion not found.")
        return occasion

    async def list(
        self, actor_id: UUID, *, limit: int, cursor: UUID | None, from_date: date | None
    ) -> tuple[list[Occasion], UUID | None]:
        """Validate ownership of pagination anchors before fetching a bounded page."""
        after = await self.get(actor_id, cursor) if cursor is not None else None
        if after is not None and from_date is not None and after.occasion_date < from_date:
            raise NotFoundError("Occasion cursor not found in this date range.")
        rows = await self._repository.list_occasions_for_actor(
            actor_id, limit=limit + 1, from_date=from_date, after=after
        )
        return rows[:limit], rows[limit - 1].id if len(rows) > limit else None

    async def update(
        self,
        actor_id: UUID,
        occasion_id: UUID,
        *,
        title: str | None,
        occasion_date: date | None,
        reminder_days_before: int | None,
    ) -> Occasion:
        """Lock current ownership before modifying an occasion."""
        occasion = await self.get(actor_id, occasion_id, lock=True)
        return await self._repository.update_occasion(
            occasion,
            title=title,
            occasion_date=occasion_date,
            reminder_days_before=reminder_days_before,
        )

    async def delete(self, actor_id: UUID, occasion_id: UUID) -> None:
        """Delete only the customer's own record."""
        if not await self._repository.delete_occasion_for_actor(occasion_id, actor_id):
            raise NotFoundError("Occasion not found.")

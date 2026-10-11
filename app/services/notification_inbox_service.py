"""Current eligibility and safe projection of actor-owned notification rows."""

from uuid import UUID

from app.core.exceptions import NotFoundError
from app.models.notification_models import InboxNotification
from app.repositories.notification_inbox_repository import NotificationInboxRepository
from app.schemas.notification_inbox import (
    EmptyParameters,
    NotificationPage,
    NotificationResponse,
    OccasionNavigation,
    OrderNavigation,
    OrderParameters,
    ReadAllResponse,
    UnreadCount,
    WalletNavigation,
    WithdrawalParameters,
)
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.notification_cursor import decode_cursor, encode_cursor


def notification_response(row: InboxNotification) -> NotificationResponse:
    """Construct allowlisted localization fields, never stored free-form text."""
    stem = row.notification_type.lower()
    navigation = (
        OrderNavigation(order_id=row.resource_id)
        if row.destination_kind == "ORDER"
        else OccasionNavigation(occasion_id=row.resource_id)
        if row.destination_kind == "OCCASION"
        else WalletNavigation()
    )
    parameters = (
        OrderParameters.model_validate({"status": row.status})
        if row.notification_type == "ORDER_STATUS_CHANGED"
        else WithdrawalParameters.model_validate({"status": row.status})
        if row.notification_type == "WITHDRAWAL_STATUS_CHANGED"
        else EmptyParameters()
    )
    return NotificationResponse.model_validate(
        {
            "id": row.id,
            "type": row.notification_type,
            "title_key": f"notification.{stem}.title",
            "body_key": f"notification.{stem}.body",
            "parameters": parameters,
            "created_at": row.created_at,
            "read_at": row.read_at,
            "navigation": navigation,
        }
    )


class NotificationInboxService:
    """Enforce marketplace eligibility before owned inbox operations."""

    def __init__(
        self, repository: NotificationInboxRepository, eligibility: CourierEligibilityService
    ) -> None:
        """Bind persistence and existing eligibility rules."""
        self._repository = repository
        self._eligibility = eligibility

    async def list(
        self, actor_id: UUID, *, unread_only: bool, limit: int, cursor: str | None
    ) -> NotificationPage:
        """Permit unread anchors to have been read between pages."""
        await self._eligibility.require_marketplace_actor(actor_id)
        after = await self._repository.get(actor_id, decode_cursor(cursor)) if cursor else None
        if cursor and after is None:
            raise NotFoundError("Notification cursor not found.")
        rows = await self._repository.page(
            actor_id, unread_only=unread_only, limit=limit + 1, after=after
        )
        return NotificationPage(
            items=[notification_response(row) for row in rows[:limit]],
            next_cursor=encode_cursor(rows[limit - 1].id) if len(rows) > limit else None,
            unread_count=await self._repository.unread_count(actor_id),
        )

    async def unread_count(self, actor_id: UUID) -> UnreadCount:
        """Require current account eligibility even when there are no notifications."""
        await self._eligibility.require_marketplace_actor(actor_id)
        return UnreadCount(unread_count=await self._repository.unread_count(actor_id))

    async def read(self, actor_id: UUID, identifier: UUID) -> NotificationResponse:
        """Hide foreign identifiers and preserve idempotent read timestamps."""
        await self._eligibility.require_marketplace_actor(actor_id)
        row = await self._repository.mark_read(actor_id, identifier)
        if row is None:
            raise NotFoundError("Notification not found.")
        return notification_response(row)

    async def read_all(self, actor_id: UUID) -> ReadAllResponse:
        """Mark the owned statement snapshot in a single database operation."""
        await self._eligibility.require_marketplace_actor(actor_id)
        count, cutoff = await self._repository.mark_all(actor_id)
        return ReadAllResponse(marked_count=count, read_at=cutoff)

"""Sign bounded order-photo pages only after current participant authorization."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.integrations.storage.base import StorageClient
from app.models.enums import MediaType
from app.repositories.order_repository import OrderRepository
from app.schemas.order_media import MediaPurpose, OrderMediaPage, OrderMediaResponse
from app.services.courier_eligibility_service import CourierEligibilityService

_READ_TTL_SECONDS = 300


class OrderMediaReadService:
    """Expose trusted photo metadata and short-lived signed URLs to participants."""

    def __init__(
        self,
        orders: OrderRepository,
        eligibility: CourierEligibilityService,
        storage: StorageClient,
    ) -> None:
        """Reuse order ownership, account eligibility and storage signing."""
        self._orders = orders
        self._eligibility = eligibility
        self._storage = storage

    async def list(
        self,
        actor_id: UUID,
        order_id: UUID,
        *,
        purpose: MediaPurpose | None,
        limit: int,
        cursor: UUID | None,
    ) -> OrderMediaPage:
        """Reject inaccessible orders even when they have no attached photos."""
        await self._eligibility.require_marketplace_actor(actor_id)
        if await self._orders.get_for_actor(order_id, actor_id) is None:
            raise NotFoundError("Order not found.")
        media_type = (
            MediaType.CUSTOMER_REQUEST
            if purpose == "ORDER_REQUEST"
            else MediaType.DELIVERY_PROOF
            if purpose == "DELIVERY_PROOF"
            else None
        )
        rows = await self._orders.page_media_for_actor(
            order_id,
            actor_id,
            purpose=media_type,
            limit=limit + 1,
            cursor=cursor,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=_READ_TTL_SECONDS)
        items = [
            OrderMediaResponse(
                id=row.id,
                purpose="ORDER_REQUEST"
                if row.media_type is MediaType.CUSTOMER_REQUEST
                else "DELIVERY_PROOF",
                content_type=row.content_type,
                byte_size=row.byte_size,
                created_at=row.created_at,
                access_url=self._storage.signed_read_url(
                    row.storage_key, ttl_seconds=_READ_TTL_SECONDS
                ),
                expires_at=expires_at,
            )
            for row in rows[:limit]
        ]
        return OrderMediaPage(
            items=items,
            next_cursor=str(items[-1].id) if len(rows) > limit else None,
        )

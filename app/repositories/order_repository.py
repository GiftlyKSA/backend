"""Order and order-media persistence (SPEC SECTION 10, 13, 20.C)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import (
    City,
    Conversation,
    CourierProfile,
    Order,
    OrderMedia,
    OrderNotification,
    User,
)
from app.models.enums import MediaType, OrderStatus

# Statuses that count against a customer's concurrent-order limit.
_CUSTOMER_ACTIVE = (
    OrderStatus.NEW,
    OrderStatus.ASSIGNED,
    OrderStatus.WAITING_PAYMENT,
    OrderStatus.IN_PROGRESS,
    OrderStatus.DELIVERED,
    OrderStatus.DISPUTED,
)
# Statuses that count against a courier's concurrent-assignment limit.
_COURIER_ACTIVE = (
    OrderStatus.ASSIGNED,
    OrderStatus.WAITING_PAYMENT,
    OrderStatus.IN_PROGRESS,
)


@dataclass(frozen=True)
class LiveOrderState:
    """Current account authorization and minimal order state from one statement."""

    user: User
    courier_verified: bool | None
    order_id: uuid.UUID | None
    status: OrderStatus | None
    courier_id: uuid.UUID | None
    assigned_at: datetime | None


class OrderRepository:
    """Creates and reads orders, with FOR UPDATE locking for the accept race."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def get_live_state(
        self, order_id: uuid.UUID, actor_id: uuid.UUID
    ) -> LiveOrderState | None:
        """Project ownership and courier verification without loading city relationships."""
        row = (
            await self._session.execute(
                select(
                    User,
                    CourierProfile.is_verified,
                    Order.id,
                    Order.status,
                    Order.courier_id,
                    Order.assigned_at,
                )
                .outerjoin(CourierProfile, CourierProfile.user_id == User.id)
                .outerjoin(
                    Order,
                    (Order.id == order_id)
                    & ((Order.customer_id == actor_id) | (Order.courier_id == actor_id)),
                )
                .where(User.id == actor_id)
            )
        ).one_or_none()
        return LiveOrderState(*row) if row is not None else None

    async def create(
        self,
        *,
        customer_id: uuid.UUID,
        description: str | None,
        delivery_city: City,
        delivery_date: date,
        address_note: str | None,
    ) -> Order:
        """Insert a NEW order for the selected city and date."""
        order = Order(
            customer_id=customer_id,
            description=description,
            city=delivery_city,
            delivery_date=delivery_date,
            delivery_address_note=address_note,
            status=OrderStatus.NEW,
        )
        self._session.add(order)
        await self._session.flush()
        self._session.add(OrderNotification(order_id=order.id, city_id=delivery_city.id))
        await self._session.flush()
        return order

    async def add_media(
        self,
        *,
        order_id: uuid.UUID,
        uploaded_by_user_id: uuid.UUID,
        media_type: MediaType,
        storage_key: str,
        content_type: str,
        byte_size: int,
        captured_at: datetime | None = None,
    ) -> None:
        """Attach a media object (customer request or delivery proof) to an order."""
        self._session.add(
            OrderMedia(
                order_id=order_id,
                uploaded_by_user_id=uploaded_by_user_id,
                media_type=media_type,
                storage_key=storage_key,
                content_type=content_type,
                byte_size=byte_size,
                captured_at=captured_at,
            )
        )
        await self._session.flush()

    async def get(self, order_id: uuid.UUID) -> Order | None:
        """Return an order by id, or None."""
        return await self._session.get(Order, order_id)

    async def get_for_actor(self, order_id: uuid.UUID, actor_id: uuid.UUID) -> Order | None:
        """Return an order only if the actor is its customer or courier (ownership)."""
        result: Order | None = await self._session.scalar(
            select(Order).where(
                Order.id == order_id,
                (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
            )
        )
        return result

    async def lock(self, order_id: uuid.UUID) -> Order | None:
        """Load an order FOR UPDATE (the DB layer of the accept race)."""
        result: Order | None = await self._session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def lock_for_actor(self, order_id: uuid.UUID, actor_id: uuid.UUID) -> Order | None:
        """Lock and refresh current participation before validating a transition."""
        result: Order | None = await self._session.scalar(
            select(Order)
            .where(
                Order.id == order_id,
                (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def lock_actor(self, actor_id: uuid.UUID) -> None:
        """Serialize quota decisions before order locks, without blocking FK references."""
        await self._session.scalar(
            select(User.id).where(User.id == actor_id).with_for_update(key_share=True)
        )

    async def update_admin_details(
        self,
        order: Order,
        *,
        description: str | None,
        delivery_city: City,
        delivery_date: date,
        delivery_address_note: str | None,
    ) -> None:
        """Update non-financial order details for an administrator."""
        order.description = description
        order.city = delivery_city
        order.delivery_date = delivery_date
        order.delivery_address_note = delivery_address_note
        await self._session.flush()

    async def delete(self, order: Order) -> None:
        """Permanently remove a safe-to-delete draft order and dependent request media."""
        await self._session.delete(order)
        await self._session.flush()

    async def count_customer_active(self, customer_id: uuid.UUID) -> int:
        """Count a customer's non-terminal orders (concurrency limit)."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.customer_id == customer_id, Order.status.in_(_CUSTOMER_ACTIVE))
        )
        return int(total or 0)

    async def count_courier_active(self, courier_id: uuid.UUID) -> int:
        """Count a courier's active assignments (concurrency limit)."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.courier_id == courier_id, Order.status.in_(_COURIER_ACTIVE))
        )
        return int(total or 0)

    async def list_for_customer(
        self,
        customer_id: uuid.UUID,
        *,
        status: OrderStatus | None,
        limit: int,
        before_id: uuid.UUID | None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[Order]:
        """Return a customer's orders, newest first, keyset-paged."""
        query = select(Order).where(Order.customer_id == customer_id)
        if status is not None:
            query = query.where(Order.status == status)
        if from_date is not None:
            query = query.where(Order.delivery_date >= from_date)
        if to_date is not None:
            query = query.where(Order.delivery_date <= to_date)
        return await self._page(query, limit, before_id)

    async def list_for_courier(
        self,
        courier_id: uuid.UUID,
        *,
        status: OrderStatus | None,
        limit: int,
        before_id: uuid.UUID | None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[Order]:
        """Return only orders assigned to the courier, including terminal history."""
        query = select(Order).where(Order.courier_id == courier_id)
        if status is not None:
            query = query.where(Order.status == status)
        if from_date is not None:
            query = query.where(Order.delivery_date >= from_date)
        if to_date is not None:
            query = query.where(Order.delivery_date <= to_date)
        return await self._page(query, limit, before_id)

    async def list_order_media_for_actor(
        self, order_id: uuid.UUID, actor_id: uuid.UUID
    ) -> list[OrderMedia]:
        """Return order media only when the actor is its customer or courier."""
        return list(
            await self._session.scalars(
                select(OrderMedia)
                .join(Order, Order.id == OrderMedia.order_id)
                .where(
                    OrderMedia.order_id == order_id,
                    (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
                )
                .order_by(OrderMedia.created_at, OrderMedia.id)
            )
        )

    async def page_media_for_actor(
        self,
        order_id: uuid.UUID,
        actor_id: uuid.UUID,
        *,
        purpose: MediaType | None,
        limit: int,
        cursor: uuid.UUID | None,
    ) -> list[OrderMedia]:
        """Page only request/proof photos for the order's current participants."""
        query = (
            select(OrderMedia)
            .join(Order, Order.id == OrderMedia.order_id)
            .where(
                OrderMedia.order_id == order_id,
                (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
                OrderMedia.media_type.in_((MediaType.CUSTOMER_REQUEST, MediaType.DELIVERY_PROOF)),
            )
        )
        if purpose is not None:
            query = query.where(OrderMedia.media_type == purpose)
        if cursor is not None:
            anchor = await self._session.scalar(query.where(OrderMedia.id == cursor))
            if anchor is None:
                raise NotFoundError("Pagination cursor not found in this list.")
            query = query.where(
                tuple_(OrderMedia.created_at, OrderMedia.id)
                > (
                    anchor.created_at,
                    anchor.id,
                )
            )
        return list(
            await self._session.scalars(
                query.order_by(OrderMedia.created_at, OrderMedia.id).limit(limit)
            )
        )

    async def list_available(
        self, city_id: uuid.UUID, *, limit: int, before_id: uuid.UUID | None
    ) -> list[Order]:
        """Return NEW orders in a city (the courier radar), newest first, keyset-paged."""
        query = select(Order).where(
            Order.delivery_city_id == city_id, Order.status == OrderStatus.NEW
        )
        return await self._page(query, limit, before_id)

    async def _page(
        self, query: Select[tuple[Order]], limit: int, before_id: uuid.UUID | None
    ) -> list[Order]:
        if before_id is not None:
            anchor = await self._session.scalar(query.where(Order.id == before_id))
            if anchor is None:
                raise NotFoundError("Pagination cursor not found in this list.")
            query = query.where(tuple_(Order.created_at, Order.id) < (anchor.created_at, anchor.id))
        query = query.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit)
        return list(await self._session.scalars(query))

    async def create_conversation(
        self, *, order_id: uuid.UUID, customer_id: uuid.UUID, courier_id: uuid.UUID
    ) -> Conversation:
        """Create the order's conversation (unique per order) on assignment."""
        conversation = Conversation(
            order_id=order_id, customer_id=customer_id, courier_id=courier_id
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def flush(self) -> None:
        """Flush pending writes."""
        await self._session.flush()

    async def lock_overdue_unaccepted(self, *, before: date, limit: int) -> list[Order]:
        """Claim a bounded batch without blocking concurrent acceptance or sweeps."""
        return list(
            await self._session.scalars(
                select(Order)
                .where(
                    Order.status == OrderStatus.NEW,
                    Order.courier_id.is_(None),
                    Order.delivery_date < before,
                )
                .order_by(Order.delivery_date, Order.id)
                .limit(limit)
                .with_for_update(skip_locked=True, of=Order)
                .execution_options(populate_existing=True)
            )
        )

    async def list_auto_approve_due(self, cutoff: datetime, limit: int) -> list[Order]:
        """Return DELIVERED orders whose delivered_at is at or before ``cutoff``."""
        return list(
            await self._session.scalars(
                select(Order)
                .where(Order.status == OrderStatus.DELIVERED, Order.delivered_at <= cutoff)
                .order_by(Order.delivered_at)
                .limit(limit)
            )
        )

    @staticmethod
    def now() -> datetime:
        """Return the current UTC time (single source for assigned/cancelled stamps)."""
        return datetime.now(UTC)

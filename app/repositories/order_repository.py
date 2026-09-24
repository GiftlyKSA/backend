"""Order and order-media persistence (SPEC SECTION 10, 13, 20.C).

Spatial writes put longitude FIRST in ``ST_MakePoint`` — reversing it puts Jeddah in
Antarctica and every geofence check silently fails. Money-free ownership is enforced
in the query (customer or courier), never fetch-then-compare.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from geoalchemy2 import Geography
from redis.asyncio import Redis
from sqlalchemy import Select, cast, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Order, OrderMedia, User
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


class OrderRepository:
    """Creates and reads orders, with FOR UPDATE locking for the accept race."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def create(
        self,
        *,
        customer_id: uuid.UUID,
        description: str | None,
        delivery_city: str,
        longitude: float,
        latitude: float,
        delivery_date: date,
        address_note: str | None,
    ) -> Order:
        """Insert a NEW order; the point is built lng-first (ST_MakePoint(x, y))."""
        order = Order(
            customer_id=customer_id,
            description=description,
            delivery_city=delivery_city,
            delivery_location=func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326),
            delivery_date=delivery_date,
            delivery_address_note=address_note,
            status=OrderStatus.NEW,
        )
        self._session.add(order)
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
        capture_longitude: float | None = None,
        capture_latitude: float | None = None,
        captured_at: datetime | None = None,
    ) -> None:
        """Attach a media object (customer request or delivery proof) to an order.

        A DELIVERY_PROOF must carry the courier's capture location (the DB CHECK
        ``chk_proof_has_location``); the point is built longitude-FIRST.
        """
        location = None
        if capture_longitude is not None and capture_latitude is not None:
            location = func.ST_SetSRID(func.ST_MakePoint(capture_longitude, capture_latitude), 4326)
        self._session.add(
            OrderMedia(
                order_id=order_id,
                uploaded_by_user_id=uploaded_by_user_id,
                media_type=media_type,
                storage_key=storage_key,
                content_type=content_type,
                byte_size=byte_size,
                capture_location=location,
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
        delivery_city: str,
        delivery_date: date,
        delivery_address_note: str | None,
    ) -> None:
        """Update non-financial, non-location order details for an administrator."""
        order.description = description
        order.delivery_city = delivery_city
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
    ) -> list[Order]:
        """Return a customer's orders, newest first, keyset-paged."""
        query = select(Order).where(Order.customer_id == customer_id)
        if status is not None:
            query = query.where(Order.status == status)
        return await self._page(query, limit, before_id)

    async def list_for_courier(
        self,
        courier_id: uuid.UUID,
        *,
        status: OrderStatus | None,
        limit: int,
        before_id: uuid.UUID | None,
    ) -> list[Order]:
        """Return only orders assigned to the courier, including terminal history."""
        query = select(Order).where(Order.courier_id == courier_id)
        if status is not None:
            query = query.where(Order.status == status)
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

    async def list_available(
        self, city: str, *, limit: int, before_id: uuid.UUID | None
    ) -> list[Order]:
        """Return NEW orders in a city (the courier radar), newest first, keyset-paged."""
        query = select(Order).where(Order.delivery_city == city, Order.status == OrderStatus.NEW)
        return await self._page(query, limit, before_id)

    async def _page(
        self, query: Select[tuple[Order]], limit: int, before_id: uuid.UUID | None
    ) -> list[Order]:
        query = query.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit)
        if before_id is not None:
            anchor = await self._session.get(Order, before_id)
            if anchor is not None:
                query = query.where(
                    tuple_(Order.created_at, Order.id) < (anchor.created_at, anchor.id)
                )
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

    async def coords_for_actor(
        self, order_id: uuid.UUID, actor_id: uuid.UUID
    ) -> tuple[float, float] | None:
        """Return coordinates only when SQL proves the actor participates."""
        row = (
            await self._session.execute(
                select(
                    func.ST_X(Order.delivery_location), func.ST_Y(Order.delivery_location)
                ).where(
                    Order.id == order_id,
                    (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
                )
            )
        ).first()
        return (float(row[0]), float(row[1])) if row is not None else None

    async def flush(self) -> None:
        """Flush pending writes."""
        await self._session.flush()

    async def distance_to_delivery(
        self, order_id: uuid.UUID, *, longitude: float, latitude: float
    ) -> float | None:
        """Return metres between (lng, lat) and the order's drop-off, or None.

        Both points are cast to ``geography`` so ``ST_Distance`` returns metres — on plain
        ``geometry`` it returns degrees, which would make every geofence check meaningless.
        The point is built longitude-FIRST.
        """
        point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
        result = await self._session.scalar(
            select(
                func.ST_Distance(cast(Order.delivery_location, Geography), cast(point, Geography))
            ).where(Order.id == order_id)
        )
        return float(result) if result is not None else None

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


@dataclass(frozen=True, slots=True)
class CourierLocation:
    """One sanitized, ephemeral courier location sample."""

    order_id: uuid.UUID
    courier_id: uuid.UUID
    latitude: float
    longitude: float
    accuracy: float | None
    received_at: datetime


class CourierLocationRepository:
    """Stores only the latest courier location in Redis with a short TTL."""

    def __init__(self, redis: Redis) -> None:
        """Bind the repository to the shared Redis client."""
        self._redis = redis

    async def save(self, location: CourierLocation, *, ttl_seconds: int = 60) -> None:
        """Replace the latest sample and publish the same sanitized payload."""
        payload = asdict(location)
        payload["order_id"] = str(location.order_id)
        payload["courier_id"] = str(location.courier_id)
        payload["received_at"] = location.received_at.isoformat()
        encoded = json.dumps(payload, separators=(",", ":"))
        await self._redis.set(
            self._key(location.order_id, location.courier_id), encoded, ex=ttl_seconds
        )
        await self._redis.publish(self.channel(location.order_id), encoded)

    async def get(self, order_id: uuid.UUID, courier_id: uuid.UUID) -> CourierLocation | None:
        """Return the unexpired latest sample for an order/courier pair."""
        raw: bytes | str | None = await self._redis.get(self._key(order_id, courier_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        return CourierLocation(
            order_id=uuid.UUID(payload["order_id"]),
            courier_id=uuid.UUID(payload["courier_id"]),
            latitude=float(payload["latitude"]),
            longitude=float(payload["longitude"]),
            accuracy=float(payload["accuracy"]) if payload["accuracy"] is not None else None,
            received_at=datetime.fromisoformat(payload["received_at"]),
        )

    async def delete(self, order_id: uuid.UUID, courier_id: uuid.UUID) -> None:
        """Remove the ephemeral sample when tracking is no longer allowed."""
        await self._redis.delete(self._key(order_id, courier_id))

    @staticmethod
    def channel(order_id: uuid.UUID) -> str:
        """Return the private fan-out channel for one order."""
        return f"orders:{order_id}:location"

    @staticmethod
    def _key(order_id: uuid.UUID, courier_id: uuid.UUID) -> str:
        return f"orders:{order_id}:couriers:{courier_id}:location"

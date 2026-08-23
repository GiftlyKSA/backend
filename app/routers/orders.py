"""Order routes (SPEC SECTION 19, 20.C).

Role and state authority are dependencies, not if-statements. The actor id comes from
the JWT. A courier sees the exact delivery point only after the order is assigned.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_redis, get_settings, require_role
from app.core.money import money_str
from app.models import Dispute, Order
from app.models.enums import OrderStatus, UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.device_token_repository import DeviceTokenRepository
from app.repositories.dispute_repository import DisputeRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.message_repository import MessageWriter
from app.repositories.order_repository import OrderRepository
from app.repositories.rating_repository import RatingRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.schemas.fulfillment import (
    DeliverRequest,
    DisputeRequest,
    DisputeResponse,
)
from app.schemas.orders import (
    CancelOrderRequest,
    CreateOrderRequest,
    OrderDetail,
    OrderListResponse,
    OrderSummary,
)
from app.services.fulfillment_service import DeliveryInput, FulfillmentService
from app.services.media_service import MediaService
from app.services.money_service import MoneyService
from app.services.notification_service import NotificationService
from app.services.order_service import NewOrderInput, OrderService
from app.services.rating_service import RatingService

router = APIRouter(prefix="/api/orders", tags=["orders"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
_Customer = require_role(UserRole.CUSTOMER)
_CourierRole = require_role(UserRole.COURIER)
_Participant = require_role(UserRole.CUSTOMER, UserRole.COURIER)


def _service(request: Request, db: AsyncSession) -> OrderService:
    return OrderService(
        session=db,
        orders=OrderRepository(db),
        users=UserRepository(db),
        couriers=CourierRepository(db),
        media=MediaService(request.app.state.clients.storage, get_settings(request)),
        messages=MessageWriter(db),
        ratings=RatingService(orders=OrderRepository(db), ratings=RatingRepository(db)),
        redis=get_redis(request),
        settings=get_settings(request),
    )


def _fulfillment(request: Request, db: AsyncSession) -> FulfillmentService:
    return FulfillmentService(
        orders=OrderRepository(db),
        invoices=InvoiceRepository(db),
        disputes=DisputeRepository(db),
        wallets=WalletRepository(db),
        money=MoneyService(WalletRepository(db)),
        media=MediaService(request.app.state.clients.storage, get_settings(request)),
        settings=get_settings(request),
    )


def _notifier(request: Request, db: AsyncSession) -> NotificationService:
    return NotificationService(
        devices=DeviceTokenRepository(db), push=request.app.state.clients.push
    )


def _dispute(dispute: Dispute) -> DisputeResponse:
    return DisputeResponse(
        id=str(dispute.id),
        order_id=str(dispute.order_id),
        status=str(dispute.status),
        reason=dispute.reason,
        resolution_note=dispute.resolution_note,
    )


async def _summary(service: OrderService, order: Order, actor: Actor) -> OrderSummary:
    return OrderSummary(
        id=str(order.id),
        status=str(order.status),
        delivery_city=order.delivery_city,
        delivery_date=order.delivery_date.isoformat(),
        description=order.description,
        created_at=order.created_at.isoformat(),
        current_actor_has_rated=await service.current_actor_has_rated(
            order_id=order.id, actor_id=actor.id
        ),
    )


async def _active_courier(
    request: Request,
    db: DbDep,
    actor: Annotated[Actor, Depends(_CourierRole)],
) -> Actor:
    await _service(request, db).require_active_verified_courier(actor.id)
    return actor


@router.post("", response_model=OrderDetail, status_code=201)
async def create_order(
    request: Request,
    db: DbDep,
    body: CreateOrderRequest,
    actor: Annotated[Actor, Depends(_Customer)],
) -> OrderDetail:
    """Create a NEW gift-request order."""
    service = _service(request, db)
    order = await service.create_order(
        customer_id=actor.id,
        data=NewOrderInput(
            description=body.description,
            delivery_city=body.delivery_city,
            latitude=body.latitude,
            longitude=body.longitude,
            delivery_date=body.delivery_date,
            request_media_keys=body.request_media_keys,
        ),
    )
    # Best-effort radar ping to couriers in the city (SPEC SECTION 13; deferred from P6).
    await _notifier(request, db).notify_city_couriers(
        city=order.delivery_city,
        title="New gift request nearby",
        body="A customer just posted a new order in your city.",
    )
    return await _detail(service, order, actor)


@router.get("", response_model=OrderListResponse)
async def list_orders(
    request: Request,
    db: DbDep,
    actor: Annotated[Actor, Depends(_Participant)],
    status: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderListResponse:
    """List customer-owned or courier-assigned orders, newest first."""
    status_enum = OrderStatus(status) if status else None
    service = _service(request, db)
    rows = await service.list_for_actor(
        actor_id=actor.id,
        role=actor.role,
        status=status_enum,
        limit=limit,
        before_id=uuid.UUID(cursor) if cursor else None,
    )
    return await _page(service, rows, limit, actor)


@router.get("/available", response_model=OrderListResponse)
async def available_orders(
    request: Request,
    db: DbDep,
    actor: Annotated[Actor, Depends(_active_courier)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderListResponse:
    """List NEW orders in the courier's city (the radar). No exact coordinates."""
    service = _service(request, db)
    rows = await service.list_available_for_courier(
        courier_id=actor.id,
        limit=limit,
        before_id=uuid.UUID(cursor) if cursor else None,
    )
    return await _page(service, rows, limit, actor)


@router.get("/{order_id}", response_model=OrderDetail)
async def get_order(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    actor: Annotated[Actor, Depends(require_role(UserRole.CUSTOMER, UserRole.COURIER))],
) -> OrderDetail:
    """Return an order the caller participates in."""
    service = _service(request, db)
    order = await service.get_order_for_actor(order_id=order_id, actor_id=actor.id)
    return await _detail(service, order, actor)


@router.post("/{order_id}/accept", response_model=OrderDetail)
async def accept_order(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_active_courier)],
) -> OrderDetail:
    """Accept a NEW order (Redis lock + FOR UPDATE race)."""
    service = _service(request, db)
    order = await service.accept_order(order_id=order_id, courier_id=actor.id)
    return await _detail(service, order, actor)


@router.post("/{order_id}/cancel", response_model=OrderDetail)
async def cancel_order(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    body: CancelOrderRequest,
    actor: Annotated[Actor, Depends(require_role(UserRole.CUSTOMER, UserRole.COURIER))],
) -> OrderDetail:
    """Cancel an order before it is in progress."""
    service = _service(request, db)
    order = await service.cancel_order(order_id=order_id, actor_id=actor.id, reason=body.reason)
    return await _detail(service, order, actor)


@router.post("/{order_id}/deliver", response_model=OrderDetail)
async def deliver_order(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    body: DeliverRequest,
    actor: Annotated[Actor, Depends(_active_courier)],
) -> OrderDetail:
    """Mark an in-progress order delivered with geofenced proof (assigned courier)."""
    order = await _fulfillment(request, db).submit_delivery(
        order_id=order_id,
        courier_id=actor.id,
        data=DeliveryInput(
            latitude=body.latitude,
            longitude=body.longitude,
            proof_media_keys=body.proof_media_keys,
            note=body.note,
        ),
    )
    return await _detail(_service(request, db), order, actor)


@router.post("/{order_id}/approve", response_model=OrderDetail)
async def approve_order(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Customer)],
) -> OrderDetail:
    """Approve a delivered order: complete it and release escrow (customer)."""
    order = await _fulfillment(request, db).approve_order(order_id=order_id, customer_id=actor.id)
    return await _detail(_service(request, db), order, actor)


@router.post("/{order_id}/dispute", response_model=DisputeResponse, status_code=201)
async def dispute_order(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    body: DisputeRequest,
    actor: Annotated[Actor, Depends(_Participant)],
) -> DisputeResponse:
    """Open a dispute on an order, freezing escrow (either participant)."""
    dispute = await _fulfillment(request, db).raise_dispute(
        order_id=order_id, actor_id=actor.id, reason=body.reason
    )
    return _dispute(dispute)


async def _page(
    service: OrderService, rows: list[Order], limit: int, actor: Actor
) -> OrderListResponse:
    items = [await _summary(service, order, actor) for order in rows]
    next_cursor = str(rows[-1].id) if len(rows) == limit else None
    return OrderListResponse(items=items, next_cursor=next_cursor)


async def _detail(service: OrderService, order: Order, actor: Actor) -> OrderDetail:
    # A courier sees the exact point only after assignment (SPEC SECTION 17.3).
    show_coords = actor.role is UserRole.CUSTOMER or order.status is not OrderStatus.NEW
    lat = lng = None
    if show_coords:
        coords = await service.coordinates_for_actor(order_id=order.id, actor_id=actor.id)
        if coords is not None:
            lng, lat = coords
    return OrderDetail(
        id=str(order.id),
        status=str(order.status),
        customer_id=str(order.customer_id),
        courier_id=str(order.courier_id) if order.courier_id else None,
        delivery_city=order.delivery_city,
        delivery_date=order.delivery_date.isoformat(),
        description=order.description,
        latitude=lat,
        longitude=lng,
        total_amount=money_str(order.total_amount),
        assigned_at=order.assigned_at.isoformat() if order.assigned_at else None,
        created_at=order.created_at.isoformat(),
        current_actor_has_rated=await service.current_actor_has_rated(
            order_id=order.id, actor_id=actor.id
        ),
    )

"""Private order-photo reads for authorized mobile participants."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_role
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.user_repository import UserRepository
from app.schemas.order_media import MediaPurpose, OrderMediaPage
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.order_media_read_service import OrderMediaReadService

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("/{order_id}/media", response_model=OrderMediaPage)
async def list_order_media(
    request: Request,
    response: Response,
    order_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[Actor, Depends(require_role(UserRole.CUSTOMER, UserRole.COURIER))],
    purpose: Annotated[MediaPurpose | None, Query()] = None,
    cursor: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> OrderMediaPage:
    """Return confirmed order photos with five-minute signed access URLs."""
    response.headers["Cache-Control"] = "private, no-store"
    return await OrderMediaReadService(
        OrderRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
        request.app.state.clients.storage,
    ).list(actor.id, order_id, purpose=purpose, limit=limit, cursor=cursor)

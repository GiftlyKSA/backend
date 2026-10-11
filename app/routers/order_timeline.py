"""Current-participant order history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_role
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.order_timeline_repository import OrderTimelineRepository
from app.repositories.user_repository import UserRepository
from app.schemas.order_timeline import OrderTimelinePage
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.order_timeline_service import OrderTimelineService

DbDep = Annotated[AsyncSession, Depends(get_db)]
ActorDep = Annotated[Actor, Depends(require_role(UserRole.CUSTOMER, UserRole.COURIER))]

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("/{order_id}/timeline", response_model=OrderTimelinePage)
async def order_timeline(
    order_id: UUID,
    response: Response,
    db: DbDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=22)] = None,
) -> OrderTimelinePage:
    """Read real statuses oldest first, explicitly identifying incomplete historical data."""
    response.headers["Cache-Control"] = "private, no-store"
    return await OrderTimelineService(
        OrderTimelineRepository(db),
        OrderRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
    ).list(actor.id, order_id, limit=limit, cursor=cursor)

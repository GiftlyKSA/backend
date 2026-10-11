"""Private owned mobile notification inbox."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_role
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.notification_inbox_repository import NotificationInboxRepository
from app.repositories.user_repository import UserRepository
from app.schemas.notification_inbox import (
    NotificationPage,
    NotificationResponse,
    ReadAllResponse,
    UnreadCount,
)
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.notification_inbox_service import NotificationInboxService

DbDep = Annotated[AsyncSession, Depends(get_db)]
ActorDep = Annotated[Actor, Depends(require_role(UserRole.CUSTOMER, UserRole.COURIER))]

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _service(db: AsyncSession) -> NotificationInboxService:
    return NotificationInboxService(
        NotificationInboxRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
    )


@router.get("", response_model=NotificationPage)
async def list_notifications(
    response: Response,
    db: DbDep,
    actor: ActorDep,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=22)] = None,
) -> NotificationPage:
    """Read owned neutral events newest first; reauthorize destinations when opening."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).list(actor.id, unread_only=unread_only, limit=limit, cursor=cursor)


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(response: Response, db: DbDep, actor: ActorDep) -> UnreadCount:
    """Read the current database-backed owned unread count."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).unread_count(actor.id)


@router.post("/read-all", response_model=ReadAllResponse)
async def read_all(response: Response, db: DbDep, actor: ActorDep) -> ReadAllResponse:
    """Mark the owned unread snapshot; later arrivals remain unread."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).read_all(actor.id)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def read_notification(
    notification_id: UUID, response: Response, db: DbDep, actor: ActorDep
) -> NotificationResponse:
    """Idempotently mark one owned event as read."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).read(actor.id, notification_id)

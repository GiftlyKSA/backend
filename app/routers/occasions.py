"""Authenticated customer calendar CRUD."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_role
from app.models.enums import UserRole
from app.repositories.planning_repository import PlanningRepository
from app.schemas.date_range import DateRange, date_range
from app.schemas.occasions import (
    CreateOccasionRequest,
    OccasionPage,
    OccasionResponse,
    UpdateOccasionRequest,
)
from app.services.occasion_service import OccasionService

router = APIRouter(prefix="/api/occasions", tags=["occasions"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
CustomerDep = Annotated[Actor, Depends(require_role(UserRole.CUSTOMER))]


def _service(db: AsyncSession) -> OccasionService:
    return OccasionService(PlanningRepository(db))


@router.post("", response_model=OccasionResponse, status_code=201)
async def create_occasion(
    body: CreateOccasionRequest, db: DbDep, actor: CustomerDep
) -> OccasionResponse:
    """Save a special date; automatic reminders and annual recurrence are not available."""
    occasion = await _service(db).create(actor.id, **body.model_dump())
    return OccasionResponse.model_validate(occasion)


@router.get("", response_model=OccasionPage)
async def list_occasions(
    db: DbDep,
    actor: CustomerDep,
    dates: Annotated[DateRange, Depends(date_range)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: UUID | None = None,
) -> OccasionPage:
    """List owned occasions by date then ID, within optional inclusive Gregorian bounds.

    Use YYYY-MM-DD. Reset the cursor when bounds change and retain identical filters
    on subsequent pages. Malformed or reversed ranges return HTTP 422.
    """
    rows, next_cursor = await _service(db).list(
        actor.id, limit=limit, cursor=cursor, from_date=dates.from_date, to_date=dates.to_date
    )
    return OccasionPage(
        items=[OccasionResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/{occasion_id}", response_model=OccasionResponse)
async def get_occasion(occasion_id: UUID, db: DbDep, actor: CustomerDep) -> OccasionResponse:
    """Read a saved date belonging to this customer."""
    return OccasionResponse.model_validate(await _service(db).get(actor.id, occasion_id))


@router.patch("/{occasion_id}", response_model=OccasionResponse)
async def update_occasion(
    occasion_id: UUID, body: UpdateOccasionRequest, db: DbDep, actor: CustomerDep
) -> OccasionResponse:
    """Update supplied fields on a customer-owned date."""
    occasion = await _service(db).update(actor.id, occasion_id, **body.model_dump())
    return OccasionResponse.model_validate(occasion)


@router.delete("/{occasion_id}", status_code=204)
async def delete_occasion(occasion_id: UUID, db: DbDep, actor: CustomerDep) -> Response:
    """Delete a customer-owned date; return no response body."""
    await _service(db).delete(actor.id, occasion_id)
    return Response(status_code=204)

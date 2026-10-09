"""Authenticated customer calendar CRUD."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_settings, require_role
from app.models.enums import UserRole
from app.repositories.operation_repository import OperationRepository
from app.repositories.planning_repository import PlanningRepository
from app.schemas.date_range import DateRange, date_range
from app.schemas.occasions import (
    CreateOccasionRequest,
    OccasionPage,
    OccasionResponse,
    UpdateOccasionRequest,
)
from app.services.occasion_service import OccasionService
from app.services.operation_service import OperationService

router = APIRouter(prefix="/api/occasions", tags=["occasions"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
CustomerDep = Annotated[Actor, Depends(require_role(UserRole.CUSTOMER))]


def _service(db: AsyncSession) -> OccasionService:
    return OccasionService(PlanningRepository(db))


@router.post("", response_model=OccasionResponse, status_code=201)
async def create_occasion(
    body: CreateOccasionRequest,
    db: DbDep,
    actor: CustomerDep,
    request: Request,
    idempotency_key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
) -> OccasionResponse:
    """Save a special date; automatic reminders and annual recurrence are not available."""
    operations = OperationService(OperationRepository(db), get_settings(request))
    operation = None
    if idempotency_key is not None:
        payload = TypeAdapter(dict[str, JsonValue]).validate_python(body.model_dump(mode="json"))
        operation = await operations.begin(actor.id, "occasion.create", idempotency_key, payload)
        if operation.result_encrypted is not None:
            return OccasionResponse.model_validate_json(operations.result(operation))
    occasion = await _service(db).create(actor.id, **body.model_dump())
    response = OccasionResponse.model_validate(occasion)
    if operation is not None:
        await operations.finish(operation, response, occasion.id)
    return response


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
    occasion_id: UUID,
    body: UpdateOccasionRequest,
    db: DbDep,
    actor: CustomerDep,
    request: Request,
    idempotency_key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
) -> OccasionResponse:
    """Update supplied fields on a customer-owned date."""
    operations = OperationService(OperationRepository(db), get_settings(request))
    operation = None
    if idempotency_key is not None:
        payload = TypeAdapter(dict[str, JsonValue]).validate_python(
            {
                "occasion_id": str(occasion_id),
                "body": body.model_dump(mode="json", exclude_unset=True),
            }
        )
        operation = await operations.begin(actor.id, "occasion.update", idempotency_key, payload)
        if operation.result_encrypted is not None:
            return OccasionResponse.model_validate_json(operations.result(operation))
    occasion = await _service(db).update(actor.id, occasion_id, **body.model_dump())
    response = OccasionResponse.model_validate(occasion)
    if operation is not None:
        await operations.finish(operation, response, occasion.id)
    return response


@router.delete("/{occasion_id}", status_code=204)
async def delete_occasion(occasion_id: UUID, db: DbDep, actor: CustomerDep) -> Response:
    """Delete a customer-owned date; return no response body."""
    await _service(db).delete(actor.id, occasion_id)
    return Response(status_code=204)

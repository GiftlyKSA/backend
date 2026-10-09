"""Private, authorized recovery for existing client-identified writes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_settings, require_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.operation_repository import OperationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.operations import OperationName, OperationResponse
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.operation_service import OperationService

router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.get("/{operation_key}", response_model=OperationResponse)
async def get_operation(
    operation_key: UUID,
    operation: OperationName,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[Actor, Depends(require_role(UserRole.CUSTOMER, UserRole.COURIER))],
) -> OperationResponse:
    """Recover a retained owned result; absence does not prove a request never executed."""
    response.headers["Cache-Control"] = "private, no-store"
    if actor.role is UserRole.COURIER and operation != "wallet.topup":
        raise ForbiddenError()
    await CourierEligibilityService(
        users=UserRepository(db), couriers=CourierRepository(db)
    ).require_eligible_actor(actor.id)
    service = OperationService(OperationRepository(db), get_settings(request))
    row = await service.find(actor.id, operation, operation_key)
    if row is None:
        raise NotFoundError(
            "No retained committed result. This does not prove the write never ran."
        )
    result = (
        TypeAdapter(dict[str, JsonValue]).validate_json(service.result(row))
        if row.result_encrypted
        else None
    )
    return OperationResponse(
        operation_key=operation_key,
        operation=operation,
        status="COMPLETED" if result is not None else "OUTCOME_UNKNOWN",
        resource_id=row.resource_id,
        result=result,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )

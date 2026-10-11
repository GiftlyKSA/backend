"""Authenticated courier proceeds and target settings routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_role
from app.models.enums import UserRole
from app.repositories.courier_report_repository import CourierReportRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository
from app.schemas.courier_report_schemas import (
    EarningsReport,
    Granularity,
    TargetSettings,
    TargetUpdate,
)
from app.schemas.date_range import CalendarDate
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.courier_report_service import CourierReportService

router = APIRouter(prefix="/api/couriers/me", tags=["courier reports"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
CourierActor = Annotated[Actor, Depends(require_role(UserRole.COURIER))]


def _service(db: AsyncSession) -> CourierReportService:
    return CourierReportService(
        CourierReportRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
    )


@router.get("/earnings", response_model=EarningsReport)
async def earnings(
    response: Response,
    db: DbDep,
    actor: CourierActor,
    from_date: Annotated[CalendarDate, Query()],
    to_date: Annotated[CalendarDate, Query()],
    granularity: Granularity = "day",
) -> EarningsReport:
    """Report signed settled payout including reimbursement, never wallet balance."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).earnings(actor.id, from_date, to_date, granularity)


@router.get("/report-settings", response_model=TargetSettings)
async def report_settings(response: Response, db: DbDep, actor: CourierActor) -> TargetSettings:
    """Read optimistic target settings and full current-period progress."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).settings(actor.id)


@router.patch("/report-settings", response_model=TargetSettings)
async def update_report_settings(
    response: Response, db: DbDep, actor: CourierActor, body: TargetUpdate
) -> TargetSettings:
    """Replace target; stale versions fail with409 CONFLICT."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _service(db).update_settings(actor.id, body)

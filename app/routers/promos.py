"""Public promo routes.

Customers can browse current promos and preview a code against their own order. The
customer invoice-promo endpoint reserves usages; only confirmed settlement consumes them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_settings, require_role
from app.core.money import money_str
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.promo_repository import PromoRepository
from app.repositories.user_repository import UserRepository
from app.schemas.invoices import PromoPreviewResponse, PromoValidateRequest
from app.schemas.promos import PromoCatalogItem, PromoCatalogResponse
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.invoice_service import InvoiceService
from app.services.payment_reservation_service import build_payment_reservation_service
from app.services.promo_service import PromoService

router = APIRouter(prefix="/api/promos", tags=["promos"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
_Customer = require_role(UserRole.CUSTOMER)


def _service(request: Request, db: AsyncSession) -> InvoiceService:
    return InvoiceService(
        invoices=InvoiceRepository(db),
        reservations=build_payment_reservation_service(db),
        orders=OrderRepository(db),
        promos=PromoService(PromoRepository(db)),
        eligibility=CourierEligibilityService(
            users=UserRepository(db), couriers=CourierRepository(db)
        ),
        settings=get_settings(request),
    )


@router.post("/validate", response_model=PromoPreviewResponse)
async def validate_promo(
    request: Request,
    db: DbDep,
    body: PromoValidateRequest,
    actor: Annotated[Actor, Depends(_Customer)],
) -> PromoPreviewResponse:
    """Preview a promo against the customer's own order's active invoice."""
    preview = await _service(request, db).preview_promo(
        order_id=body.order_id, code=body.code, customer_id=actor.id
    )
    return PromoPreviewResponse(
        code=preview.code,
        discount_amount=money_str(preview.discount_amount),
        original_total_amount=money_str(preview.original_total_amount),
        total_amount=money_str(preview.total_amount),
    )


@router.get("", response_model=PromoCatalogResponse)
async def list_available_promos(
    db: DbDep,
    response: Response,
    _actor: Annotated[Actor, Depends(_Customer)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PromoCatalogResponse:
    """List currently active promos for customers, newest first."""
    response.headers["Cache-Control"] = "private, no-store"
    promos = await PromoRepository(db).list_available(now=datetime.now(UTC), limit=limit)
    return PromoCatalogResponse(
        items=[
            PromoCatalogItem(
                code=promo.code,
                description=promo.description,
                discount_type=promo.discount_type,
                percent_value=(
                    f"{promo.percent_value:.2f}" if promo.percent_value is not None else None
                ),
                fixed_amount=(
                    money_str(promo.fixed_amount) if promo.fixed_amount is not None else None
                ),
                max_discount_amount=(
                    money_str(promo.max_discount_amount)
                    if promo.max_discount_amount is not None
                    else None
                ),
                min_order_amount=money_str(promo.min_order_amount),
                starts_at=promo.starts_at,
                ends_at=promo.ends_at,
                created_at=promo.created_at,
            )
            for promo in promos
        ]
    )

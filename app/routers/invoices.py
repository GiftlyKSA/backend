"""Invoice routes (SPEC SECTION 11, 14).

The courier authors an invoice; the platform prices it. Reads are open to the order's
participants (customer or courier) and 404 to anyone else — no existence leak. The actor
id always comes from the JWT, never the body.
"""

from __future__ import annotations

import uuid
from asyncio import to_thread
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi import Response as HttpResponse
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_redis, get_settings, require_role
from app.core.money import money_str, parse_money
from app.models import Invoice, InvoiceItem
from app.models.enums import InvoiceStatus, UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.invoice_promo_repository import InvoicePromoRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.promo_repository import PromoRepository
from app.repositories.user_repository import UserRepository
from app.schemas.date_range import DateRange, date_range
from app.schemas.invoice_list import InvoicePage
from app.schemas.invoices import (
    ApplyInvoicePromoRequest,
    CreateInvoiceRequest,
    InvoiceItemResponse,
    InvoiceResponse,
)
from app.schemas.payments import PayInvoiceRequest, PayInvoiceResponse
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.invoice_pdf import render_invoice_pdf
from app.services.invoice_promo_service import InvoicePromoService
from app.services.invoice_service import InvoiceLineInput, InvoiceService, NewInvoiceInput
from app.services.mobile_invoice_service import MobileInvoiceService
from app.services.payment_reservation_service import build_payment_reservation_service
from app.services.payment_service import build_payment_service
from app.services.promo_service import PromoService

router = APIRouter(prefix="/api", tags=["invoices"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
_Courier = require_role(UserRole.COURIER)
_Customer = require_role(UserRole.CUSTOMER)
_Participant = require_role(UserRole.CUSTOMER, UserRole.COURIER)


@router.get("/invoices", response_model=InvoicePage)
async def list_invoices(
    response: HttpResponse,
    db: DbDep,
    actor: Annotated[Actor, Depends(_Participant)],
    dates: Annotated[DateRange, Depends(date_range)],
    status: Annotated[InvoiceStatus | None, Query()] = None,
    include_historical: Annotated[bool, Query()] = False,
    cursor: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> InvoicePage:
    """List owned invoice revisions; issued-date bounds use Asia/Riyadh days."""
    response.headers["Cache-Control"] = "private, no-store"
    service = MobileInvoiceService(
        InvoiceRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
    )
    return await service.list(
        actor.id,
        role=actor.role,
        limit=limit,
        cursor=cursor,
        status=status,
        include_historical=include_historical,
        from_date=dates.from_date,
        to_date=dates.to_date,
    )


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
        close_payment=build_payment_service(
            session=db,
            gateway=request.app.state.clients.gateway,
            redis=get_redis(request),
            settings=get_settings(request),
        ).close_invoice_payment,
    )


def _item(item: InvoiceItem) -> InvoiceItemResponse:
    return InvoiceItemResponse(
        position=item.position,
        title=item.title,
        description=item.description,
        unit_price_amount=money_str(item.unit_price_amount),
        quantity=item.quantity,
        line_net_amount=money_str(item.line_net_amount),
        line_discount_amount=money_str(item.line_discount_amount),
        line_total_amount=money_str(item.line_total_amount),
    )


def _detail(invoice: Invoice, items: list[InvoiceItem]) -> InvoiceResponse:
    return InvoiceResponse(
        id=str(invoice.id),
        order_id=str(invoice.order_id),
        status=str(invoice.status),
        currency=invoice.currency,
        items_net_amount=money_str(invoice.items_net_amount),
        courier_fee_amount=money_str(invoice.courier_fee_amount),
        service_fee_amount=money_str(invoice.service_fee_amount),
        discount_amount=money_str(invoice.discount_amount),
        net_after_discount_amount=money_str(invoice.net_after_discount_amount),
        total_amount=money_str(invoice.total_amount),
        promo_code=invoice.promo_code_snapshot,
        issued_at=invoice.issued_at.isoformat() if invoice.issued_at else None,
        expires_at=invoice.expires_at.isoformat() if invoice.expires_at else None,
        items=[_item(i) for i in items],
    )


@router.post("/orders/{order_id}/invoices", response_model=InvoiceResponse, status_code=201)
async def create_invoice(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    body: CreateInvoiceRequest,
    actor: Annotated[Actor, Depends(_Courier)],
) -> InvoiceResponse:
    """Author and issue an invoice for an order (assigned courier only)."""
    data = NewInvoiceInput(
        items=[
            InvoiceLineInput(
                title=line.title,
                unit_price_amount=parse_money(line.unit_price_amount),
                quantity=line.quantity,
                description=line.description,
            )
            for line in body.items
        ],
        courier_fee_amount=parse_money(body.courier_fee_amount),
        promo_code=body.promo_code,
    )
    invoice = await _service(request, db).create_invoice(
        order_id=order_id, courier_id=actor.id, data=data
    )
    items = await InvoiceRepository(db).list_items(invoice.id)
    return _detail(invoice, items)


@router.get("/orders/{order_id}/invoice", response_model=InvoiceResponse)
async def get_order_invoice(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
) -> InvoiceResponse:
    """Return an order's active invoice (participant only)."""
    invoice, items = await _service(request, db).get_active_invoice_for_order(
        order_id=order_id, actor_id=actor.id
    )
    return _detail(invoice, items)


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    request: Request,
    db: DbDep,
    invoice_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
) -> InvoiceResponse:
    """Return an invoice by id (participant only)."""
    invoice, items = await _service(request, db).get_invoice_for_actor(
        invoice_id=invoice_id, actor_id=actor.id
    )
    return _detail(invoice, items)


@router.get(
    "/invoices/{invoice_id}/pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
async def download_invoice_pdf(
    request: Request,
    db: DbDep,
    invoice_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Participant)],
) -> Response:
    """Download a stored invoice as English PDF, scoped to order participants."""
    invoice, items = await _service(request, db).get_invoice_for_actor(
        invoice_id=invoice_id, actor_id=actor.id
    )
    cache = getattr(request.app.state, "invoice_pdf_cache", None)
    document = (
        await cache.render(invoice, items)
        if cache is not None
        else await to_thread(render_invoice_pdf, invoice, items)
    )
    return Response(
        document,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="giftly-invoice-{invoice.id}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/invoices/{invoice_id}/pay", response_model=PayInvoiceResponse)
async def pay_invoice(
    request: Request,
    db: DbDep,
    invoice_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Customer)],
    body: PayInvoiceRequest | None = None,
) -> PayInvoiceResponse:
    """Pay an issued invoice from wallet, gateway, or a split of both (customer only)."""
    service = build_payment_service(
        session=db,
        gateway=request.app.state.clients.gateway,
        redis=get_redis(request),
        settings=get_settings(request),
    )
    result = await service.pay_invoice(
        invoice_id=invoice_id,
        customer_id=actor.id,
        use_wallet=body.use_wallet if body is not None else True,
    )
    return PayInvoiceResponse(
        invoice_id=str(result.invoice_id),
        status=result.status,
        amount_from_wallet=money_str(result.amount_from_wallet),
        amount_from_gateway=money_str(result.amount_from_gateway),
        payment_url=result.payment_url,
        payment_intent_id=str(result.intent_id) if result.intent_id else None,
        session_reused=result.session_reused,
        use_wallet=result.use_wallet,
    )


@router.post("/invoices/{invoice_id}/promo", response_model=InvoiceResponse)
async def apply_invoice_promo(
    db: DbDep,
    invoice_id: uuid.UUID,
    body: ApplyInvoicePromoRequest,
    actor: Annotated[Actor, Depends(_Customer)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> InvoiceResponse:
    """Apply/remove a promo by replacing an unpaid invoice; pay the returned invoice ID."""
    service = InvoicePromoService(
        invoices=InvoiceRepository(db),
        orders=OrderRepository(db),
        payments=PaymentRepository(db),
        promos=PromoService(PromoRepository(db)),
        operations=InvoicePromoRepository(db),
        eligibility=CourierEligibilityService(
            users=UserRepository(db), couriers=CourierRepository(db)
        ),
    )
    invoice, items = await service.apply(
        invoice_id=invoice_id, customer_id=actor.id, code=body.code, key=idempotency_key
    )
    return _detail(invoice, items)


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceResponse)
async def cancel_invoice(
    request: Request,
    db: DbDep,
    invoice_id: uuid.UUID,
    actor: Annotated[Actor, Depends(_Courier)],
) -> InvoiceResponse:
    """Cancel an unpaid issued invoice, reopening the order (issuing courier only)."""
    invoice = await _service(request, db).cancel_invoice(invoice_id=invoice_id, courier_id=actor.id)
    items = await InvoiceRepository(db).list_items(invoice.id)
    return _detail(invoice, items)

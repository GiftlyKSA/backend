"""Wallet routes.

Reads are scoped to the authenticated user's own wallet â€” ownership is enforced by
filtering on the actor id from the JWT, never a path or body value.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_redis, get_settings, require_role
from app.core.exceptions import NotFoundError
from app.core.money import money_str, parse_money
from app.models.enums import UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.operation_repository import OperationRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.schemas.date_range import CalendarDate, DateRange, date_range
from app.schemas.payments import TopupRequest, TopupResponse
from app.schemas.wallet_statement import WalletStatement
from app.schemas.wallets import (
    TransactionPage,
    TransactionResponse,
    WalletResponse,
)
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.operation_service import OperationService
from app.services.payment_service import build_payment_service
from app.services.reporting_dates import reporting_bounds
from app.services.wallet_statement_pdf import WalletStatementPdfService
from app.services.wallet_statement_service import WalletStatementService

router = APIRouter(prefix="/api/wallets", tags=["wallets"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
_CustomerOrCourier = require_role(UserRole.CUSTOMER, UserRole.COURIER)
_Customer = require_role(UserRole.CUSTOMER)


async def _eligible_customer_or_courier(
    db: DbDep,
    actor: Annotated[Actor, Depends(_CustomerOrCourier)],
) -> Actor:
    await CourierEligibilityService(
        users=UserRepository(db), couriers=CourierRepository(db)
    ).require_eligible_actor(actor.id)
    return actor


async def _eligible_customer(
    db: DbDep,
    actor: Annotated[Actor, Depends(_Customer)],
) -> Actor:
    eligibility = CourierEligibilityService(
        users=UserRepository(db), couriers=CourierRepository(db)
    )
    await eligibility.require_customer(actor.id)
    return actor


@router.get("/me", response_model=WalletResponse)
async def get_my_wallet(
    db: DbDep, actor: Annotated[Actor, Depends(_eligible_customer)]
) -> WalletResponse:
    """Return the authenticated customer's wallet snapshot."""
    wallet = await WalletRepository(db).get_by_user(actor.id)
    if wallet is None:
        raise NotFoundError("Wallet not found.")
    return WalletResponse(
        balance=money_str(wallet.balance),
        held_balance=money_str(wallet.held_balance),
        available=money_str(wallet.balance - wallet.held_balance),
        currency=wallet.currency,
    )


@router.post("/topup", response_model=TopupResponse, status_code=201)
async def start_topup(
    request: Request,
    db: DbDep,
    body: TopupRequest,
    actor: Annotated[Actor, Depends(_eligible_customer)],
    idempotency_key: Annotated[uuid.UUID | None, Header(alias="Idempotency-Key")] = None,
) -> TopupResponse:
    """Start a wallet top-up and return the gateway payment URL."""
    service = build_payment_service(
        session=db,
        gateway=request.app.state.clients.gateway,
        redis=get_redis(request),
        settings=get_settings(request),
    )
    operations = OperationService(OperationRepository(db), get_settings(request))
    operation = None
    if idempotency_key is not None:
        operation = await operations.begin(
            actor.id,
            "wallet.topup",
            idempotency_key,
            {"amount": money_str(parse_money(body.amount))},
        )
        if operation.result_encrypted is not None:
            return TopupResponse.model_validate_json(operations.result(operation))

    async def bind_intent(intent_id: uuid.UUID) -> None:
        if operation is not None:
            await operations.bind(operation, intent_id)

    result = await service.create_topup(
        user_id=actor.id,
        amount=parse_money(body.amount),
        on_intent=bind_intent if operation is not None else None,
    )
    response = TopupResponse(
        payment_intent_id=str(result.intent_id),
        amount=money_str(result.amount),
        payment_url=result.payment_url,
        status=result.status,
        session_reused=result.session_reused,
    )
    if operation is not None:
        await operations.finish(operation, response, result.intent_id)
    return response


@router.get("/me/transactions", response_model=TransactionPage)
async def list_my_transactions(
    db: DbDep,
    actor: Annotated[Actor, Depends(_eligible_customer_or_courier)],
    dates: Annotated[DateRange, Depends(date_range)],
    cursor: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TransactionPage:
    """Return the authenticated user's ledger entries, newest first (keyset paged)."""
    repo = WalletRepository(db)
    wallet = await repo.get_by_user(actor.id)
    if wallet is None:
        raise NotFoundError("Wallet not found.")
    before = cursor
    start, end = reporting_bounds(dates.from_date, dates.to_date)
    if start is None and end is None:
        rows = await repo.list_transactions(wallet.id, limit=limit, before_id=before)
    else:
        rows = await repo.list_transactions(
            wallet.id, limit=limit, before_id=before, start=start, end=end
        )
    items = [
        TransactionResponse(
            id=str(t.id),
            amount=money_str(t.amount),
            type=str(t.type),
            status=str(t.status),
            balance_after=money_str(t.balance_after),
            created_at=t.created_at.isoformat(),
            description=t.description,
            order_id=str(t.reference_order_id) if t.reference_order_id else None,
            invoice_id=str(t.reference_invoice_id) if t.reference_invoice_id else None,
            payment_intent_id=str(t.reference_intent_id) if t.reference_intent_id else None,
        )
        for t in rows
    ]
    next_cursor = str(rows[-1].id) if len(rows) == limit else None
    return TransactionPage(items=items, next_cursor=next_cursor)


@router.get("/me/statement", response_model=WalletStatement)
async def get_wallet_statement(
    response: Response,
    db: DbDep,
    actor: Annotated[Actor, Depends(_CustomerOrCourier)],
    from_date: Annotated[CalendarDate, Query(description="Inclusive Asia/Riyadh start day.")],
    to_date: Annotated[CalendarDate, Query(description="Inclusive Asia/Riyadh end day.")],
    cursor: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> WalletStatement:
    """Return owned current totals and transactions for at most 366 Riyadh days."""
    response.headers["Cache-Control"] = "private, no-store"
    return await WalletStatementService(
        WalletRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
    ).read(actor.id, from_date=from_date, to_date=to_date, limit=limit, cursor=cursor)


@router.get(
    "/me/statement/pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
async def download_wallet_statement(
    db: DbDep,
    actor: Annotated[Actor, Depends(_CustomerOrCourier)],
    from_date: Annotated[CalendarDate, Query(description="Inclusive Asia/Riyadh start day.")],
    to_date: Annotated[CalendarDate, Query(description="Inclusive Asia/Riyadh end day.")],
) -> Response:
    """Download a complete informational snapshot of at most 366 days and 5000 entries.

    Owner-only PDF with literal descriptions and private, no-store headers. English headings,
    Arabic shaping; unsupported glyphs use Unicode escapes. Complete export limits: 250 pages,
    1,000,000 description characters, 8 MiB and two concurrent renders per API process.
    Oversized ranges/text return 422; busy or expired rendering returns 503.
    """
    service = WalletStatementService(
        WalletRepository(db),
        CourierEligibilityService(users=UserRepository(db), couriers=CourierRepository(db)),
    )
    payload = await WalletStatementPdfService(service).export(
        actor.id,
        from_date=from_date,
        to_date=to_date,
        release_reads=db.commit,
    )
    return Response(
        payload,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                f'attachment; filename="giftly-statement-{from_date}-{to_date}.pdf"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )

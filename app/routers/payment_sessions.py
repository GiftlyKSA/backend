"""Owned hosted-checkout reads, reconciliation and confirmed cancellation."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, get_redis, get_settings, require_role
from app.core.money import money_str
from app.integrations.payments.base import PaymentContext
from app.models import PaymentIntent
from app.models.enums import PaymentIntentStatus, PaymentPurpose, UserRole
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository
from app.schemas.payments import PaymentSessionResponse
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.payment_service import PaymentService, build_payment_service


def _private_response(response: Response) -> None:
    """Keep owned payment state out of browser and shared response caches."""
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/api/payment-sessions", tags=["payments"], dependencies=[Depends(_private_response)]
)
order_router = APIRouter(
    prefix="/api/orders", tags=["payments"], dependencies=[Depends(_private_response)]
)
wallet_router = APIRouter(
    prefix="/api/wallets", tags=["payments"], dependencies=[Depends(_private_response)]
)
DbDep = Annotated[AsyncSession, Depends(get_db)]
_CustomerOrCourier = require_role(UserRole.CUSTOMER, UserRole.COURIER)


async def _eligible_actor(db: DbDep, actor: Annotated[Actor, Depends(_CustomerOrCourier)]) -> Actor:
    await CourierEligibilityService(
        users=UserRepository(db), couriers=CourierRepository(db)
    ).require_eligible_actor(actor.id)
    return actor


ActorDep = Annotated[Actor, Depends(_eligible_actor)]


def _service(request: Request, db: AsyncSession) -> PaymentService:
    return build_payment_service(
        session=db,
        gateway=request.app.state.clients.gateway,
        redis=get_redis(request),
        settings=get_settings(request),
    )


def _response(intent: PaymentIntent) -> PaymentSessionResponse:
    context = (
        PaymentContext.model_validate(intent.checkout_snapshot)
        if intent.checkout_snapshot
        else None
    )

    active = (
        intent.status is PaymentIntentStatus.NEW
        and intent.checkout_state == "ACTIVE"
        and intent.expires_at > datetime.now(UTC)
    )
    purpose = str(getattr(intent, "purpose", PaymentPurpose.ORDER_INVOICE))
    topup = purpose == PaymentPurpose.WALLET_TOPUP
    return PaymentSessionResponse(
        payment_intent_id=str(intent.id),
        provider=intent.checkout_provider,
        status="PENDING" if intent.status is PaymentIntentStatus.NEW else str(intent.status),
        checkout_state=intent.checkout_state,
        order_id=str(intent.order_id) if intent.order_id else None,
        invoice_id=str(intent.reference_invoice_id) if intent.reference_invoice_id else None,
        currency=intent.currency,
        amount_from_wallet=money_str(intent.wallet_reserved_amount),
        amount_from_gateway=money_str(intent.amount),
        expires_at=intent.expires_at.isoformat(),
        payment_url=intent.gateway_payment_url if active else None,
        invoice=context.invoice if context else None,
        purpose=purpose,
        title=context.title if context else "Top up" if topup else "Invoice payment",
        description=context.description if context else None,
        use_wallet=context.use_wallet
        if context
        else bool(getattr(intent, "use_wallet", not topup)),
    )


@order_router.get("/{order_id}/payment-session", response_model=PaymentSessionResponse)
async def get_order_payment_session(
    request: Request,
    db: DbDep,
    order_id: UUID,
    actor: ActorDep,
) -> PaymentSessionResponse:
    """Recover the payer's order checkout after a timeout or application restart."""
    return _response(
        await _service(request, db).get_order_payment_session(order_id=order_id, user_id=actor.id)
    )


@wallet_router.get("/me/topup-session", response_model=PaymentSessionResponse)
async def get_topup_payment_session(
    request: Request,
    db: DbDep,
    actor: ActorDep,
) -> PaymentSessionResponse:
    """Recover the payer's hosted top-up after a timeout or restart."""
    return _response(await _service(request, db).get_topup_payment_session(user_id=actor.id))


@router.get("/{intent_id}", response_model=PaymentSessionResponse)
async def get_payment_session(
    request: Request, db: DbDep, intent_id: UUID, actor: ActorDep
) -> PaymentSessionResponse:
    """Read the payer's session; use refresh to query authoritative provider status."""
    return _response(
        await _service(request, db).get_payment_session(intent_id=intent_id, user_id=actor.id)
    )


@router.post("/{intent_id}/refresh", response_model=PaymentSessionResponse)
async def refresh_payment_session(
    request: Request, db: DbDep, intent_id: UUID, actor: ActorDep
) -> PaymentSessionResponse:
    """Verify payment with the provider, then return the committed settlement state."""
    return _response(
        await _service(request, db).refresh_payment_session(intent_id=intent_id, user_id=actor.id)
    )


@router.post("/{intent_id}/cancel", response_model=PaymentSessionResponse)
async def cancel_payment_session(
    request: Request, db: DbDep, intent_id: UUID, actor: ActorDep
) -> PaymentSessionResponse:
    """Close an unpaid session; a payment already completed settles instead."""
    return _response(
        await _service(request, db).cancel_payment_session(intent_id=intent_id, user_id=actor.id)
    )

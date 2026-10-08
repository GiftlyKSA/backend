from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, PaymentSessionPendingError
from app.models.enums import InvoiceStatus, OrderStatus, PaymentIntentStatus
from app.schemas.payments import PayInvoiceRequest
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from pydantic import ValidationError

from tests.conftest import make_test_settings


def payment_service() -> PaymentService:
    gateway = Mock(uses_hosted_sessions=True, provider="DHAMEN")
    return PaymentService(
        **{
            key: AsyncMock()
            for key in (
                "payments",
                "invoices",
                "orders",
                "wallets",
                "money",
                "promos",
                "users",
            )
        },
        gateway=gateway,
        redis=AsyncMock(),
        settings=make_test_settings(),
    )


@pytest.mark.parametrize("use_wallet,expected_wallet", [(False, "0.00"), (True, "25.00")])
async def test_wallet_toggle_only_backend_calculates_split(use_wallet, expected_wallet) -> None:
    service = payment_service()
    customer_id, order_id = uuid4(), uuid4()
    invoice = SimpleNamespace(
        id=uuid4(),
        order_id=order_id,
        status=InvoiceStatus.ISSUED,
        total_amount=Decimal("100.00"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service._invoices.get_for_actor.return_value = invoice
    service._invoices.lock.return_value = invoice
    service._orders.lock.return_value = SimpleNamespace(
        id=order_id, customer_id=customer_id, status=OrderStatus.WAITING_PAYMENT
    )
    service._payments.get_open_intent_for_order.return_value = None
    service._money.available_balance.return_value = Decimal("25.00")
    service._start_invoice_gateway_payment = AsyncMock()
    await service.pay_invoice(invoice_id=invoice.id, customer_id=customer_id, use_wallet=use_wallet)
    call = service._start_invoice_gateway_payment.call_args.kwargs
    assert call["wallet_amount"] == Decimal(expected_wallet)
    assert call["gateway_amount"] == Decimal("100.00") - Decimal(expected_wallet)
    if not use_wallet:
        service._money.available_balance.assert_not_awaited()


def test_pay_request_rejects_amount_and_non_boolean_toggle() -> None:
    assert PayInvoiceRequest().use_wallet is True
    for body in ({"use_wallet": "false"}, {"amount": "1.00"}):
        with pytest.raises(ValidationError):
            PayInvoiceRequest.model_validate(body)


async def test_same_topup_reuses_existing_session_without_another_pending_credit() -> None:
    service = payment_service()
    intent = SimpleNamespace(
        id=uuid4(),
        amount=Decimal("100.00"),
        status=PaymentIntentStatus.NEW,
        gateway_payment_url="https://pay.example.com/topup",
    )
    service._payments.get_topup_for_actor.return_value = intent
    service._hosted.reuse_or_close = AsyncMock(return_value=intent)
    result = await service.create_topup(user_id=uuid4(), amount=Decimal("100.00"))
    assert result.intent_id == intent.id
    assert result.session_reused is True
    service._payments.create_intent.assert_not_awaited()
    service._money.stage_topup.assert_not_awaited()


async def test_changed_topup_amount_requires_explicit_cancellation() -> None:
    service = payment_service()
    service._payments.get_topup_for_actor.return_value = SimpleNamespace(amount=Decimal("100.00"))
    with pytest.raises(ConflictError):
        await service.create_topup(user_id=uuid4(), amount=Decimal("200.00"))
    service._payments.create_intent.assert_not_awaited()


@pytest.mark.parametrize("unknown", [False, True])
async def test_courier_invoice_cancel_closes_payment_before_invoice_mutation(unknown) -> None:
    invoice = SimpleNamespace(id=uuid4(), order_id=uuid4(), status=InvoiceStatus.ISSUED)
    invoices, orders, close = AsyncMock(), AsyncMock(), AsyncMock()
    invoices.lock_for_courier.return_value = invoice
    orders.lock.return_value = SimpleNamespace(status=OrderStatus.WAITING_PAYMENT)
    service = InvoiceService(
        invoices=invoices,
        orders=orders,
        reservations=AsyncMock(),
        promos=AsyncMock(),
        eligibility=AsyncMock(),
        settings=make_test_settings(),
        close_payment=close,
    )
    if unknown:
        close.side_effect = PaymentSessionPendingError()
        with pytest.raises(PaymentSessionPendingError):
            await service.cancel_invoice(invoice_id=invoice.id, courier_id=uuid4())
        assert invoice.status is InvoiceStatus.ISSUED
        service._promos.release.assert_not_awaited()
        service._reservations.expire_for_invoice.assert_not_awaited()
    else:
        await service.cancel_invoice(invoice_id=invoice.id, courier_id=uuid4())
        close.assert_awaited_once_with(invoice_id=invoice.id)
        assert invoice.status is InvoiceStatus.CANCELLED


async def test_payment_winning_courier_cancel_race_is_committed_and_not_cancelled() -> None:
    service = payment_service()
    invoice = SimpleNamespace(id=uuid4(), order_id=uuid4())
    service._invoices.lock.return_value = invoice
    intent = SimpleNamespace(checkout_state="ACTIVE", checkout_provider="DHAMEN")
    service._payments.get_open_intent_for_order.return_value = intent
    service._hosted.close = AsyncMock(return_value=SimpleNamespace(status=PaymentIntentStatus.PAID))
    with pytest.raises(ConflictError):
        await service.close_invoice_payment(invoice_id=invoice.id)
    service._payments.checkpoint.assert_awaited_once()


def test_simulated_session_preserves_wallet_choice_and_rejects_changing_it() -> None:
    service = payment_service()
    service._gateway.uses_hosted_sessions = False
    intent = SimpleNamespace(
        id=uuid4(),
        status=PaymentIntentStatus.NEW,
        use_wallet=False,
        wallet_reserved_amount=Decimal("0.00"),
        amount=Decimal("100.00"),
        gateway_payment_url="https://pay.example.com/test",
    )
    invoice = SimpleNamespace(id=uuid4())
    result = service._reused_invoice_result(invoice, intent, use_wallet=False)
    assert result.use_wallet is False
    with pytest.raises(ConflictError):
        service._reused_invoice_result(invoice, intent, use_wallet=True)

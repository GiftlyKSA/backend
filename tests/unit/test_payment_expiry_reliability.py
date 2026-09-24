"""Financial state regressions independent of external services."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import InvoiceStatus, OrderStatus, PaymentIntentStatus, PaymentPurpose
from app.services import expiry_service
from app.services.invoice_service import InvoiceService
from app.services.payment_reservation_service import PaymentReservationService
from app.services.payment_service import PaymentService, WebhookEvent

from tests.conftest import make_test_settings


def _intent(**changes: object) -> SimpleNamespace:
    values = dict(
        id=uuid4(),
        user_id=uuid4(),
        reference_invoice_id=uuid4(),
        purpose=PaymentPurpose.ORDER_INVOICE,
        status=PaymentIntentStatus.NEW,
        amount=Decimal("424.50"),
        wallet_reserved_amount=Decimal("300.00"),
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _service(intent: SimpleNamespace) -> PaymentService:
    payments = AsyncMock()
    payments.lock_intent_by_payment_link.return_value = intent
    payments.get_intent_by_payment_link.return_value = intent

    async def fail(_intent: object, *, reason: str) -> None:
        intent.status = PaymentIntentStatus.FAILED

    payments.mark_failed.side_effect = fail
    return PaymentService(
        payments=payments,
        invoices=AsyncMock(),
        orders=AsyncMock(),
        wallets=AsyncMock(),
        money=AsyncMock(),
        promos=AsyncMock(),
        users=AsyncMock(),
        gateway=Mock(),
        redis=Mock(),
        settings=make_test_settings(),
    )


async def test_failure_releases_intent_owned_hold_once() -> None:
    intent = _intent()
    service = _service(intent)
    event = WebhookEvent("link", "FAILED", None)
    assert (await service._settle_locked(event)).outcome == "failed"
    assert (await service._settle_locked(event)).outcome == "already_processed"
    service._money.release_hold.assert_awaited_once_with(
        wallet_id=service._wallets.get_by_user.return_value.id, amount=Decimal("300.00")
    )


async def test_failed_release_does_not_mark_intent_failed() -> None:
    service = _service(_intent())
    service._money.release_hold.side_effect = RuntimeError("wallet unavailable")
    with pytest.raises(RuntimeError, match="wallet unavailable"):
        await service._settle_locked(WebhookEvent("link", "FAILED", None))
    service._payments.mark_failed.assert_not_awaited()


@pytest.mark.parametrize("status", [PaymentIntentStatus.PAID, PaymentIntentStatus.FAILED])
async def test_topup_expiry_preserves_terminal_status(monkeypatch, status) -> None:
    intent = _intent(purpose=PaymentPurpose.WALLET_TOPUP, status=status)
    payments = AsyncMock()
    payments.lock_intent.return_value = intent
    monkeypatch.setattr(expiry_service, "PaymentRepository", Mock(return_value=payments))
    assert not await expiry_service.ExpiryService(AsyncMock()).expire_topup_intent(intent.id)
    payments.mark_expired.assert_not_awaited()


async def test_topup_expiry_rechecks_deadline(monkeypatch) -> None:
    intent = _intent(
        purpose=PaymentPurpose.WALLET_TOPUP,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    payments = AsyncMock()
    payments.lock_intent.return_value = intent
    monkeypatch.setattr(expiry_service, "PaymentRepository", Mock(return_value=payments))
    assert not await expiry_service.ExpiryService(AsyncMock()).expire_topup_intent(intent.id)
    payments.mark_expired.assert_not_awaited()


async def test_callback_rechecks_reference_after_waiting_for_invoice_lock() -> None:
    candidate = _intent()
    service = _service(candidate)
    service._payments.lock_intent_by_payment_link.return_value = _intent()
    with pytest.raises(ConflictError, match="reference changed"):
        await service._settle_locked(WebhookEvent("link", "FAILED", None))
    service._money.release_hold.assert_not_awaited()
    service._payments.mark_failed.assert_not_awaited()


async def test_failure_requires_wallet_before_releasing_reservation() -> None:
    service = _service(_intent())
    service._wallets.get_by_user.return_value = None
    with pytest.raises(NotFoundError):
        await service._settle_locked(WebhookEvent("link", "FAILED", None))
    service._payments.mark_failed.assert_not_awaited()


async def test_pending_attempt_is_reused_even_if_wallet_now_covers_total() -> None:
    intent = _intent()
    service = _service(intent)
    invoice = SimpleNamespace(
        id=intent.reference_invoice_id,
        status=InvoiceStatus.ISSUED,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        order_id=uuid4(),
    )
    service._invoices.get_for_actor.return_value = invoice
    service._invoices.lock.return_value = invoice
    service._orders.lock.return_value = SimpleNamespace(
        customer_id=intent.user_id, status=OrderStatus.WAITING_PAYMENT
    )
    intent.gateway_payment_url = "https://example.test/checkout"
    service._payments.get_open_intent_for_invoice.return_value = intent
    service._money.available_balance.return_value = Decimal("1000.00")
    result = await service.pay_invoice(invoice_id=invoice.id, customer_id=intent.user_id)
    assert result.status == "PENDING"
    assert result.amount_from_wallet == Decimal("300.00")
    service._money.fund_escrow_for_invoice.assert_not_awaited()
    service._money.hold_funds.assert_not_awaited()


async def test_success_uses_intent_reservation_not_mutable_invoice_split() -> None:
    intent = _intent()
    service = _service(intent)
    invoice = SimpleNamespace(
        id=intent.reference_invoice_id,
        status=InvoiceStatus.ISSUED,
        order_id=uuid4(),
        amount_from_wallet=Decimal("999.00"),
        payment_method=None,
    )
    service._invoices.lock.return_value = invoice
    service._orders.lock.return_value = SimpleNamespace(
        id=invoice.order_id, status=OrderStatus.WAITING_PAYMENT
    )
    await service._settle_invoice(intent)
    assert service._money.fund_escrow_for_invoice.await_args.kwargs["wallet_amount"] == Decimal(
        "300.00"
    )
    assert invoice.amount_from_wallet == Decimal("300.00")
    assert invoice.status is InvoiceStatus.PAID


async def test_invoice_expiry_releases_only_active_intent_then_promo_and_order() -> None:
    service = expiry_service.ExpiryService(AsyncMock())
    service._invoices = AsyncMock()
    service._payments = AsyncMock()
    service._wallets = AsyncMock()
    service._money = AsyncMock()
    service._promos = AsyncMock()
    service._orders = AsyncMock()
    service._reservations = PaymentReservationService(
        payments=service._payments, wallets=service._wallets, money=service._money
    )
    invoice = SimpleNamespace(
        id=uuid4(),
        order_id=uuid4(),
        status=InvoiceStatus.ISSUED,
        amount_from_wallet=Decimal("999.00"),
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    order = SimpleNamespace(status=OrderStatus.WAITING_PAYMENT, total_amount=Decimal("724.50"))
    service._invoices.lock.return_value = invoice
    service._payments.get_open_intent_for_invoice.return_value = _intent()
    service._orders.lock.return_value = order
    assert await service.expire_invoice(invoice.id)
    assert not await service.expire_invoice(invoice.id)
    service._money.release_hold.assert_awaited_once_with(
        wallet_id=service._wallets.get_by_user.return_value.id, amount=Decimal("300.00")
    )
    service._promos.release.assert_awaited_once_with(invoice_id=invoice.id)
    assert invoice.status is InvoiceStatus.EXPIRED
    assert order.status is OrderStatus.ASSIGNED
    assert order.total_amount == Decimal("0.00")


async def test_expiry_worker_rolls_back_failed_policy(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.get_settings", make_test_settings)
    from app.workers import expiry

    policy = AsyncMock()
    policy.expire_invoice.side_effect = RuntimeError("release failed")
    monkeypatch.setattr(expiry, "ExpiryService", Mock(return_value=policy))
    session = AsyncMock()
    assert not await expiry._expire_invoice(session, uuid4())
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("release_fails", [False, True])
async def test_invoice_cancellation_expires_pending_reservation(release_fails: bool) -> None:
    invoices, orders, reservations = AsyncMock(), AsyncMock(), AsyncMock()
    invoice = SimpleNamespace(id=uuid4(), order_id=uuid4(), status=InvoiceStatus.ISSUED)
    order = SimpleNamespace(status=OrderStatus.WAITING_PAYMENT)
    invoices.lock_for_courier.return_value = invoice
    orders.lock.return_value = order
    service = InvoiceService(
        invoices=invoices,
        orders=orders,
        promos=AsyncMock(),
        eligibility=AsyncMock(),
        settings=make_test_settings(),
        reservations=reservations,
    )
    if release_fails:
        reservations.expire_for_invoice.side_effect = RuntimeError("release failed")
        with pytest.raises(RuntimeError, match="release failed"):
            await service.cancel_invoice(invoice_id=invoice.id, courier_id=uuid4())
        assert invoice.status is InvoiceStatus.ISSUED
        service._promos.release.assert_not_awaited()
        return
    await service.cancel_invoice(invoice_id=invoice.id, courier_id=uuid4())
    reservations.expire_for_invoice.assert_awaited_once_with(invoice.id)
    assert invoice.status is InvoiceStatus.CANCELLED
    assert order.status is OrderStatus.ASSIGNED

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.exceptions import PaymentSessionPendingError
from app.integrations.payments.base import (
    PaymentContext,
    PaymentCustomer,
    PaymentState,
    PaymentStatus,
)
from app.models.enums import PaymentIntentStatus
from app.services.hosted_payment_service import HostedPaymentService


def attempt(state: str = "ACTIVE") -> SimpleNamespace:
    context = PaymentContext(
        reference=str(uuid.uuid4()),
        customer=PaymentCustomer(str(uuid.uuid4()), "Test", "+966501234567", None),
        amount=Decimal("100.00"),
        currency="SAR",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        amount_from_wallet=Decimal("25.00"),
    )
    return SimpleNamespace(
        id=uuid.UUID(context.reference),
        user_id=uuid.UUID(context.customer.external_id),
        checkout_provider="DHAMEN",
        gateway_reference=context.reference,
        gateway_payment_url="https://pay.example.com/test" if state == "ACTIVE" else None,
        checkout_state=state,
        checkout_snapshot=context.model_dump(mode="json"),
        amount=context.amount,
        currency="SAR",
        expires_at=context.expires_at,
        wallet_reserved_amount=context.amount_from_wallet,
        status=PaymentIntentStatus.NEW,
        reference_invoice_id=uuid.uuid4(),
    )


def service(row: SimpleNamespace) -> tuple[HostedPaymentService, AsyncMock, AsyncMock]:
    payments = AsyncMock()
    payments.lock_intent.return_value = row
    payments.lock_session_intent.return_value = row
    gateway = AsyncMock()
    gateway.provider = "DHAMEN"
    gateway.validate_checkout = Mock()
    settle = AsyncMock()
    hosted = HostedPaymentService(
        payments=payments, gateway=gateway, reservations=AsyncMock(), settle=settle
    )
    return hosted, gateway, settle


async def test_active_session_is_reused_without_creating_or_holding_again() -> None:
    row = attempt()
    hosted, gateway, settle = service(row)
    result = await hosted.reuse_or_close(row)
    assert result is row
    gateway.create_checkout.assert_not_called()
    gateway.get_payment_status.assert_not_called()
    settle.assert_not_called()


async def test_unknown_creation_keeps_same_attempt_and_never_creates_another() -> None:
    row = attempt("CREATING")
    hosted, gateway, settle = service(row)
    gateway.get_payment_status.return_value = PaymentStatus(PaymentState.PENDING, row.amount)
    with pytest.raises(PaymentSessionPendingError):
        await hosted.reuse_or_close(row)
    gateway.create_checkout.assert_not_called()
    hosted._reservations.release_locked_intent.assert_not_called()
    settle.assert_not_called()


async def test_timeout_attempt_recovers_existing_checkout_url() -> None:
    row = attempt("CREATING")
    hosted, gateway, _ = service(row)
    gateway.get_payment_status.return_value = PaymentStatus(
        PaymentState.PENDING, row.amount, "https://pay.example.com/recovered"
    )
    result = await hosted.reconcile(row)
    assert result.gateway_payment_url == "https://pay.example.com/recovered"
    assert result.checkout_state == "ACTIVE"
    gateway.create_checkout.assert_not_called()


async def test_authoritative_paid_status_settles_once() -> None:
    row = attempt()
    hosted, gateway, settle = service(row)
    gateway.get_payment_status.return_value = PaymentStatus(PaymentState.PAID, row.amount)
    await hosted.reconcile(row)
    settle.assert_awaited_once_with(row)


async def test_applying_verified_status_never_commits_partial_settlement() -> None:
    row = attempt()
    hosted, gateway, settle = service(row)
    await hosted.apply_status(row, PaymentStatus(PaymentState.PAID, row.amount))
    settle.assert_awaited_once_with(row)
    hosted._payments.checkpoint.assert_not_called()
    gateway.get_payment_status.assert_not_called()


async def test_batch_late_payment_does_not_commit_other_settlements() -> None:
    row = attempt("CLOSED")
    row.status = PaymentIntentStatus.CANCELLED
    hosted, _, settle = service(row)
    with pytest.raises(PaymentSessionPendingError):
        await hosted.apply_status(row, PaymentStatus(PaymentState.PAID, row.amount))
    hosted._payments.checkpoint.assert_not_called()
    settle.assert_not_called()


async def test_cancel_timeout_does_not_release_reservation_or_permit_replacement() -> None:
    row = attempt()
    hosted, gateway, _ = service(row)
    gateway.get_payment_status.return_value = PaymentStatus(PaymentState.PENDING, row.amount)
    gateway.cancel_checkout.side_effect = PaymentSessionPendingError()
    with pytest.raises(PaymentSessionPendingError):
        await hosted.close(row)
    assert row.checkout_state == "CLOSING"
    assert row.status is PaymentIntentStatus.NEW
    hosted._reservations.release_locked_intent.assert_not_called()


async def test_successful_close_releases_once_and_preserves_history() -> None:
    row = attempt()
    hosted, gateway, _ = service(row)
    gateway.get_payment_status.return_value = PaymentStatus(PaymentState.PENDING, row.amount)
    await hosted.close(row)
    assert row.status is PaymentIntentStatus.CANCELLED
    assert row.checkout_state == "CLOSED"
    hosted._reservations.release_locked_intent.assert_awaited_once_with(row)
    assert row.gateway_reference is not None


async def test_late_payment_after_cancellation_requires_review_without_double_settlement() -> None:
    row = attempt("CLOSED")
    row.status = PaymentIntentStatus.CANCELLED
    hosted, gateway, settle = service(row)
    gateway.get_payment_status.return_value = PaymentStatus(PaymentState.PAID, row.amount)
    with pytest.raises(PaymentSessionPendingError):
        await hosted.reconcile(row)
    assert row.checkout_state == "REVIEW"
    assert row.status is PaymentIntentStatus.CANCELLED
    settle.assert_not_called()


async def test_invalid_context_is_rejected_before_creation_claim_is_committed() -> None:
    row = attempt("CREATING")
    hosted, gateway, _ = service(row)
    gateway.validate_checkout = Mock(side_effect=PaymentSessionPendingError())
    with pytest.raises(PaymentSessionPendingError):
        await hosted.create(row, PaymentContext.model_validate(row.checkout_snapshot))
    hosted._payments.checkpoint.assert_not_called()
    gateway.create_checkout.assert_not_called()


async def test_expired_session_paid_during_close_is_returned_without_replacement() -> None:
    row = attempt()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    hosted, gateway, settle = service(row)
    gateway.get_payment_status.side_effect = [
        PaymentStatus(PaymentState.PENDING, row.amount),
        PaymentStatus(PaymentState.PAID, row.amount),
    ]

    async def mark_paid(current: SimpleNamespace) -> None:
        current.status = PaymentIntentStatus.PAID

    settle.side_effect = mark_paid
    assert await hosted.reuse_or_close(row) is row
    assert row.status is PaymentIntentStatus.PAID
    gateway.cancel_checkout.assert_not_called()
    hosted._reservations.release_locked_intent.assert_not_called()

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.config import Environment
from app.core.exceptions import DomainError
from app.integrations.payments.base import PaymentState, PaymentStatus
from app.integrations.payments.dhamen import DhamenPaymentClient
from app.models.enums import PaymentIntentStatus
from app.services.payment_service import PaymentService

from tests.conftest import make_test_settings


def service() -> PaymentService:
    gateway = Mock()
    gateway.provider = "DHAMEN"
    gateway.uses_hosted_sessions = True
    gateway.parse_notifications = DhamenPaymentClient.parse_notifications
    svc = PaymentService(
        **{
            key: AsyncMock()
            for key in ("payments", "invoices", "orders", "wallets", "money", "promos", "users")
        },
        gateway=gateway,
        redis=AsyncMock(),
        settings=make_test_settings(),
    )

    async def lock_batch(ids):
        return {id: await svc._payments.get_intent(id) for id in ids}

    svc._payments.lock_session_batch.side_effect = lock_batch
    return svc


def body(reference: str, event: str = "Deposit_Notification") -> bytes:
    return json.dumps(
        {
            "Header": {"BatchId": "batch-test", "BatchCreationTime": "2026-10-06T10:00:00Z"},
            "Notifications": [
                {
                    "NotificationId": 1,
                    "NotificationType": event,
                    "NotificationTime": "2026-10-06T10:00:00Z",
                    "Payment": {"PaymentID": reference, "PaymentAmount": "999999.00"},
                }
            ],
        }
    ).encode()


async def test_notification_only_reconciles_existing_provider_reference_not_payload_amount() -> (
    None
):
    svc = service()
    reference = uuid.uuid4()
    intent = SimpleNamespace(
        id=reference,
        user_id=uuid.uuid4(),
        status=PaymentIntentStatus.NEW,
        checkout_provider="DHAMEN",
        amount=Decimal("100.00"),
        gateway_reference=str(reference),
    )
    svc._payments.get_intent.return_value = intent
    svc._hosted = AsyncMock()
    status = PaymentStatus(PaymentState.PAID, intent.amount)
    svc._hosted.verify_status.return_value = status
    result = await svc.handle_dhamen_notifications(raw_body=body(str(reference)))
    assert result.status == "SUCCESS"
    svc._hosted.verify_status.assert_awaited_once_with(intent)
    svc._hosted.apply_locked_status.assert_awaited_once_with(intent, status)
    svc._money.fund_escrow_for_invoice.assert_not_called()


async def test_refund_notification_never_marks_invoice_paid() -> None:
    svc = service()
    svc._hosted = AsyncMock()
    result = await svc.handle_dhamen_notifications(
        raw_body=body(str(uuid.uuid4()), "Refund_Payment_Notification")
    )
    assert result.status == "SUCCESS"
    svc._hosted.reconcile.assert_not_called()


async def test_batch_verifies_all_payments_before_any_settlement() -> None:
    svc = service()
    references = [uuid.uuid4(), uuid.uuid4()]
    intents = {
        str(reference): SimpleNamespace(
            id=reference,
            user_id=uuid.uuid4(),
            status=PaymentIntentStatus.NEW,
            checkout_provider="DHAMEN",
            gateway_reference=str(reference),
        )
        for reference in references
    }
    svc._payments.get_intent.side_effect = lambda value: intents[str(value)]
    svc._hosted = AsyncMock()
    events: list[str] = []

    async def verify(intent: SimpleNamespace) -> PaymentStatus:
        events.append("verify")
        return PaymentStatus(PaymentState.PAID, Decimal("100.00"))

    async def apply(intent: SimpleNamespace, status: PaymentStatus) -> None:
        events.append("apply")

    svc._hosted.verify_status.side_effect = verify
    svc._hosted.apply_locked_status.side_effect = apply
    payload = json.loads(body(str(references[0])))
    payload["Notifications"][0]["Payment"] = [
        {"PaymentID": str(reference)} for reference in references
    ]
    await svc.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
    assert events == ["verify", "verify", "apply", "apply"]


async def test_failed_batch_verification_never_starts_settlement() -> None:
    svc = service()
    references = [uuid.uuid4(), uuid.uuid4()]
    svc._payments.get_intent.side_effect = lambda reference: SimpleNamespace(
        id=reference,
        user_id=uuid.uuid4(),
        status=PaymentIntentStatus.NEW,
        checkout_provider="DHAMEN",
        gateway_reference=str(reference),
    )
    svc._hosted = AsyncMock()
    svc._hosted.verify_status.side_effect = [
        PaymentStatus(PaymentState.PAID, Decimal("100.00")),
        DomainError(),
    ]
    payload = json.loads(body(str(references[0])))
    payload["Notifications"][0]["Payment"] = [
        {"PaymentID": str(reference)} for reference in references
    ]
    with pytest.raises(DomainError):
        await svc.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
    assert svc._hosted.verify_status.await_count == 2
    svc._hosted.apply_locked_status.assert_not_called()
    svc._payments.insert_notification_receipt_if_new.assert_not_called()


async def test_duplicate_payment_references_are_verified_and_applied_once_per_batch() -> None:
    svc = service()
    reference = uuid.uuid4()
    svc._payments.get_intent.return_value = SimpleNamespace(
        id=reference,
        user_id=uuid.uuid4(),
        status=PaymentIntentStatus.NEW,
        checkout_provider="DHAMEN",
        gateway_reference=str(reference),
    )
    svc._hosted = AsyncMock()
    payload = json.loads(body(str(reference)))
    duplicate = dict(payload["Notifications"][0], NotificationId=2)
    payload["Notifications"].append(duplicate)
    await svc.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
    svc._hosted.verify_status.assert_awaited_once()
    svc._hosted.apply_locked_status.assert_awaited_once()
    assert svc._payments.insert_notification_receipt_if_new.await_count == 2


async def test_malformed_batch_and_unknown_reference_are_not_acknowledged() -> None:
    svc = service()
    with pytest.raises(DomainError):
        await svc.handle_dhamen_notifications(raw_body=b"{}")
    svc._payments.get_intent.return_value = None
    with pytest.raises(DomainError):
        await svc.handle_dhamen_notifications(raw_body=body(str(uuid.uuid4())))


async def test_production_notifications_remain_disabled_before_any_side_effect() -> None:
    svc = service()
    svc._settings = make_test_settings().model_copy(update={"ENVIRONMENT": Environment.PRODUCTION})
    with pytest.raises(DomainError) as caught:
        await svc.handle_dhamen_notifications(raw_body=body(str(uuid.uuid4())))
    assert caught.value.code == "PAYMENTS_DISABLED"
    svc._payments.get_intent.assert_not_called()

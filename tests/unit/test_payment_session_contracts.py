import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.exceptions import NotFoundError, PaymentSessionPendingError
from app.models.enums import PaymentIntentStatus
from app.routers.payment_sessions import _response
from app.services.payment_service import PaymentService

from tests.conftest import make_test_settings


def row(status: PaymentIntentStatus = PaymentIntentStatus.NEW) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        reference_invoice_id=uuid.uuid4(),
        checkout_snapshot=None,
        checkout_provider="DHAMEN",
        checkout_state="ACTIVE",
        status=status,
        amount=Decimal("100.00"),
        wallet_reserved_amount=Decimal("25.00"),
        currency="SAR",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        gateway_payment_url="https://pay.example.com/test",
    )


def test_active_session_contract_has_exact_amounts_and_resume_url() -> None:
    intent = row()
    response = _response(intent)
    assert response.status == "PENDING"
    assert response.amount_from_wallet == "25.00"
    assert response.amount_from_gateway == "100.00"
    assert response.payment_url == intent.gateway_payment_url


@pytest.mark.parametrize("status", [PaymentIntentStatus.PAID, PaymentIntentStatus.CANCELLED])
def test_terminal_sessions_never_offer_payment_url(status: PaymentIntentStatus) -> None:
    assert _response(row(status)).payment_url is None


def test_expired_session_never_offers_stale_url() -> None:
    intent = row()
    intent.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert _response(intent).payment_url is None
    assert _response(intent).status == "PENDING"


async def test_session_read_and_order_recovery_enforce_payer_scope() -> None:
    payments = AsyncMock()
    payments.get_intent_for_actor.return_value = None
    payments.get_latest_intent_for_order.return_value = None
    svc = PaymentService(
        payments=payments,
        **{
            key: AsyncMock()
            for key in ("invoices", "orders", "wallets", "money", "promos", "users")
        },
        gateway=Mock(),
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    intent_id, user_id, order_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with pytest.raises(NotFoundError):
        await svc.get_payment_session(intent_id=intent_id, user_id=user_id)
    payments.get_intent_for_actor.assert_awaited_once_with(intent_id, user_id)
    with pytest.raises(NotFoundError):
        await svc.get_order_payment_session(order_id=order_id, user_id=user_id)
    payments.get_latest_intent_for_order.assert_awaited_once_with(
        order_id=order_id, user_id=user_id
    )


@pytest.mark.parametrize("paid", [False, True])
async def test_checkout_response_obeys_callback_or_cancellation_during_creation(
    paid: bool,
) -> None:
    users = AsyncMock()
    users.get.return_value = SimpleNamespace(
        id=uuid.uuid4(), full_name="Test", phone="+966501234567", email=None
    )
    gateway = Mock(uses_hosted_sessions=True)
    svc = PaymentService(
        **{
            key: AsyncMock()
            for key in ("payments", "invoices", "orders", "wallets", "money", "promos")
        },
        users=users,
        gateway=gateway,
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    intent = row(PaymentIntentStatus.PAID if paid else PaymentIntentStatus.NEW)
    intent.reference_invoice_id = None
    intent.checkout_state = "CLOSED" if paid else "CLOSING"
    svc._hosted.create = AsyncMock(return_value=intent)
    if paid:
        assert (
            await svc._create_checkout(intent=intent, user_id=users.get.return_value.id, items=())
            is None
        )
    else:
        with pytest.raises(PaymentSessionPendingError):
            await svc._create_checkout(intent=intent, user_id=users.get.return_value.id, items=())


async def test_callback_batch_locks_every_intent_before_any_settlement() -> None:
    payments = AsyncMock()
    service = PaymentService(
        payments=payments,
        **{
            key: AsyncMock()
            for key in ("invoices", "orders", "wallets", "money", "promos", "users")
        },
        gateway=Mock(provider="DHAMEN"),
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    intents = [row(), row()]
    for intent in intents:
        intent.user_id = uuid.uuid4()
    events = []

    async def lock_batch(ids):
        events.append(("lock", set(ids)))
        return {intent.id: intent for intent in intents}

    async def settle(intent, status):
        assert events == [("lock", {item.id for item in intents}), ("wallets",)]

    payments.lock_session_batch.side_effect = lock_batch
    service._wallets.lock_payment_batch.side_effect = lambda **kwargs: events.append(("wallets",))
    service._verify_dhamen_payments = AsyncMock(return_value=[(item, None) for item in intents])
    service._gateway.parse_notifications.return_value = ()
    service._hosted.apply_locked_status = AsyncMock(side_effect=settle)
    await service.handle_dhamen_notifications(raw_body=b"{}")
    assert service._hosted.apply_locked_status.await_count == 2
    payments.lock_session_intent.assert_not_awaited()


async def test_batch_locks_invoices_then_orders_then_intents_in_id_order() -> None:
    from app.repositories.payment_repository import PaymentRepository
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    references = [(uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]
    result = Mock()
    result.all.return_value = references
    session.execute.return_value = result
    ids = [uuid.uuid4(), uuid.uuid4()]
    session.scalars.return_value = [SimpleNamespace(id=id) for id in ids]
    locked = await PaymentRepository(session).lock_session_batch(ids)
    assert set(locked) == set(ids)
    sql = [
        str(call.args[0].compile(dialect=postgresql.dialect()))
        for call in session.execute.call_args_list
    ]
    assert len(sql) == 3
    assert session.scalars.await_count == 1
    sql.append(str(session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())))
    for query, table in zip(sql[1:], ("invoices", "orders", "payment_intents"), strict=True):
        assert f"FROM {table}" in query
        assert f"ORDER BY {table}.id FOR UPDATE" in query
    assert "FOR UPDATE" not in sql[0]
    session.commit.assert_not_awaited()


async def test_late_paid_callback_persists_review_before_any_batch_settlement() -> None:
    from app.integrations.payments.base import PaymentState, PaymentStatus

    payments, wallets = AsyncMock(), AsyncMock()
    service = PaymentService(
        payments=payments,
        wallets=wallets,
        **{key: AsyncMock() for key in ("invoices", "orders", "money", "promos", "users")},
        gateway=Mock(provider="DHAMEN"),
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    normal, late = row(), row(PaymentIntentStatus.CANCELLED)
    normal.user_id, late.user_id = uuid.uuid4(), uuid.uuid4()
    late.checkout_state = "CLOSED"
    payments.lock_session_batch.return_value = {normal.id: normal, late.id: late}
    service._verify_dhamen_payments = AsyncMock(
        return_value=[
            (item, PaymentStatus(PaymentState.PAID, item.amount)) for item in (normal, late)
        ]
    )
    service._gateway.parse_notifications.return_value = ()
    service._hosted.apply_locked_status = AsyncMock()
    with pytest.raises(PaymentSessionPendingError):
        await service.handle_dhamen_notifications(raw_body=b"{}")
    assert late.checkout_state == "REVIEW"
    assert late.status is PaymentIntentStatus.CANCELLED
    payments.checkpoint.assert_awaited_once()
    service._hosted.apply_locked_status.assert_not_awaited()
    wallets.lock_payment_batch.assert_not_awaited()


async def test_batch_ledger_rows_precede_globally_sorted_wallet_locks() -> None:
    from app.repositories.wallet_repository import WalletRepository
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    session.scalars.return_value = [uuid.uuid4(), uuid.uuid4()]
    await WalletRepository(session).lock_payment_batch(
        intent_ids=[uuid.uuid4()], user_ids=[uuid.uuid4()]
    )
    ledger = str(session.scalars.call_args.args[0].compile(dialect=postgresql.dialect()))
    wallets = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ORDER BY transactions.id" in ledger and "FOR UPDATE" in ledger
    assert "ORDER BY wallets.id FOR UPDATE" in wallets
    assert "wallets.user_id IN" in wallets and "wallets.type IN" in wallets
    assert "wallets.id IN" in wallets
    session.commit.assert_not_awaited()


async def test_oversized_payment_ledger_batch_fails_before_wallet_lock() -> None:
    from app.repositories.wallet_repository import WalletRepository

    session = AsyncMock()
    session.scalars.return_value = [uuid.uuid4()] * 401
    with pytest.raises(ValueError, match="ledger limit"):
        await WalletRepository(session).lock_payment_batch(
            intent_ids=[uuid.uuid4()], user_ids=[uuid.uuid4()]
        )
    session.execute.assert_not_awaited()

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import PaymentProviderUnavailableError, PaymentSessionPendingError
from app.integrations.payments.base import PaymentCheckout, PaymentState, PaymentStatus
from app.integrations.payments.dhamen import DhamenPaymentClient
from app.models import Invoice, Order, PaymentIntent, Transaction
from app.models.enums import (
    InvoiceStatus,
    OrderStatus,
    PaymentIntentStatus,
    PaymentPurpose,
    TransactionStatus,
    TransactionType,
)
from app.repositories.payment_repository import PaymentRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.money_service import MoneyService
from app.services.payment_service import build_payment_service
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.conftest import make_test_settings
from tests.integration.test_payment_service import _customer_with_wallet, _issued_invoice


def gateway() -> AsyncMock:
    client = AsyncMock()
    client.provider = "DHAMEN"
    client.uses_hosted_sessions = True
    client.validate_checkout = Mock()
    client.create_checkout.side_effect = lambda context: PaymentCheckout(
        context.reference, "https://pay.example.com/test"
    )
    return client


@pytest.mark.parametrize("fail_settlement", [False, True])
async def test_notification_batch_invoice_order_and_ledger_are_atomic(
    db_connection: AsyncConnection, fail_settlement: bool
) -> None:
    async with AsyncSession(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    ) as db_session:
        await _assert_notification_batch_atomic(db_session, fail_settlement)


async def _assert_notification_batch_atomic(
    db_session: AsyncSession, fail_settlement: bool
) -> None:
    client = gateway()
    client.parse_notifications = DhamenPaymentClient.parse_notifications
    client.get_payment_status.side_effect = lambda context: PaymentStatus(
        PaymentState.PAID, context.amount
    )
    service = build_payment_service(
        session=db_session, gateway=client, redis=AsyncMock(), settings=make_test_settings()
    )
    records = []
    for _ in range(2):
        customer, order, invoice = await _issued_invoice(db_session)
        result = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
        records.append((invoice.id, order.id, result.intent_id))
    payload = {
        "Header": {"BatchId": str(uuid4()), "BatchCreationTime": datetime.now(UTC).isoformat()},
        "Notifications": [
            {
                "NotificationId": uuid4().int % 9223372036854775807,
                "NotificationType": "Deposit_Notification",
                "NotificationTime": datetime.now(UTC).isoformat(),
                "Payment": [{"PaymentID": str(intent_id)} for _, _, intent_id in records],
            }
        ],
    }
    if fail_settlement:
        service._promos.consume = AsyncMock(side_effect=[None, RuntimeError("test failure")])
        with pytest.raises(RuntimeError, match="test failure"):
            await service.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
        await db_session.rollback()
    else:
        ack = await service.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
        await db_session.commit()
        assert ack.status == "SUCCESS"
        await service.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
        await db_session.commit()
    for invoice_id, order_id, intent_id in records:
        invoice = await db_session.get(Invoice, invoice_id)
        order = await db_session.get(Order, order_id)
        intent = await db_session.get(PaymentIntent, intent_id)
        assert invoice.status is (InvoiceStatus.ISSUED if fail_settlement else InvoiceStatus.PAID)
        assert order.status is (
            OrderStatus.WAITING_PAYMENT if fail_settlement else OrderStatus.IN_PROGRESS
        )
        assert intent.status is (
            PaymentIntentStatus.NEW if fail_settlement else PaymentIntentStatus.PAID
        )
        rows = list(
            await db_session.scalars(
                select(Transaction).where(Transaction.reference_intent_id == intent_id)
            )
        )
        assert rows
        expected = TransactionStatus.PENDING if fail_settlement else TransactionStatus.SETTLED
        assert all(row.status is expected for row in rows)
        assert sum((row.amount for row in rows), Decimal("0.00")) == Decimal("0.00")


async def test_pending_invoice_rows_settle_in_place_and_consume_hold(
    db_session: AsyncSession,
) -> None:
    customer, order, invoice = await _issued_invoice(db_session)
    wallets = WalletRepository(db_session)
    wallet = await wallets.get_by_user(customer.id)
    await MoneyService(wallets).credit_wallet_reward(
        user_wallet_id=wallet.id,
        amount=Decimal("300.00"),
        reason=TransactionType.GIVEAWAY,
        operation_id=uuid4(),
    )
    client = gateway()
    service = build_payment_service(
        session=db_session, gateway=client, redis=AsyncMock(), settings=make_test_settings()
    )
    result = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
    rows = list(
        await db_session.scalars(
            select(Transaction).where(Transaction.reference_intent_id == result.intent_id)
        )
    )
    assert all(row.status is TransactionStatus.PENDING for row in rows)
    row_ids = {row.id for row in rows}
    assert wallet.balance == Decimal("300.00")
    assert wallet.held_balance == Decimal("300.00")
    client.get_payment_status.return_value = PaymentStatus(
        PaymentState.PAID, result.amount_from_gateway
    )
    await service.refresh_payment_session(intent_id=result.intent_id, user_id=customer.id)
    await db_session.flush()
    assert wallet.balance == Decimal("0.00")
    assert wallet.held_balance == Decimal("0.00")
    settled = list(
        await db_session.scalars(
            select(Transaction).where(Transaction.reference_intent_id == result.intent_id)
        )
    )
    assert {row.id for row in settled} == row_ids
    assert all(row.status is TransactionStatus.SETTLED for row in settled)
    assert sum((row.amount for row in settled), Decimal("0.00")) == Decimal("0.00")
    assert (await MoneyService(wallets).reconcile()).ok


@pytest.mark.parametrize("paid", [False, True])
async def test_topup_pending_credit_only_changes_balance_on_verified_payment(
    db_session: AsyncSession,
    paid: bool,
) -> None:
    customer, wallet = await _customer_with_wallet(db_session)
    client = gateway()
    service = build_payment_service(
        session=db_session, gateway=client, redis=AsyncMock(), settings=make_test_settings()
    )
    result = await service.create_topup(user_id=customer.id, amount=Decimal("100.00"))
    assert wallet.balance == Decimal("0.00")
    assert client.create_checkout.call_args.args[0].title == "Top up"
    context = client.create_checkout.call_args.args[0]
    assert str(customer.id) in context.description
    client.get_payment_status.return_value = PaymentStatus(
        PaymentState.PAID if paid else PaymentState.PENDING, Decimal("100.00")
    )
    if paid:
        await service.refresh_payment_session(intent_id=result.intent_id, user_id=customer.id)
        await service.refresh_payment_session(intent_id=result.intent_id, user_id=customer.id)
    else:
        await service.cancel_payment_session(intent_id=result.intent_id, user_id=customer.id)
    await db_session.flush()
    assert wallet.balance == (Decimal("100.00") if paid else Decimal("0.00"))
    rows = list(
        await db_session.scalars(
            select(Transaction).where(Transaction.reference_intent_id == result.intent_id)
        )
    )
    assert len(rows) == 2
    expected = TransactionStatus.SETTLED if paid else TransactionStatus.REVERSED
    assert all(row.status is expected for row in rows)
    assert (await MoneyService(WalletRepository(db_session)).reconcile()).ok


async def test_order_reuses_one_hosted_session_and_full_invoice_snapshot(
    db_session: AsyncSession,
) -> None:
    customer, order, invoice = await _issued_invoice(db_session)
    client = gateway()
    service = build_payment_service(
        session=db_session, gateway=client, redis=AsyncMock(), settings=make_test_settings()
    )
    first = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
    second = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
    assert first.intent_id == second.intent_id
    assert second.session_reused is True
    client.create_checkout.assert_awaited_once()
    intent = await PaymentRepository(db_session).get_open_intent_for_order(order.id)
    assert intent.checkout_snapshot["invoice"]["total_amount"] == "630.00"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PaymentIntent)
            .where(PaymentIntent.order_id == order.id)
        )
        == 1
    )


async def test_creation_timeout_is_durable_and_recovers_the_same_reference(
    db_session: AsyncSession,
) -> None:
    customer, order, invoice = await _issued_invoice(db_session)
    client = gateway()
    client.create_checkout.side_effect = PaymentProviderUnavailableError()
    service = build_payment_service(
        session=db_session, gateway=client, redis=AsyncMock(), settings=make_test_settings()
    )
    with pytest.raises(PaymentProviderUnavailableError):
        await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
    intent = await service.get_order_payment_session(order_id=order.id, user_id=customer.id)
    assert intent.checkout_state == "CREATING"
    client.get_payment_status.return_value = PaymentStatus(
        PaymentState.PENDING, intent.amount, "https://pay.example.com/recovered"
    )
    result = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
    assert result.intent_id == intent.id
    assert result.payment_url == "https://pay.example.com/recovered"
    client.create_checkout.assert_awaited_once()


async def test_database_rejects_two_open_attempts_for_same_order(db_session: AsyncSession) -> None:
    customer, order, invoice = await _issued_invoice(db_session)
    repository = PaymentRepository(db_session)
    await repository.create_intent(
        user_id=customer.id,
        purpose=PaymentPurpose.ORDER_INVOICE,
        amount=Decimal("100.00"),
        reference_invoice_id=invoice.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    with pytest.raises(IntegrityError), db_session.begin_nested():
        await repository.create_intent(
            user_id=customer.id,
            purpose=PaymentPurpose.ORDER_INVOICE,
            amount=Decimal("100.00"),
            reference_invoice_id=invoice.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    intent = await repository.get_open_intent_for_order(order.id)
    assert intent.status is PaymentIntentStatus.NEW


async def test_two_connections_replace_expired_checkout_by_reusing_the_winner() -> None:
    from tests.integration.test_orders_api import _make_stack

    settings, engine, factory = await _make_stack()
    client = gateway()
    try:
        async with factory() as session:
            customer, order, invoice = await _issued_invoice(session)
            await session.commit()
            service = build_payment_service(
                session=session, gateway=client, redis=AsyncMock(), settings=settings
            )
            first = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
            intent = await PaymentRepository(session).get_intent(first.intent_id)
            intent.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()
            customer_id, invoice_id, old_reference = (
                customer.id,
                invoice.id,
                intent.gateway_reference,
            )
            order_id = order.id

        arrived = 0
        both_arrived = asyncio.Event()

        async def status(context):
            nonlocal arrived
            if context.reference == old_reference:
                arrived += 1
                if arrived == 2:
                    both_arrived.set()
                if arrived <= 2:
                    await asyncio.wait_for(both_arrived.wait(), timeout=10)
            return PaymentStatus(
                PaymentState.PENDING, context.amount, "https://pay.example.com/test"
            )

        client.get_payment_status.side_effect = status

        async def pay():
            async with factory() as session:
                service = build_payment_service(
                    session=session, gateway=client, redis=AsyncMock(), settings=settings
                )
                result = await service.pay_invoice(invoice_id=invoice_id, customer_id=customer_id)
                await session.commit()
                return result

        results = await asyncio.gather(pay(), pay())
        assert results[0].intent_id == results[1].intent_id
        assert client.create_checkout.await_count == 2
        async with factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(PaymentIntent)
                .where(
                    PaymentIntent.order_id == order_id,
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                )
            )
            assert count == 1
    finally:
        await engine.dispose()


async def test_late_paid_batch_keeps_review_after_receiver_rollback(db_connection) -> None:
    async with AsyncSession(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    ) as session:
        client = gateway()
        client.parse_notifications = DhamenPaymentClient.parse_notifications
        client.get_payment_status.side_effect = lambda context: PaymentStatus(
            PaymentState.PENDING, context.amount
        )
        service = build_payment_service(
            session=session, gateway=client, redis=AsyncMock(), settings=make_test_settings()
        )
        records = []
        for _ in range(2):
            customer, _, invoice = await _issued_invoice(session)
            result = await service.pay_invoice(invoice_id=invoice.id, customer_id=customer.id)
            records.append((customer.id, invoice.id, result.intent_id))
        await service.cancel_payment_session(intent_id=records[1][2], user_id=records[1][0])
        await session.commit()
        client.get_payment_status.side_effect = lambda context: PaymentStatus(
            PaymentState.PAID, context.amount
        )
        payload = {
            "Header": {"BatchId": str(uuid4()), "BatchCreationTime": datetime.now(UTC).isoformat()},
            "Notifications": [
                {
                    "NotificationId": uuid4().int % 9223372036854775807,
                    "NotificationType": "Deposit_Notification",
                    "NotificationTime": datetime.now(UTC).isoformat(),
                    "Payment": [{"PaymentID": str(record[2])} for record in records],
                }
            ],
        }
        with pytest.raises(PaymentSessionPendingError):
            await service.handle_dhamen_notifications(raw_body=json.dumps(payload).encode())
        await session.rollback()
        normal = await session.get(PaymentIntent, records[0][2])
        late = await session.get(PaymentIntent, records[1][2])
        assert normal.status is PaymentIntentStatus.NEW
        assert late.status is PaymentIntentStatus.CANCELLED
        assert late.checkout_state == "REVIEW"
        assert (await session.get(Invoice, records[0][1])).status is InvoiceStatus.ISSUED
        with pytest.raises(PaymentSessionPendingError):
            await service.pay_invoice(invoice_id=records[1][1], customer_id=records[1][0])


async def test_batch_prelocks_wallet_union_before_other_intent_can_settle() -> None:
    from tests.integration.test_orders_api import _make_stack

    _, engine, factory = await _make_stack()
    try:
        async with factory() as seed:
            records = [await _customer_with_wallet(seed) for _ in range(2)]
            repository = PaymentRepository(seed)
            ids = []
            for user, _ in records:
                intent = await repository.create_intent(
                    user_id=user.id,
                    purpose=PaymentPurpose.WALLET_TOPUP,
                    amount=Decimal("100.00"),
                    reference_invoice_id=None,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
                ids.append(intent.id)
            user_ids = [user.id for user, _ in records]
            other_wallet_id = records[1][1].id
            await seed.commit()

        async with factory() as batch, factory() as other:
            await PaymentRepository(batch).lock_session_batch(ids)
            await WalletRepository(batch).lock_payment_batch(intent_ids=ids, user_ids=user_ids)
            started = asyncio.Event()

            async def contend():
                started.set()
                return await WalletRepository(other).lock_wallets([other_wallet_id])

            contender = asyncio.create_task(contend())
            try:
                await started.wait()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(contender), timeout=0.1)
                await batch.commit()
                locked = await asyncio.wait_for(contender, timeout=5)
                assert other_wallet_id in locked
                await other.rollback()
            finally:
                if not contender.done():
                    contender.cancel()
                await asyncio.gather(contender, return_exceptions=True)
    finally:
        await engine.dispose()

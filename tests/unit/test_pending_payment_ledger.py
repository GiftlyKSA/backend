from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import InsufficientFundsError
from app.models.enums import TransactionStatus, TransactionType
from app.services.money_service import Leg, MoneyService


async def test_staging_balanced_group_does_not_change_wallet_balance() -> None:
    wallet_id, counter_id, intent_id = uuid4(), uuid4(), uuid4()
    wallet = SimpleNamespace(balance=Decimal("100.00"), held_balance=Decimal("25.00"))
    counter = SimpleNamespace(balance=Decimal("0.00"), held_balance=Decimal("0.00"))
    repo = AsyncMock()
    repo.idempotency_key_exists.return_value = False
    repo.append_transaction = Mock()
    repo.lock_wallets.return_value = {wallet_id: wallet, counter_id: counter}
    legs = [
        Leg(
            wallet_id,
            Decimal("-25.00"),
            TransactionType.PAYMENT,
            reference_intent_id=intent_id,
            idempotency_key=f"intent:{intent_id}:pending",
        ),
        Leg(
            counter_id, Decimal("25.00"), TransactionType.ESCROW_HOLD, reference_intent_id=intent_id
        ),
    ]
    await MoneyService(repo).stage_group(legs=legs, correlation_id=uuid4())
    assert wallet.balance == Decimal("100.00")
    assert counter.balance == Decimal("0.00")
    assert all(
        call.kwargs["status"] is TransactionStatus.PENDING
        for call in repo.append_transaction.call_args_list
    )


async def test_verified_pending_debit_settles_once_and_consumes_its_hold() -> None:
    wallet_id, counter_id, intent_id, correlation = uuid4(), uuid4(), uuid4(), uuid4()
    wallet = SimpleNamespace(
        balance=Decimal("100.00"), held_balance=Decimal("25.00"), version=1, user_id=uuid4()
    )
    counter = SimpleNamespace(
        balance=Decimal("0.00"), held_balance=Decimal("0.00"), version=1, user_id=None
    )
    rows = [
        SimpleNamespace(
            wallet_id=wid,
            amount=amount,
            status=TransactionStatus.PENDING,
            balance_after=Decimal("0.00"),
            correlation_id=correlation,
        )
        for wid, amount in [(wallet_id, Decimal("-25.00")), (counter_id, Decimal("25.00"))]
    ]
    repo = AsyncMock()
    repo.lock_intent_transactions.return_value = rows
    repo.lock_wallets.return_value = {wallet_id: wallet, counter_id: counter}
    money = MoneyService(repo)
    assert (
        await money.settle_pending_intent(
            intent_id=intent_id, held_wallet_id=wallet_id, held_amount=Decimal("25.00")
        )
        is True
    )
    assert (
        await money.settle_pending_intent(
            intent_id=intent_id, held_wallet_id=wallet_id, held_amount=Decimal("25.00")
        )
        is False
    )
    assert wallet.balance == Decimal("75.00")
    assert wallet.held_balance == Decimal("0.00")
    assert all(row.status is TransactionStatus.SETTLED for row in rows)
    assert rows[0].balance_after == Decimal("75.00")


async def test_reversing_pending_topup_never_credits_balance() -> None:
    rows = [SimpleNamespace(status=TransactionStatus.PENDING) for _ in range(2)]
    repo = AsyncMock()
    repo.lock_intent_transactions.return_value = rows
    await MoneyService(repo).reverse_pending_intent(uuid4())
    assert all(row.status is TransactionStatus.REVERSED for row in rows)
    repo.lock_wallets.assert_not_awaited()


async def test_pending_settlement_does_not_spend_another_payment_hold() -> None:
    wallet_id, counter_id, correlation = uuid4(), uuid4(), uuid4()
    rows = [
        SimpleNamespace(
            wallet_id=wid,
            amount=amount,
            status=TransactionStatus.PENDING,
            correlation_id=correlation,
        )
        for wid, amount in [(wallet_id, Decimal("-25.00")), (counter_id, Decimal("25.00"))]
    ]
    wallet = SimpleNamespace(
        user_id=uuid4(), balance=Decimal("40.00"), held_balance=Decimal("50.00")
    )
    counter = SimpleNamespace(user_id=None, balance=Decimal("0.00"), held_balance=Decimal("0.00"))
    repo = AsyncMock()
    repo.lock_intent_transactions.return_value = rows
    repo.lock_wallets.return_value = {wallet_id: wallet, counter_id: counter}
    with pytest.raises(InsufficientFundsError):
        await MoneyService(repo).settle_pending_intent(
            intent_id=uuid4(), held_wallet_id=wallet_id, held_amount=Decimal("25.00")
        )
    assert wallet.balance == Decimal("40.00")
    assert wallet.held_balance == Decimal("50.00")
    assert all(row.status is TransactionStatus.PENDING for row in rows)

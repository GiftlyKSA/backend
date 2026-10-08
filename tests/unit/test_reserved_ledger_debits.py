from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import InsufficientFundsError
from app.models.enums import TransactionType
from app.services.money_service import Leg, MoneyService


@asynccontextmanager
async def _savepoint():
    yield


@pytest.mark.parametrize("debits", [["-80.00"], ["-10.00", "-15.00"]])
async def test_locked_debit_preserves_other_reservations(debits):
    wallet_id, counter_id = uuid4(), uuid4()
    wallet = SimpleNamespace(
        user_id=uuid4(), balance=Decimal("100.00"), held_balance=Decimal("80.00"), version=1
    )
    counter = SimpleNamespace(
        user_id=None, balance=Decimal("0.00"), held_balance=Decimal("0.00"), version=1
    )
    repo = AsyncMock()
    repo.savepoint = Mock(side_effect=_savepoint)
    repo.append_transaction = Mock()
    repo.idempotency_key_exists.return_value = False
    repo.lock_wallets.return_value = {wallet_id: wallet, counter_id: counter}
    legs = [Leg(wallet_id, Decimal(amount), TransactionType.PAYMENT) for amount in debits]
    legs.append(
        Leg(
            counter_id,
            -sum((leg.amount for leg in legs), Decimal("0.00")),
            TransactionType.ESCROW_HOLD,
        )
    )
    with pytest.raises(InsufficientFundsError):
        await MoneyService(repo).post_group(legs=legs, correlation_id=uuid4())
    assert (wallet.balance, wallet.held_balance) == (Decimal("100.00"), Decimal("80.00"))
    repo.append_transaction.assert_not_called()


@pytest.mark.parametrize("own_hold", [Decimal("25.00"), Decimal("80.00")])
async def test_operation_hold_is_consumed_once_without_touching_another_hold(own_hold):
    wallet_id, counter_id = uuid4(), uuid4()
    wallet = SimpleNamespace(
        user_id=uuid4(), balance=Decimal("100.00"), held_balance=Decimal("100.00"), version=1
    )
    counter = SimpleNamespace(
        user_id=None, balance=Decimal("0.00"), held_balance=Decimal("0.00"), version=1
    )
    repo = AsyncMock()
    repo.savepoint = Mock(side_effect=_savepoint)
    repo.append_transaction = Mock()
    repo.idempotency_key_exists.return_value = False
    repo.lock_wallets.return_value = {wallet_id: wallet, counter_id: counter}
    money = MoneyService(repo)
    legs = [
        Leg(wallet_id, -own_hold, TransactionType.PAYMENT, idempotency_key="operation"),
        Leg(counter_id, own_hold, TransactionType.ESCROW_HOLD),
    ]
    assert await money.post_group(
        legs=legs, correlation_id=uuid4(), held_wallet_id=wallet_id, held_amount=own_hold
    )
    assert wallet.balance == Decimal("100.00") - own_hold
    assert wallet.held_balance == Decimal("100.00") - own_hold
    repo.idempotency_key_exists.return_value = True
    assert not await money.post_group(
        legs=legs, correlation_id=uuid4(), held_wallet_id=wallet_id, held_amount=own_hold
    )
    assert wallet.held_balance == Decimal("100.00") - own_hold


async def test_replay_after_wallet_lock_does_not_debit_or_release_hold():
    wallet_id, counter_id = uuid4(), uuid4()
    repo = AsyncMock()
    repo.savepoint = Mock(side_effect=_savepoint)
    repo.append_transaction = Mock()
    repo.idempotency_key_exists.side_effect = [False, True]
    repo.lock_wallets.return_value = {}
    assert not await MoneyService(repo).post_group(
        legs=[
            Leg(
                wallet_id, Decimal("-25.00"), TransactionType.PAYMENT, idempotency_key="already-won"
            ),
            Leg(counter_id, Decimal("25.00"), TransactionType.ESCROW_HOLD),
        ],
        correlation_id=uuid4(),
        held_wallet_id=wallet_id,
        held_amount=Decimal("25.00"),
    )
    repo.append_transaction.assert_not_called()

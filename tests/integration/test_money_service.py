"""Tests for the money/ledger service (SPEC SECTION 20, 24).

Covers the double-entry primitive, the golden top-up and approval-payout groups, the
non-negative overdraft guard, reconciliation, a ledger property test (random balanced
groups keep every wallet's balance equal to its settled sum and every correlation group
summed to zero), and a concurrency test (parallel idempotent posts credit exactly once).
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.core.config import Settings
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import InsufficientFundsError
from app.models import PaymentIntent, User, Wallet
from app.models.enums import (
    PaymentIntentStatus,
    PaymentPurpose,
    TransactionStatus,
    TransactionType,
    UserRole,
    WalletType,
)
from app.repositories.wallet_repository import WalletRepository
from app.services.money_service import LedgerImbalanceError, Leg, MoneyService
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import make_test_settings


def _settings() -> Settings:
    overrides: dict[str, object] = {}
    if os.environ.get("DATABASE_URL"):
        overrides["DATABASE_URL"] = os.environ["DATABASE_URL"]
    if os.environ.get("REDIS_URL"):
        overrides["REDIS_URL"] = os.environ["REDIS_URL"]
    return make_test_settings(**overrides)


async def _make_user_wallet(db: AsyncSession, wtype: WalletType) -> Wallet:
    user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db.add(user)
    await db.flush()
    wallet = Wallet(user_id=user.id, type=wtype)
    db.add(wallet)
    await db.flush()
    return wallet


async def _make_topup_intent(
    db: AsyncSession, user_id: uuid.UUID, amount: Decimal
) -> PaymentIntent:
    intent = PaymentIntent(
        user_id=user_id,
        purpose=PaymentPurpose.WALLET_TOPUP,
        amount=amount,
        status=PaymentIntentStatus.NEW,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db.add(intent)
    await db.flush()
    return intent


async def test_credit_topup_golden(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    before_gateway = gateway.balance
    intent = await _make_topup_intent(db_session, wallet.user_id, Decimal("300.00"))

    posted = await service.credit_topup(
        user_wallet_id=wallet.id, amount=Decimal("300.00"), intent_id=intent.id
    )
    assert posted is True
    await db_session.refresh(wallet)
    await db_session.refresh(gateway)
    assert wallet.balance == Decimal("300.00")
    assert gateway.balance == before_gateway - Decimal("300.00")


async def test_topup_is_idempotent(db_session: AsyncSession) -> None:
    service = MoneyService(WalletRepository(db_session))
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    intent = await _make_topup_intent(db_session, wallet.user_id, Decimal("300.00"))
    assert (
        await service.credit_topup(
            user_wallet_id=wallet.id, amount=Decimal("300.00"), intent_id=intent.id
        )
        is True
    )
    # Replaying the same intent is a no-op.
    assert (
        await service.credit_topup(
            user_wallet_id=wallet.id, amount=Decimal("300.00"), intent_id=intent.id
        )
        is False
    )
    await db_session.refresh(wallet)
    assert wallet.balance == Decimal("300.00")


async def test_approval_payout_group_golden(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    escrow = await repo.get_system(WalletType.SYSTEM_ESCROW)
    revenue = await repo.get_system(WalletType.SYSTEM_REVENUE)
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    courier = await _make_user_wallet(db_session, WalletType.COURIER)

    # Fund escrow (gateway -> escrow) so it holds the 570.00 to release.
    await service.post_group(
        correlation_id=uuid.uuid4(),
        legs=[
            Leg(wallet_id=gateway.id, amount=Decimal("-570.00"), txn_type=TransactionType.PAYMENT),
            Leg(wallet_id=escrow.id, amount=Decimal("570.00"), txn_type=TransactionType.PAYMENT),
        ],
    )
    await db_session.refresh(escrow)
    escrow_start = escrow.balance

    # Approval payout: escrow -570.00 -> courier 540 + revenue 30.
    await service.post_group(
        correlation_id=uuid.uuid4(),
        legs=[
            Leg(
                wallet_id=escrow.id,
                amount=Decimal("-570.00"),
                txn_type=TransactionType.ESCROW_RELEASE,
            ),
            Leg(
                wallet_id=courier.id,
                amount=Decimal("540.00"),
                txn_type=TransactionType.ESCROW_RELEASE,
            ),
            Leg(wallet_id=revenue.id, amount=Decimal("30.00"), txn_type=TransactionType.COMMISSION),
        ],
    )
    await db_session.refresh(escrow)
    await db_session.refresh(courier)
    assert escrow.balance == escrow_start - Decimal("570.00")
    assert courier.balance == Decimal("540.00")

    report = await service.reconcile()
    assert report.ok, report.drifts


async def test_imbalanced_group_is_rejected(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    with pytest.raises(LedgerImbalanceError):
        await service.post_group(
            correlation_id=uuid.uuid4(),
            legs=[
                Leg(wallet_id=wallet.id, amount=Decimal("300.00"), txn_type=TransactionType.TOPUP),
                Leg(
                    wallet_id=gateway.id, amount=Decimal("-299.00"), txn_type=TransactionType.TOPUP
                ),
            ],
        )


async def test_overdraft_of_user_wallet_is_blocked(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    with pytest.raises(InsufficientFundsError):
        await service.post_group(
            correlation_id=uuid.uuid4(),
            legs=[
                Leg(
                    wallet_id=wallet.id, amount=Decimal("-50.00"), txn_type=TransactionType.PAYMENT
                ),
                Leg(
                    wallet_id=gateway.id, amount=Decimal("50.00"), txn_type=TransactionType.PAYMENT
                ),
            ],
        )


async def test_ledger_property_random_groups_keep_invariants(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    a = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    b = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    rng = random.Random(1234)

    for _ in range(40):
        amount = Decimal(rng.randint(1, 5000)) / Decimal(100)
        target = rng.choice([a, b])
        # Bound transfers by the live DB balance so a user wallet never goes negative
        # (post_group updates the wallet object in place, so target.balance is current).
        if rng.random() < 0.5 or target.balance < amount:
            await service.post_group(
                correlation_id=uuid.uuid4(),
                legs=[
                    Leg(wallet_id=gateway.id, amount=-amount, txn_type=TransactionType.TOPUP),
                    Leg(wallet_id=target.id, amount=amount, txn_type=TransactionType.TOPUP),
                ],
            )
        else:
            other = b if target is a else a
            await service.post_group(
                correlation_id=uuid.uuid4(),
                legs=[
                    Leg(wallet_id=target.id, amount=-amount, txn_type=TransactionType.PAYMENT),
                    Leg(wallet_id=other.id, amount=amount, txn_type=TransactionType.PAYMENT),
                ],
            )

    # The invariant: every wallet's balance equals the sum of its settled transactions.
    for wallet in (a, b, gateway):
        await db_session.refresh(wallet)
        assert wallet.balance == await repo.settled_balance(wallet.id)
    report = await service.reconcile()
    assert report.ok, report.drifts


# --- Concurrency: parallel idempotent posts credit exactly once -------------


@pytest_asyncio.fixture
async def committing_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = _settings()
    engine = build_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.rollback()
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")
    yield build_session_factory(engine)
    await engine.dispose()


async def test_parallel_topups_credit_exactly_once(
    committing_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Seed a committed user, wallet, and top-up intent.
    async with committing_factory() as s:
        user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
        s.add(user)
        await s.flush()
        wallet = Wallet(user_id=user.id, type=WalletType.CUSTOMER)
        s.add(wallet)
        await s.flush()
        intent = PaymentIntent(
            user_id=user.id,
            purpose=PaymentPurpose.WALLET_TOPUP,
            amount=Decimal("300.00"),
            status=PaymentIntentStatus.NEW,
            expires_at=datetime.now(UTC) + timedelta(hours=48),
        )
        s.add(intent)
        await s.commit()
        wallet_id = wallet.id
        user_id = user.id
        intent_id = intent.id

    async def attempt() -> bool:
        async with committing_factory() as session:
            service = MoneyService(WalletRepository(session))
            try:
                posted = await service.credit_topup(
                    user_wallet_id=wallet_id, amount=Decimal("300.00"), intent_id=intent_id
                )
                await session.commit()
                return posted
            except IntegrityError:
                await session.rollback()
                return False

    results = await asyncio.gather(*[attempt() for _ in range(10)])
    # Exactly one attempt actually posted the credit; the rest were replays or lost the
    # unique-key race. The committed group is balanced, so it is left in place (the
    # ledger is append-only — its rows must never be deleted).
    assert sum(1 for r in results if r) == 1
    async with committing_factory() as s:
        w = await s.get(Wallet, wallet_id)
        assert w is not None
        assert w.balance == Decimal("300.00")
        report = await MoneyService(WalletRepository(s)).reconcile()
        assert report.ok, report.drifts
    # user_id is retained by the committed (balanced) ledger rows; nothing to clean up.
    _ = user_id


async def test_available_balance_and_no_wallet(db_session: AsyncSession) -> None:
    service = MoneyService(WalletRepository(db_session))
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    assert await service.available_balance(wallet.user_id) == Decimal("0.00")
    # A user with no wallet reports zero available.
    assert await service.available_balance(uuid.uuid4()) == Decimal("0.00")


async def test_reconcile_detects_drift(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    # Append a lone SETTLED entry WITHOUT updating the wallet balance -> the invariant
    # (balance == sum of settled) and the correlation zero-sum are both violated.
    repo.append_transaction(
        wallet_id=wallet.id,
        amount=Decimal("10.00"),
        txn_type=TransactionType.TOPUP,
        status=TransactionStatus.SETTLED,
        correlation_id=uuid.uuid4(),
        balance_after=Decimal("10.00"),
    )
    await db_session.flush()
    report = await service.reconcile()
    assert not report.ok
    assert any("wallet" in d for d in report.drifts)


async def test_run_reconciliation_job(committing_factory: async_sessionmaker[AsyncSession]) -> None:
    # Uses the committed DB; the run builds its own engine from settings.
    from app.workers.reconciliation import run_reconciliation

    report = await run_reconciliation()
    assert report.wallets_checked >= 4  # at least the four system wallets


async def test_reconciliation_counts_and_exact_discrepancies(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    service = MoneyService(repo)
    baseline = await service.reconcile()
    assert baseline.ok, baseline.drifts
    await _make_user_wallet(db_session, WalletType.CUSTOMER)
    empty_wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    empty_wallet.balance = Decimal("0.01")
    settled_wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    settled_wallet.balance = Decimal("10.01")
    correlation_id = uuid.uuid4()
    for status, amount in [
        (TransactionStatus.SETTLED, Decimal("10.00")),
        (TransactionStatus.SETTLED, Decimal("0.01")),
        (TransactionStatus.PENDING, Decimal("99.00")),
        (TransactionStatus.REVERSED, Decimal("7.00")),
    ]:
        repo.append_transaction(
            wallet_id=settled_wallet.id,
            amount=amount,
            txn_type=TransactionType.TOPUP,
            status=status,
            correlation_id=correlation_id if status == TransactionStatus.SETTLED else uuid.uuid4(),
            balance_after=settled_wallet.balance,
        )
    await db_session.flush()
    statements: list[str] = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = await db_session.connection()
    event.listen(connection.sync_connection, "before_cursor_execute", record_statement)
    try:
        report = await service.reconcile()
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record_statement)

    assert len(statements) == 1
    assert report.wallets_checked == baseline.wallets_checked + 3
    assert report.correlations_checked == baseline.correlations_checked + 1
    assert report.drifts == [
        f"wallet {empty_wallet.id} balance 0.01 != settled sum 0.00",
        f"correlation {correlation_id} settled sum 10.01 != 0.00",
    ]


async def test_reconciliation_ignores_cached_wallet_balance(db_session: AsyncSession) -> None:
    repo = WalletRepository(db_session)
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    service = MoneyService(repo)
    await service.post_group(
        correlation_id=uuid.uuid4(),
        legs=[
            Leg(wallet_id=gateway.id, amount=Decimal("-0.01"), txn_type=TransactionType.TOPUP),
            Leg(wallet_id=wallet.id, amount=Decimal("0.01"), txn_type=TransactionType.TOPUP),
        ],
    )
    # A loaded ORM instance must not override the balance in the statement snapshot.
    from sqlalchemy.orm.attributes import set_committed_value

    set_committed_value(wallet, "balance", Decimal("0.00"))
    report = await service.reconcile()
    assert report.ok, report.drifts


async def test_reconciliation_snapshot_survives_post_between_reads(
    committing_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with committing_factory() as setup:
        wallet = await _make_user_wallet(setup, WalletType.CUSTOMER)
        gateway = await WalletRepository(setup).get_system(WalletType.SYSTEM_GATEWAY)
        wallet_id, gateway_id = wallet.id, gateway.id
        await setup.commit()

    async with committing_factory() as reader:
        baseline = await MoneyService(WalletRepository(reader)).reconcile()
        assert baseline.ok, baseline.drifts
        await reader.rollback()
        posted = False

        async def post_after_read(original, *args, **kwargs):
            nonlocal posted
            result = await original(*args, **kwargs)
            if not posted:
                posted = True
                async with committing_factory() as writer:
                    await MoneyService(WalletRepository(writer)).post_group(
                        correlation_id=uuid.uuid4(),
                        legs=[
                            Leg(
                                wallet_id=gateway_id,
                                amount=Decimal("-0.01"),
                                txn_type=TransactionType.TOPUP,
                            ),
                            Leg(
                                wallet_id=wallet_id,
                                amount=Decimal("0.01"),
                                txn_type=TransactionType.TOPUP,
                            ),
                        ],
                    )
                    await writer.commit()
            return result

        original_execute, original_scalars = reader.execute, reader.scalars

        async def execute(*args, **kwargs):
            return await post_after_read(original_execute, *args, **kwargs)

        async def scalars(*args, **kwargs):
            return await post_after_read(original_scalars, *args, **kwargs)

        monkeypatch.setattr(reader, "execute", execute)
        monkeypatch.setattr(reader, "scalars", scalars)
        report = await MoneyService(WalletRepository(reader)).reconcile()
        assert posted
        assert report.ok, report.drifts
        assert report.wallets_checked == baseline.wallets_checked
        assert report.correlations_checked == baseline.correlations_checked

    async with committing_factory() as verifier:
        after = await MoneyService(WalletRepository(verifier)).reconcile()
        assert after.ok, after.drifts
        assert after.correlations_checked == baseline.correlations_checked + 1


async def test_wallet_debit_waits_for_concurrent_reservation_and_rejects_stale_available(
    committing_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with committing_factory() as setup:
        wallet = await _make_user_wallet(setup, WalletType.CUSTOMER)
        repo = WalletRepository(setup)
        gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
        intent = await _make_topup_intent(setup, wallet.user_id, Decimal("100.00"))
        await MoneyService(repo).credit_topup(
            user_wallet_id=wallet.id,
            amount=Decimal("100.00"),
            intent_id=intent.id,
        )
        wallet_id, user_id, gateway_id = wallet.id, wallet.user_id, gateway.id
        await setup.commit()

    async with committing_factory() as payer, committing_factory() as reserver:
        payer_repo = WalletRepository(payer)
        payer_money = MoneyService(payer_repo)
        assert await payer_money.available_balance(user_id) == Decimal("100.00")
        await MoneyService(WalletRepository(reserver)).hold_funds(
            wallet_id=wallet_id,
            amount=Decimal("80.00"),
        )
        locking = asyncio.Event()
        original_lock = payer_repo.lock_wallets

        async def signal_lock(wallet_ids):
            locking.set()
            return await original_lock(wallet_ids)

        monkeypatch.setattr(payer_repo, "lock_wallets", signal_lock)
        debit = asyncio.create_task(
            payer_money.post_group(
                correlation_id=uuid.uuid4(),
                legs=[
                    Leg(wallet_id, Decimal("-80.00"), TransactionType.PAYMENT),
                    Leg(gateway_id, Decimal("80.00"), TransactionType.PAYMENT),
                ],
            )
        )
        await asyncio.wait_for(locking.wait(), timeout=5)
        assert not debit.done()
        await reserver.commit()
        with pytest.raises(InsufficientFundsError):
            await asyncio.wait_for(debit, timeout=5)
        await payer.rollback()
        refreshed = await payer_repo.get_by_user(user_id)
        assert refreshed is not None
        assert (refreshed.balance, refreshed.held_balance) == (Decimal("100.00"), Decimal("80.00"))
        assert (await payer_money.reconcile()).ok


async def test_legacy_payment_consumes_only_own_hold_and_replay_preserves_others(
    db_session: AsyncSession,
) -> None:
    repo = WalletRepository(db_session)
    money = MoneyService(repo)
    wallet = await _make_user_wallet(db_session, WalletType.CUSTOMER)
    intent = await _make_topup_intent(db_session, wallet.user_id, Decimal("100.00"))
    await money.credit_topup(
        user_wallet_id=wallet.id, amount=Decimal("100.00"), intent_id=intent.id
    )
    await money.hold_funds(wallet_id=wallet.id, amount=Decimal("100.00"))
    gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
    legs = [
        Leg(
            wallet.id,
            Decimal("-25.00"),
            TransactionType.PAYMENT,
            idempotency_key=f"legacy:{uuid.uuid4()}",
        ),
        Leg(gateway.id, Decimal("25.00"), TransactionType.PAYMENT),
    ]
    for expected in (True, False):
        assert (
            await money.post_group(
                legs=legs,
                correlation_id=uuid.uuid4(),
                held_wallet_id=wallet.id,
                held_amount=Decimal("25.00"),
            )
            is expected
        )
        await db_session.refresh(wallet)
        assert (wallet.balance, wallet.held_balance) == (Decimal("75.00"), Decimal("75.00"))
    assert (await money.reconcile()).ok


async def test_distinct_invoice_payment_cannot_consume_gateway_payment_reservation(
    committing_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from app.models import Invoice, Order
    from app.models.enums import InvoiceStatus, OrderStatus

    from tests.integration.test_payment_service import _issued_invoice, _service

    async with committing_factory() as setup:
        user, _, first_invoice = await _issued_invoice(setup)
        _, second_order, second_invoice = await _issued_invoice(setup)
        second_order.customer_id = user.id
        second_invoice.items_net_amount = Decimal("1000.00")
        second_invoice.courier_fee_amount = Decimal("0.00")
        second_invoice.service_fee_amount = Decimal("0.00")
        second_invoice.net_after_discount_amount = Decimal("1000.00")
        second_invoice.total_amount = Decimal("1000.00")
        repo = WalletRepository(setup)
        wallet = await repo.get_by_user(user.id)
        assert wallet is not None
        topup = await _make_topup_intent(setup, user.id, Decimal("630.00"))
        await MoneyService(repo).credit_topup(
            user_wallet_id=wallet.id,
            amount=Decimal("630.00"),
            intent_id=topup.id,
        )
        user_id = user.id
        first_invoice_id, second_invoice_id = first_invoice.id, second_invoice.id
        await setup.commit()

    async with committing_factory() as payer, committing_factory() as reserver:
        service = _service(payer, AsyncMock())
        read_available, reserved = asyncio.Event(), asyncio.Event()
        original_available = service._money.available_balance

        async def stale_available(user_id):
            available = await original_available(user_id)
            read_available.set()
            await reserved.wait()
            return available

        monkeypatch.setattr(service._money, "available_balance", stale_available)
        payment = asyncio.create_task(
            service.pay_invoice(
                invoice_id=first_invoice_id,
                customer_id=user_id,
            )
        )
        await asyncio.wait_for(read_available.wait(), timeout=5)
        pending = await _service(reserver, AsyncMock()).pay_invoice(
            invoice_id=second_invoice_id,
            customer_id=user_id,
        )
        assert pending.status == "PENDING"
        assert pending.amount_from_wallet == Decimal("630.00")
        assert pending.amount_from_gateway == Decimal("370.00")
        await reserver.commit()
        reserved.set()
        with pytest.raises(InsufficientFundsError):
            await asyncio.wait_for(payment, timeout=5)
        await payer.rollback()
        wallet = await WalletRepository(payer).get_by_user(user_id)
        assert wallet is not None
        assert (wallet.balance, wallet.held_balance) == (Decimal("630.00"), Decimal("630.00"))
        first = await payer.get(Invoice, first_invoice_id)
        assert first is not None and first.status is InvoiceStatus.ISSUED
        order = await payer.get(Order, first.order_id)
        assert order is not None and order.status is OrderStatus.WAITING_PAYMENT
        assert (await service._money.reconcile()).ok

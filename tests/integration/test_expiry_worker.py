"""Expiry sweeper: a lapsed unpaid invoice reopens its order and releases the hold."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.config import Settings
from app.core.db import build_engine, build_session_factory
from app.core.redis import build_redis
from app.integrations.payments.fake import FakePaymentClient
from app.models import Invoice, Order, PaymentIntent, User, Wallet
from app.models.enums import (
    InvoiceStatus,
    OrderStatus,
    PaymentIntentStatus,
    PaymentPurpose,
    TransactionType,
    UserRole,
    WalletType,
)
from app.repositories.payment_repository import PaymentRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.expiry_service import ExpiryService
from app.services.money_service import Leg, MoneyService
from app.services.payment_service import WebhookEvent, build_payment_service
from app.workers.expiry import expire_stale
from geoalchemy2 import WKTElement
from sqlalchemy import select

from tests.conftest import make_test_settings


def _settings() -> Settings:
    overrides: dict[str, object] = {}
    if os.environ.get("DATABASE_URL"):
        overrides["DATABASE_URL"] = os.environ["DATABASE_URL"]
    if os.environ.get("REDIS_URL"):
        overrides["REDIS_URL"] = os.environ["REDIS_URL"]
    return make_test_settings(**overrides)


async def test_expiry_reopens_order_and_releases_hold() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as s:
            await s.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")

    try:
        async with factory() as session:
            customer = User(
                phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER
            )
            courier = User(
                phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.COURIER
            )
            session.add_all([customer, courier])
            await session.flush()
            wallet = Wallet(user_id=customer.id, type=WalletType.CUSTOMER)
            session.add(wallet)
            await session.flush()
            # Fund the wallet through the ledger (gateway -> wallet) so the balance ==
            # settled-sum invariant holds, then place a 300 hold against the pending pay.
            repo = WalletRepository(session)
            money = MoneyService(repo)
            gateway = await repo.get_system(WalletType.SYSTEM_GATEWAY)
            await money.post_group(
                correlation_id=uuid.uuid4(),
                legs=[
                    Leg(
                        wallet_id=gateway.id,
                        amount=Decimal("-1000.00"),
                        txn_type=TransactionType.TOPUP,
                    ),
                    Leg(
                        wallet_id=wallet.id,
                        amount=Decimal("1000.00"),
                        txn_type=TransactionType.TOPUP,
                    ),
                ],
            )
            await money.hold_funds(wallet_id=wallet.id, amount=Decimal("300.00"))
            order = Order(
                customer_id=customer.id,
                courier_id=courier.id,
                delivery_city="Jeddah",
                delivery_location=WKTElement("POINT(39.2 21.5)", srid=4326),
                delivery_date=datetime.now(UTC).date() + timedelta(days=5),
                status=OrderStatus.WAITING_PAYMENT,
                total_amount=Decimal("724.50"),
            )
            session.add(order)
            await session.flush()
            invoice = Invoice(
                order_id=order.id,
                issued_by_courier_id=courier.id,
                status=InvoiceStatus.ISSUED,
                items_net_amount=Decimal("500.00"),
                courier_fee_amount=Decimal("100.00"),
                service_fee_amount=Decimal("30.00"),
                discount_amount=Decimal("0.00"),
                net_after_discount_amount=Decimal("630.00"),
                tax_amount=Decimal("94.50"),
                total_amount=Decimal("724.50"),
                amount_from_wallet=Decimal("300.00"),
                amount_from_gateway=Decimal("424.50"),
                issued_at=datetime.now(UTC) - timedelta(hours=50),
                expires_at=datetime.now(UTC) - timedelta(hours=1),  # already lapsed
            )
            session.add(invoice)
            await session.flush()
            intent = PaymentIntent(
                user_id=customer.id,
                purpose=PaymentPurpose.ORDER_INVOICE,
                amount=Decimal("424.50"),
                wallet_reserved_amount=Decimal("300.00"),
                status=PaymentIntentStatus.NEW,
                reference_invoice_id=invoice.id,
                gateway_reference=f"LINK-{uuid.uuid4().hex[:10]}",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(intent)
            await session.commit()
            order_id, invoice_id, intent_id = order.id, invoice.id, intent.id

        invoices_expired, _intents = await expire_stale(factory=factory, settings=settings)
        assert invoices_expired >= 1

        async with factory() as session:
            inv = await session.get(Invoice, invoice_id)
            assert inv is not None and inv.status is InvoiceStatus.EXPIRED
            order = await session.get(Order, order_id)
            assert order is not None and order.status is OrderStatus.ASSIGNED
            pi = await session.get(PaymentIntent, intent_id)
            assert pi is not None and pi.status is PaymentIntentStatus.EXPIRED
            # The 300 hold was released (balance untouched, held back to 0).
            wallet = await WalletRepository(session).get_by_user(order.customer_id)
            assert wallet is not None
            assert wallet.balance == Decimal("1000.00")
            assert wallet.held_balance == Decimal("0.00")
    finally:
        await engine.dispose()


async def test_expiry_builds_own_engine_and_scheduled_task() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as s:
            await s.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"database unavailable: {exc}")
    await engine.dispose()

    # No factory injected: the sweeper builds and disposes its own engine.
    invoices, intents = await expire_stale(settings=settings)
    assert invoices >= 0 and intents >= 0

    from app.workers.expiry import run_expire_stale

    # The scheduled entry point acquires a Redis lock, sweeps, and releases it.
    await run_expire_stale()


async def test_topup_settles_between_expiry_selection_and_lock(monkeypatch) -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    redis = build_redis(settings)
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()

        await redis.aclose()
        pytest.skip(f"database unavailable: {exc}")
    try:
        async with factory() as session:
            user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
            session.add(user)
            await session.flush()
            wallet = Wallet(user_id=user.id, type=WalletType.CUSTOMER)
            session.add(wallet)
            await session.flush()
            service = build_payment_service(
                session=session,
                gateway=FakePaymentClient(settings.ENVIRONMENT),
                redis=redis,
                settings=settings,
            )
            result = await service.create_topup(user_id=user.id, amount=Decimal("100.00"))
            intent = await PaymentRepository(session).get_intent(result.intent_id)
            assert intent is not None and intent.gateway_reference is not None
            intent.expires_at = datetime.now(UTC) - timedelta(hours=1)
            intent_id, wallet_id, reference = intent.id, wallet.id, intent.gateway_reference
            await session.commit()

        lock_started, settlement_done = asyncio.Event(), asyncio.Event()
        original_lock = PaymentRepository.lock_intent

        async def wait_for_settlement(repository, payment_intent_id):
            lock_started.set()
            await asyncio.wait_for(settlement_done.wait(), timeout=5)
            return await original_lock(repository, payment_intent_id)

        monkeypatch.setattr(PaymentRepository, "lock_intent", wait_for_settlement)

        async def expire_candidate() -> bool:
            async with factory() as session:
                # Keep the stale NEW identity in the session to verify locked refresh.
                candidate = await PaymentRepository(session).get_intent(intent_id)
                assert candidate is not None and candidate.status is PaymentIntentStatus.NEW
                expired = await ExpiryService(session).expire_topup_intent(intent_id)
                await session.commit()
                assert candidate.status is PaymentIntentStatus.PAID
                return expired

        async def settle_candidate() -> None:
            await asyncio.wait_for(lock_started.wait(), timeout=5)
            async with factory() as session:
                service = build_payment_service(
                    session=session,
                    gateway=FakePaymentClient(settings.ENVIRONMENT),
                    redis=redis,
                    settings=settings,
                )
                await service._settle_locked(WebhookEvent(reference, "PAID", Decimal("100.00")))
                await session.commit()
            settlement_done.set()

        expired, _ = await asyncio.wait_for(
            asyncio.gather(expire_candidate(), settle_candidate()), timeout=10
        )
        assert not expired
        async with factory() as session:
            intent = await session.get(PaymentIntent, intent_id)
            wallet = await session.get(Wallet, wallet_id)
            assert intent is not None and intent.status is PaymentIntentStatus.PAID
            assert wallet is not None and wallet.balance == Decimal("100.00")
            assert wallet.held_balance == Decimal("0.00")
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_concurrent_reservation_releases_refresh_wallet_after_lock() -> None:
    settings = _settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    redis = build_redis(settings)
    try:
        async with factory() as session:
            await session.execute(select(User.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        await redis.aclose()
        pytest.skip(f"database unavailable: {exc}")
    try:
        async with factory() as session:
            user = User(phone=f"+96650{uuid.uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
            session.add(user)
            await session.flush()
            wallet = Wallet(user_id=user.id, type=WalletType.CUSTOMER)
            session.add(wallet)
            await session.flush()
            payments = PaymentRepository(session)
            intent = await payments.create_intent(
                user_id=user.id,
                purpose=PaymentPurpose.WALLET_TOPUP,
                amount=Decimal("100.00"),
                reference_invoice_id=None,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            money = MoneyService(WalletRepository(session))
            await money.credit_topup(
                user_wallet_id=wallet.id, amount=intent.amount, intent_id=intent.id
            )
            await payments.mark_paid(intent, paid_at=datetime.now(UTC))
            await money.hold_funds(wallet_id=wallet.id, amount=Decimal("100.00"))
            user_id, wallet_id = user.id, wallet.id
            await session.commit()

        async with factory() as first, factory() as second:
            first_money = MoneyService(WalletRepository(first))
            second_wallet = await WalletRepository(second).get_by_user(user_id)
            assert second_wallet is not None and second_wallet.held_balance == Decimal("100.00")
            await first_money.release_hold(wallet_id=wallet_id, amount=Decimal("40.00"))
            pending_release = asyncio.create_task(
                MoneyService(WalletRepository(second)).release_hold(
                    wallet_id=wallet_id, amount=Decimal("60.00")
                )
            )
            await first.commit()
            await asyncio.wait_for(pending_release, timeout=5)
            await second.commit()
            await second.refresh(second_wallet)
            assert second_wallet.balance == Decimal("100.00")
            assert second_wallet.held_balance == Decimal("0.00")
    finally:
        await redis.aclose()
        await engine.dispose()

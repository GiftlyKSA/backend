"""Explicit, idempotent development fixtures; never contact external providers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, Settings, get_migration_settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.core.logging import configure_logging
from app.core.money import ZERO
from app.core.pricing import (
    PricingConfig,
    PricingItem,
    calculate_invoice_totals,
    compute_settlement,
)
from app.models import (
    City,
    Conversation,
    CourierProfile,
    Dispute,
    Order,
    PaymentIntent,
    User,
    Wallet,
    WalletTopup,
)
from app.models.enums import (
    InvoiceStatus,
    OrderStatus,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentPurpose,
    UserRole,
    UserStatus,
    WalletType,
)
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.wallet_repository import WalletRepository
from app.seed_development import DEVELOPMENT_COURIERS, seed_couriers_in_session
from app.services.money_service import MoneyService

DEMO_COURIERS = (
    *DEVELOPMENT_COURIERS,
    ("+966555555557", "RUH"),
    ("+966555555558", "JED"),
    ("+966555555559", "RUH"),
)
DEMO_CUSTOMERS = ("+966555555551", "+966555555552", "+966555555553")
_NAMESPACE = uuid.UUID("3dac3139-a00d-4278-a2b5-218f1efe9740")
_logger = logging.getLogger(__name__)


def _fixture_id(label: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, label)


async def _fund_customer(
    session: AsyncSession,
    user: User,
    wallet: Wallet,
    money: MoneyService,
    now: datetime,
) -> None:
    intent_id = _fixture_id(f"topup:{user.id}")
    if await session.get(PaymentIntent, intent_id) is not None:
        return
    amount = Decimal("2000.00")
    session.add(
        PaymentIntent(
            id=intent_id,
            user_id=user.id,
            purpose=PaymentPurpose.WALLET_TOPUP,
            status=PaymentIntentStatus.PAID,
            amount=amount,
            checkout_provider="SIMULATED",
            checkout_state="CLOSED",
            expires_at=now + timedelta(hours=1),
            paid_at=now,
        )
    )
    await session.flush()
    session.add(
        WalletTopup(
            user_id=user.id,
            wallet_id=wallet.id,
            payment_intent_id=intent_id,
            amount=amount,
        )
    )
    await money.credit_topup(user_wallet_id=wallet.id, amount=amount, intent_id=intent_id)


async def _create_invoice(
    session: AsyncSession,
    settings: Settings,
    order: Order,
    status: OrderStatus,
    wallet: Wallet,
    courier_wallet: Wallet,
    now: datetime,
    index: int,
) -> None:
    assert order.courier_id is not None
    result = calculate_invoice_totals(
        items=[
            PricingItem("Demo gift", Decimal(80 + index * 5), 1, position=1),
            PricingItem("Demo wrapping", Decimal("10.00"), 2, position=2),
        ],
        courier_fee_amount=Decimal("25.00"),
        promo=None,
        cfg=PricingConfig(
            service_fee_rate=settings.SERVICE_FEE_RATE,
            service_fee_min_amount=settings.SERVICE_FEE_MIN_AMOUNT,
            service_fee_max_amount=settings.SERVICE_FEE_MAX_AMOUNT,
            max_invoice_amount=settings.MAX_INVOICE_AMOUNT,
        ),
    )
    invoices = InvoiceRepository(session)
    invoice = await invoices.create_draft(
        order_id=order.id,
        courier_id=order.courier_id,
        result=result,
        promo_id=None,
        promo_code_snapshot=None,
    )
    await invoices.add_items(invoice_id=invoice.id, lines=result.lines)
    await invoices.issue(invoice, issued_at=now, expires_at=now + timedelta(hours=48))
    if status is OrderStatus.ASSIGNED:
        invoice.status = InvoiceStatus.EXPIRED
        invoice.issued_at = now - timedelta(days=3)
        invoice.expires_at = now - timedelta(days=1)
    elif status is OrderStatus.CANCELLED:
        invoice.status = InvoiceStatus.CANCELLED
    else:
        settlement = compute_settlement(
            items_net_amount=result.items_net_amount,
            courier_fee_amount=result.courier_fee_amount,
            total_amount=result.total_amount,
            commission_rate=settings.PLATFORM_COMMISSION_RATE,
        )
        order.total_amount = result.total_amount
        order.commission_amount = settlement.commission_amount
        order.courier_payout_amount = settlement.courier_payout_amount
        if status is not OrderStatus.WAITING_PAYMENT:
            money = MoneyService(WalletRepository(session))
            await money.fund_escrow_for_invoice(
                customer_wallet_id=wallet.id,
                wallet_amount=result.total_amount,
                gateway_amount=ZERO,
                invoice_id=invoice.id,
                order_id=order.id,
                intent_id=None,
                was_held=False,
            )
            invoice.status = InvoiceStatus.PAID
            invoice.payment_method = PaymentMethod.WALLET_ONLY
            invoice.amount_from_wallet = result.total_amount
            invoice.paid_at = now
            if status is OrderStatus.COMPLETED:
                await money.release_escrow_on_completion(
                    order_id=order.id,
                    invoice_id=invoice.id,
                    courier_wallet_id=courier_wallet.id,
                    courier_payout_amount=settlement.courier_payout_amount,
                    platform_revenue_amount=settlement.platform_revenue_amount,
                )
            elif status is OrderStatus.REFUNDED:
                await money.refund_escrow(
                    order_id=order.id,
                    invoice_id=invoice.id,
                    customer_wallet_id=wallet.id,
                    amount=result.total_amount,
                )
    await session.flush()


async def seed_demo_in_session(session: AsyncSession, settings: Settings) -> int:
    """Create missing synthetic fixtures under the development seed transaction lock."""
    if settings.ENVIRONMENT is not Environment.DEVELOPMENT:
        return 0
    await seed_couriers_in_session(session, settings, couriers=DEMO_COURIERS)
    created_customers = list(
        await session.scalars(
            insert(User)
            .values(
                [
                    {
                        "phone": phone,
                        "role": UserRole.CUSTOMER,
                        "status": UserStatus.ACTIVE,
                        "full_name": f"Development Customer {index + 1}",
                    }
                    for index, phone in enumerate(DEMO_CUSTOMERS)
                ]
            )
            .on_conflict_do_nothing(index_elements=[User.phone])
            .returning(User)
        )
    )
    session.add_all(
        [Wallet(user_id=user.id, type=WalletType.CUSTOMER) for user in created_customers]
    )
    await session.flush()
    users = {
        user.phone: user
        for user in await session.scalars(
            select(User)
            .where(User.phone.in_([*DEMO_CUSTOMERS, *dict(DEMO_COURIERS)]))
            .with_for_update()
        )
    }
    wallets = {
        wallet.user_id: wallet
        for wallet in await session.scalars(
            select(Wallet).where(Wallet.user_id.in_([user.id for user in users.values()]))
        )
    }
    profiles = {
        profile.user_id: profile
        for profile in await session.scalars(
            select(CourierProfile).where(
                CourierProfile.user_id.in_([user.id for user in users.values()])
            )
        )
    }
    cities = {
        city.shortcut: city
        for city in await session.scalars(select(City).where(City.shortcut.in_(["RUH", "JED"])))
    }
    existing_ids = set(
        await session.scalars(
            select(Order.id).where(
                Order.id.in_(
                    [
                        _fixture_id(f"order:{phone}:{index}")
                        for phone in DEMO_CUSTOMERS
                        for index in range(10)
                    ]
                )
            )
        )
    )
    now = datetime.now(UTC)
    money = MoneyService(WalletRepository(session))
    created = 0
    for customer_index, phone in enumerate(DEMO_CUSTOMERS):
        customer = users[phone]
        if (
            customer.role is not UserRole.CUSTOMER
            or customer.status is not UserStatus.ACTIVE
            or customer.full_name != f"Development Customer {customer_index + 1}"
            or customer.id not in wallets
        ):
            continue
        city = cities["JED" if customer_index == 1 else "RUH"]
        couriers = [
            users[courier_phone]
            for courier_phone, shortcut in DEMO_COURIERS
            if shortcut == city.shortcut
            and users[courier_phone].role is UserRole.COURIER
            and users[courier_phone].status is UserStatus.ACTIVE
            and users[courier_phone].id in wallets
            and users[courier_phone].id in profiles
            and profiles[users[courier_phone].id].is_verified
            and profiles[users[courier_phone].id].city_of_residence_id == city.id
        ]
        if not couriers:
            continue
        wallet = wallets[customer.id]
        await _fund_customer(session, customer, wallet, money, now)
        statuses = [
            OrderStatus.NEW,
            OrderStatus.ASSIGNED,
            OrderStatus.WAITING_PAYMENT,
            (OrderStatus.IN_PROGRESS, OrderStatus.DELIVERED, OrderStatus.DISPUTED)[customer_index],
            OrderStatus.COMPLETED,
            OrderStatus.COMPLETED,
            OrderStatus.COMPLETED,
            OrderStatus.CANCELLED,
            OrderStatus.CANCELLED,
            OrderStatus.REFUNDED,
        ]
        for index, status in enumerate(statuses):
            order_id = _fixture_id(f"order:{phone}:{index}")
            if order_id in existing_ids:
                continue
            courier = couriers[index % len(couriers)]
            order = Order(
                id=order_id,
                customer_id=customer.id,
                courier_id=None if status is OrderStatus.NEW else courier.id,
                delivery_city_id=city.id,
                delivery_date=now.date() + timedelta(days=index),
                description=f"Development demo gift {customer_index + 1}-{index + 1}",
                status=status,
                assigned_at=None if status is OrderStatus.NEW else now,
                delivered_at=now
                if status
                in {
                    OrderStatus.DELIVERED,
                    OrderStatus.COMPLETED,
                    OrderStatus.DISPUTED,
                    OrderStatus.REFUNDED,
                }
                else None,
                completed_at=now if status is OrderStatus.COMPLETED else None,
                cancelled_reason="Development demo cancellation"
                if status is OrderStatus.CANCELLED
                else None,
            )
            session.add(order)
            await session.flush()
            if status is not OrderStatus.NEW:
                session.add(
                    Conversation(
                        order_id=order.id,
                        customer_id=customer.id,
                        courier_id=courier.id,
                        is_closed=status
                        in {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
                    )
                )
                await _create_invoice(
                    session,
                    settings,
                    order,
                    status,
                    wallet,
                    wallets[courier.id],
                    now,
                    index,
                )
            if status in {OrderStatus.DISPUTED, OrderStatus.REFUNDED}:
                session.add(
                    Dispute(
                        order_id=order.id,
                        raised_by_user_id=customer.id,
                        reason="Development demo dispute",
                        status="OPEN" if status is OrderStatus.DISPUTED else "RESOLVED_CUSTOMER",
                        resolved_at=now if status is OrderStatus.REFUNDED else None,
                        resolution_note="Development demo refund"
                        if status is OrderStatus.REFUNDED
                        else None,
                    )
                )
            created += 1
    await session.flush()
    return created


async def seed_demo() -> int:
    """Run the explicit development seed atomically without any provider clients."""
    if get_migration_settings().ENVIRONMENT is not Environment.DEVELOPMENT:
        return 0
    settings = get_settings()
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as session, session.begin():
            count = await seed_demo_in_session(session, settings)
        _logger.info("Development demo seed completed; created %d orders.", count)
        return count
    finally:
        await engine.dispose()


def main() -> None:
    """Create development demo data only when explicitly requested."""
    configure_logging()
    asyncio.run(seed_demo())


if __name__ == "__main__":
    main()

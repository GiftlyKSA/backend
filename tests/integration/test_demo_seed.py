"""Development demo accounts, invoices and balanced ledger fixtures."""

from unittest.mock import AsyncMock

import pytest
from app.core.config import Environment
from app.models import CourierProfile, Invoice, Order, PaymentIntent, User, Wallet
from app.models.enums import InvoiceStatus, OrderStatus, UserRole, UserStatus
from app.repositories.wallet_repository import WalletRepository
from app.services.money_service import MoneyService
from sqlalchemy import func, select

from tests.conftest import make_test_settings


@pytest.mark.parametrize("environment", ["test", "production"])
async def test_demo_skips_non_development_without_database(environment):
    from app.seed_demo import seed_demo_in_session

    session = AsyncMock()
    assert (
        await seed_demo_in_session(
            session,
            make_test_settings().model_copy(update={"ENVIRONMENT": Environment(environment)}),
        )
        == 0
    )
    session.execute.assert_not_awaited()


async def test_demo_has_three_customers_five_couriers_and_balanced_varied_orders(db_session):
    from app.seed_demo import seed_demo_in_session

    settings = make_test_settings(ENVIRONMENT="development")
    assert await seed_demo_in_session(db_session, settings) == 30
    users = list(
        await db_session.scalars(
            select(User).where(User.phone.in_([f"+96655555555{i}" for i in range(1, 10) if i != 4]))
        )
    )
    assert len(users) == 8
    assert all(user.status is UserStatus.ACTIVE and user.email is None for user in users)
    customers = [user for user in users if user.role is UserRole.CUSTOMER]
    couriers = [user for user in users if user.role is UserRole.COURIER]
    assert len(customers) == 3 and len(couriers) == 5
    for customer in customers:
        assert (
            await db_session.scalar(
                select(func.count()).select_from(Order).where(Order.customer_id == customer.id)
            )
            == 10
        )
    for courier in couriers:
        profile = await db_session.get(CourierProfile, courier.id)
        assert profile.is_verified
        wallet = await db_session.scalar(select(Wallet).where(Wallet.user_id == courier.id))
        assert wallet.balance > 0
        orders = list(await db_session.scalars(select(Order).where(Order.courier_id == courier.id)))
        assert all(order.delivery_city_id == profile.city_of_residence_id for order in orders)
    customer_ids = [user.id for user in customers]
    owned_orders = Order.customer_id.in_(customer_ids)
    assert set(await db_session.scalars(select(Order.status).where(owned_orders))) == set(
        OrderStatus
    )
    assert set(
        await db_session.scalars(
            select(Invoice.status).join(Order, Order.id == Invoice.order_id).where(owned_orders)
        )
    ) == {
        InvoiceStatus.ISSUED,
        InvoiceStatus.PAID,
        InvoiceStatus.CANCELLED,
        InvoiceStatus.EXPIRED,
    }
    assert (await MoneyService(WalletRepository(db_session)).reconcile()).ok
    before = {
        user.id: (await db_session.scalar(select(Wallet).where(Wallet.user_id == user.id))).balance
        for user in users
    }
    couriers[0].status = UserStatus.BANNED
    await db_session.flush()
    assert await seed_demo_in_session(db_session, settings) == 0
    assert couriers[0].status is UserStatus.BANNED
    assert (
        await db_session.scalar(select(func.count()).select_from(Order).where(owned_orders)) == 30
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Invoice)
            .join(Order, Order.id == Invoice.order_id)
            .where(owned_orders)
        )
        == 27
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PaymentIntent)
            .where(PaymentIntent.user_id.in_(customer_ids))
        )
        == 3
    )
    for user in users:
        assert (
            await db_session.scalar(select(Wallet).where(Wallet.user_id == user.id))
        ).balance == before[user.id]


async def test_demo_preserves_existing_accounts_and_never_funds_an_unrelated_customer(db_session):
    from app.seed_demo import seed_demo_in_session

    existing = User(phone="+966555555551", role=UserRole.COURIER, full_name="Existing account")
    db_session.add(existing)
    await db_session.flush()
    await seed_demo_in_session(db_session, make_test_settings(ENVIRONMENT="development"))
    await db_session.refresh(existing)
    assert existing.role is UserRole.COURIER and existing.full_name == "Existing account"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Order).where(Order.customer_id == existing.id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PaymentIntent)
            .where(PaymentIntent.user_id == existing.id)
        )
        == 0
    )

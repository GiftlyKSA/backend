"""Development-only account seeding and non-destructive replay checks."""

import importlib
import importlib.util
from unittest.mock import AsyncMock

import pytest
from app.models import CourierProfile, User, Wallet
from app.models.enums import UserRole, UserStatus
from sqlalchemy import select

from tests.conftest import make_test_settings


def seed_module():
    assert importlib.util.find_spec("app.seed_development") is not None
    return importlib.import_module("app.seed_development")


@pytest.mark.parametrize("environment", ["test", "production"])
async def test_account_seed_does_not_query_outside_development(environment):
    settings = make_test_settings().model_copy(update={"ENVIRONMENT": environment})
    session = AsyncMock()
    assert await seed_module().seed_couriers_in_session(session, settings) == 0
    session.execute.assert_not_awaited()


async def test_seed_creates_active_verified_couriers_wallets_and_preserves_edits(db_session):
    module = seed_module()
    settings = make_test_settings(ENVIRONMENT="development")
    assert await module.seed_couriers_in_session(db_session, settings) == 2
    users = list(
        await db_session.scalars(
            select(User)
            .where(User.phone.in_(["+966555555555", "+966555555556"]))
            .order_by(User.phone)
        )
    )
    assert len(users) == 2
    for user, city in zip(users, ["Riyadh", "Jeddah"], strict=True):
        assert user.role is UserRole.COURIER
        assert user.status is UserStatus.ACTIVE
        profile = await db_session.get(CourierProfile, user.id)
        assert profile.is_verified and profile.city.name == city
        assert await db_session.scalar(select(Wallet.id).where(Wallet.user_id == user.id))
    users[0].status = UserStatus.BANNED
    await db_session.flush()
    assert await module.seed_couriers_in_session(db_session, settings) == 0
    await db_session.refresh(users[0])
    assert users[0].status is UserStatus.BANNED


async def test_seed_does_not_change_an_existing_customer_role(db_session):
    existing = User(phone="+966555555555", role=UserRole.CUSTOMER)
    db_session.add(existing)
    await db_session.flush()
    settings = make_test_settings(ENVIRONMENT="development")
    assert await seed_module().seed_couriers_in_session(db_session, settings) == 1
    await db_session.refresh(existing)
    assert existing.role is UserRole.CUSTOMER
    assert await db_session.get(CourierProfile, existing.id) is None

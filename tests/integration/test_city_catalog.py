"""City seed and active-choice behavior on disposable PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.models import City, User
from app.models.enums import UserRole
from app.repositories.city_repository import CityRepository
from app.seed import seed_cities_in_session
from app.services.city_service import CityService
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession


async def test_city_seed_is_idempotent_and_inactive_cities_are_rejected(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(City))
    assert await seed_cities_in_session(db_session) == 20
    assert await seed_cities_in_session(db_session) == 0
    service = CityService(CityRepository(db_session))
    assert len(await service.list_active()) == 20
    assert await service.require_active_name(" jEdDaH ") == "Jeddah"
    jeddah_city = await service.require_active_city("Jeddah")
    assert await service.require_active_id(jeddah_city.id) is jeddah_city

    jeddah = await db_session.scalar(select(City).where(City.name == "Jeddah"))
    assert jeddah is not None
    assert jeddah.name_ar == "جدة"
    jeddah.name_ar = "outdated"
    await db_session.flush()
    assert await seed_cities_in_session(db_session) == 0
    assert jeddah.name_ar == "جدة"
    jeddah.is_active = False
    await db_session.flush()
    assert len(await service.list_active()) == 19
    with pytest.raises(ValidationDomainError):
        await service.require_active_name("Jeddah")
    with pytest.raises(ValidationDomainError):
        await service.require_active_id(jeddah_city.id)


async def test_public_user_identifiers_are_generated_and_unique(db_session: AsyncSession) -> None:
    users = [
        User(phone=f"test:{uuid4().hex[:14]}", role=UserRole.CUSTOMER),
        User(phone=f"test:{uuid4().hex[:14]}", role=UserRole.COURIER),
    ]
    db_session.add_all(users)
    await db_session.flush()
    identifiers = {user.public_identifier for user in users}
    assert len(identifiers) == 2
    assert all(1_000_000 <= identifier <= 9_999_999 for identifier in identifiers)

"""City seed and active-choice behavior on disposable PostgreSQL."""

from __future__ import annotations

import pytest
from app.core.exceptions import ValidationDomainError
from app.models import City
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

    jeddah = await db_session.scalar(select(City).where(City.name == "Jeddah"))
    assert jeddah is not None
    jeddah.is_active = False
    await db_session.flush()
    assert len(await service.list_active()) == 19
    with pytest.raises(ValidationDomainError):
        await service.require_active_name("Jeddah")

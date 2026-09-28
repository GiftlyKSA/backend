"""Persistence queries for the city catalog."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City


class CityRepository:
    """Read city choices and resolve active canonical city names."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind city queries to the current transaction."""
        self._session = session

    async def list_active(self) -> list[City]:
        """Return active cities in stable name order."""
        rows = await self._session.scalars(
            select(City).where(City.is_active.is_(True)).order_by(City.name, City.id)
        )
        return list(rows)

    async def active_city_by_name(self, name: str) -> City | None:
        """Look up an active city, ignoring input case and outer spaces."""
        city: City | None = await self._session.scalar(
            select(City).where(
                func.lower(City.name) == name.strip().lower(), City.is_active.is_(True)
            )
        )
        return city

    async def active_city_by_id(self, city_id: uuid.UUID) -> City | None:
        """Look up an active city by its stable primary key."""
        city: City | None = await self._session.scalar(
            select(City).where(City.id == city_id, City.is_active.is_(True))
        )
        return city

    async def active_name(self, name: str) -> str | None:
        """Keep the existing name lookup for name-based public clients."""
        city = await self.active_city_by_name(name)
        return city.name if city is not None else None

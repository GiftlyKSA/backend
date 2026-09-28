"""Persistence queries for the city catalog."""

from __future__ import annotations

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

    async def active_name(self, name: str) -> str | None:
        """Look up the catalog's canonical name, ignoring input case and outer spaces."""
        name_value = await self._session.scalar(
            select(City.name).where(
                func.lower(City.name) == name.strip().lower(), City.is_active.is_(True)
            )
        )
        return str(name_value) if name_value is not None else None

"""Active-city policy shared by onboarding, orders, profiles, and admin flows."""

from __future__ import annotations

import uuid

from app.core.exceptions import ValidationDomainError
from app.models import City
from app.repositories.city_repository import CityRepository


class CityService:
    """Expose active choices and reject cities absent from the catalog."""

    def __init__(self, cities: CityRepository) -> None:
        """Use the repository for authoritative city choices."""
        self._cities = cities

    async def list_active(self) -> list[City]:
        """Return cities available for new selections."""
        return await self._cities.list_active()

    async def require_active_name(self, name: str) -> str:
        """Return the canonical catalog name or reject an inactive/unknown city."""
        canonical = await self._cities.active_name(name)
        if canonical is None:
            raise ValidationDomainError("Select an active city from the city list.")
        return canonical

    async def require_active_city(self, name: str) -> City:
        """Resolve a name to the active related row before a write."""
        city = await self._cities.active_city_by_name(name)
        if city is None:
            raise ValidationDomainError("Select an active city from the city list.")
        return city

    async def require_active_id(self, city_id: uuid.UUID) -> City:
        """Resolve a selected city ID and reject inactive rows."""
        city = await self._cities.active_city_by_id(city_id)
        if city is None:
            raise ValidationDomainError("Select an active city from the city list.")
        return city

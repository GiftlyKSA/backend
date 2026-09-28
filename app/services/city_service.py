"""Active-city policy shared by onboarding, orders, profiles, and admin flows."""

from __future__ import annotations

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

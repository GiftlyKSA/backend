"""Public active city choices for onboarding and order forms."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.repositories.city_repository import CityRepository
from app.schemas.cities import CityResponse
from app.services.city_service import CityService

router = APIRouter(prefix="/api/cities", tags=["cities"])
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[CityResponse])
async def list_cities(db: DbDep) -> list[CityResponse]:
    """List active cities without requiring a login."""
    cities = await CityService(CityRepository(db)).list_active()
    return [
        CityResponse(id=city.id, name=city.name, name_ar=city.name_ar, shortcut=city.shortcut)
        for city in cities
    ]

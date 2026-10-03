"""Occasion ownership and timestamps against disposable PostgreSQL."""

from datetime import date
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models import User
from app.models.enums import UserRole
from app.repositories.planning_repository import PlanningRepository
from app.services.occasion_service import OccasionService
from sqlalchemy.ext.asyncio import AsyncSession


async def test_customer_occasion_crud_and_foreign_ownership(db_session: AsyncSession) -> None:
    user = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    db_session.add(user)
    await db_session.flush()
    service = OccasionService(PlanningRepository(db_session))
    occasion = await service.create(
        user.id, title="Anniversary", occasion_date=date(2026, 12, 15), reminder_days_before=7
    )
    identifier = occasion.id
    assert occasion.created_at.tzinfo is not None
    with pytest.raises(NotFoundError):
        await service.get(uuid4(), identifier)
    with pytest.raises(NotFoundError):
        await service.delete(uuid4(), identifier)
    updated = await service.update(
        user.id, identifier, title="Birthday", occasion_date=None, reminder_days_before=0
    )
    assert updated.title == "Birthday" and updated.reminder_days_before == 0
    assert updated.occasion_date == date(2026, 12, 15)
    assert updated.updated_at.tzinfo is not None
    items, cursor = await service.list(user.id, limit=25, cursor=None, from_date=None)
    assert [item.id for item in items] == [identifier] and cursor is None
    await service.delete(user.id, identifier)
    with pytest.raises(NotFoundError):
        await service.get(user.id, identifier)

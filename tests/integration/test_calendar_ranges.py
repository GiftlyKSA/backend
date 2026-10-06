"""Gregorian range boundaries and stable cursors against disposable PostgreSQL."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from app.models import Occasion, Order, User
from app.models.enums import OrderStatus, UserRole
from app.repositories.order_repository import OrderRepository
from app.repositories.planning_repository import PlanningRepository
from app.services.occasion_service import OccasionService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import city_by_name


@pytest.mark.parametrize("kind", ["customer", "courier", "occasion"])
async def test_same_day_keyset_pages_have_no_duplicates_or_missing_rows(
    db_session: AsyncSession, kind: str
) -> None:
    owner = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    foreign = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.CUSTOMER)
    courier = User(phone=f"+96650{uuid4().int % 10_000_000:07d}", role=UserRole.COURIER)
    db_session.add_all([owner, foreign, courier])
    await db_session.flush()
    chosen = date(2026, 10, 6)
    expected = []
    if kind == "occasion":
        for user_id, day in [(owner.id, chosen)] * 5 + [
            (foreign.id, chosen),
            (owner.id, date(2026, 10, 5)),
            (owner.id, date(2026, 10, 7)),
        ]:
            row = Occasion(user_id=user_id, title="Calendar", occasion_date=day)
            db_session.add(row)
            await db_session.flush()
            if user_id == owner.id and day == chosen:
                expected.append(row.id)
    else:
        city = await city_by_name(db_session, "Riyadh")
        for user_id, day, status, assigned in [
            (owner.id, chosen, OrderStatus.NEW, courier.id)
        ] * 5 + [
            (foreign.id, chosen, OrderStatus.NEW, None),
            (owner.id, date(2026, 10, 5), OrderStatus.NEW, courier.id),
            (owner.id, date(2026, 10, 7), OrderStatus.NEW, courier.id),
            (owner.id, chosen, OrderStatus.CANCELLED, courier.id),
        ]:
            row = Order(
                customer_id=user_id,
                courier_id=assigned,
                city=city,
                delivery_date=day,
                status=status,
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
            db_session.add(row)
            await db_session.flush()
            if user_id == owner.id and day == chosen and status == OrderStatus.NEW:
                expected.append(row.id)
    cursor = None
    found = []
    for _ in range(5):
        if kind == "occasion":
            rows, next_cursor = await OccasionService(PlanningRepository(db_session)).list(
                owner.id, limit=2, cursor=cursor, from_date=chosen, to_date=chosen
            )
        else:
            method = getattr(OrderRepository(db_session), f"list_for_{kind}")
            rows = await method(
                owner.id if kind == "customer" else courier.id,
                status=OrderStatus.NEW,
                limit=2,
                before_id=cursor,
                from_date=chosen,
                to_date=chosen,
            )
            next_cursor = rows[-1].id if len(rows) == 2 else None
        found.extend(row.id for row in rows)
        if next_cursor is None:
            break
        cursor = next_cursor
    assert len(found) == len(set(found)) == 5
    assert set(found) == set(expected)

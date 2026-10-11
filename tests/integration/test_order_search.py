"""Customer SQL search combines ownership, dates, status and keyset pages."""

from datetime import date
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models import City, Order, User
from app.models.enums import OrderStatus, UserRole
from app.repositories.order_repository import OrderRepository
from sqlalchemy import select


async def test_literal_prefix_status_dates_owner_cursor_and_exact_uuid(db_session):
    city = await db_session.scalar(select(City).limit(1))
    owner = User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER)
    stranger = User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.CUSTOMER)
    db_session.add_all([owner, stranger])
    await db_session.flush()
    rows = [
        Order(
            customer_id=owner.id,
            delivery_city_id=city.id,
            delivery_date=date(2027, 1, 5),
            description=f"Gift_% {n}",
            status=OrderStatus.NEW,
        )
        for n in range(3)
    ]
    foreign = Order(
        customer_id=stranger.id,
        delivery_city_id=city.id,
        delivery_date=date(2027, 1, 5),
        description="Gift_% foreign",
    )
    db_session.add_all([*rows, foreign])
    await db_session.flush()
    repo = OrderRepository(db_session)
    kwargs = dict(
        status=OrderStatus.NEW,
        limit=2,
        from_date=date(2027, 1, 5),
        to_date=date(2027, 1, 5),
        search="gift_%",
    )
    first = await repo.list_for_customer(owner.id, before_id=None, **kwargs)
    second = await repo.list_for_customer(owner.id, before_id=first[-1].id, **kwargs)
    assert len(first) == 2 and len(second) == 1
    assert {x.id for x in [*first, *second]} == {x.id for x in rows}
    assert (
        await repo.list_for_customer(
            owner.id, status=None, limit=20, before_id=None, search=str(foreign.id)
        )
        == []
    )
    assert (
        await repo.list_for_customer(
            owner.id, status=OrderStatus.CANCELLED, limit=20, before_id=None, search="gift_%"
        )
        == []
    )
    with pytest.raises(NotFoundError):
        await repo.list_for_customer(owner.id, before_id=foreign.id, **kwargs)

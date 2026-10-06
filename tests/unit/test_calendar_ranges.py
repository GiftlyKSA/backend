"""Calendar date filters stay inside authorized, bounded SQL queries."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.deps import Actor, get_db, require_auth
from app.main import create_app
from app.models.enums import OrderStatus, UserRole
from app.repositories.order_repository import OrderRepository
from app.repositories.planning_repository import PlanningRepository
from app.routers import occasions, orders
from app.services.occasion_service import OccasionService
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


@pytest.mark.parametrize("endpoint", ["orders", "occasions"])
@pytest.mark.parametrize(
    "params,expected",
    [
        ({}, 200),
        ({"from_date": "2026-10-01"}, 200),
        ({"to_date": "2026-10-31"}, 200),
        ({"from_date": "2026-10-06", "to_date": "2026-10-06"}, 200),
        ({"from_date": "2026-10-31", "to_date": "2026-10-01"}, 422),
        ({"from_date": "2026-02-30"}, 422),
        ({"to_date": "bad-date"}, 422),
        ({"from_date": "2026-10-01T00:00:00"}, 422),
    ],
)
async def test_range_http_validation_and_empty_page(monkeypatch, endpoint, params, expected):
    app = create_app(make_test_settings())
    actor = Actor(id=uuid4(), role=UserRole.CUSTOMER, jti="test")

    async def database():
        yield AsyncMock()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_auth] = lambda: actor
    app.dependency_overrides[orders._eligible_participant] = lambda: actor
    service = AsyncMock()
    service.list.return_value = ([], None)
    service.list_views_for_actor.return_value = []
    monkeypatch.setattr(occasions, "_service", lambda db: service)
    monkeypatch.setattr(orders, "_service", lambda request, db: service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/{endpoint}", params=params)
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json() == {"items": [], "next_cursor": None}
        call = service.list if endpoint == "occasions" else service.list_views_for_actor
        assert call.call_args.kwargs["to_date"] == (
            date.fromisoformat(params["to_date"]) if "to_date" in params else None
        )
    else:
        service.list.assert_not_awaited()
        service.list_views_for_actor.assert_not_awaited()


@pytest.mark.parametrize("kind", ["customer", "courier", "occasion"])
@pytest.mark.parametrize(
    "start,end",
    [
        (None, None),
        (date(2026, 10, 6), None),
        (None, date(2026, 10, 6)),
        (date(2026, 10, 6), date(2026, 10, 6)),
    ],
)
async def test_range_sql_is_inclusive_scoped_and_applied_to_anchor(kind, start, end):
    session = AsyncMock()
    session.scalars.return_value = []
    actor_id = uuid4()
    if kind == "occasion":
        await PlanningRepository(session).list_occasions_for_actor(
            actor_id, limit=26, from_date=start, to_date=end
        )
        field, owner = "occasions.occasion_date", "occasions.user_id"
    else:
        anchor = SimpleNamespace(id=uuid4(), created_at="2026-10-01")
        session.scalar.return_value = anchor
        await getattr(OrderRepository(session), f"list_for_{kind}")(
            actor_id,
            status=OrderStatus.NEW,
            limit=20,
            before_id=anchor.id,
            from_date=start,
            to_date=end,
        )
        field, owner = "orders.delivery_date", f"orders.{kind}_id"
        anchor_sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
        assert (f"{field} >=" in anchor_sql) == (start is not None)
        assert (f"{field} <=" in anchor_sql) == (end is not None)
        assert "orders.status =" in anchor_sql
    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(query)
    assert f"{owner} =" in sql and actor_id in query.params.values()
    assert (f"{field} >=" in sql) == (start is not None)
    assert (f"{field} <=" in sql) == (end is not None)
    assert "LIMIT" in sql and "OFFSET" not in sql


async def test_occasion_cursor_outside_upper_bound_rejected_before_list_query():
    repository = AsyncMock()
    repository.get_occasion_for_actor.return_value = SimpleNamespace(
        occasion_date=date(2026, 11, 1)
    )
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await OccasionService(repository).list(
            uuid4(), limit=25, cursor=uuid4(), from_date=None, to_date=date(2026, 10, 31)
        )
    repository.list_occasions_for_actor.assert_not_awaited()

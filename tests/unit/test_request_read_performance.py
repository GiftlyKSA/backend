from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession


async def test_read_request_does_not_initialize_write_audit_context():
    from unittest.mock import Mock

    from app.core.deps import get_db

    session = AsyncMock()
    session.info = {}
    session.__aenter__.return_value = session
    request = SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path="/api/orders"),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=Mock(return_value=session))),
    )
    dependency = get_db(request)
    assert await anext(dependency) is session
    assert session.info.get("read_only_request") is True
    assert session.execute.await_count == 1
    assert str(session.execute.await_args.args[0]) == "SET TRANSACTION READ ONLY"
    await dependency.aclose()


async def test_courier_order_enrichment_does_not_query_customer_rating_state():
    from app.models.enums import UserRole
    from app.services.order_service import OrderService

    orders, ratings, eligibility = AsyncMock(), AsyncMock(), AsyncMock()
    orders.list_for_courier.return_value = [SimpleNamespace(id=uuid4()) for _ in range(100)]
    service = OrderService(
        session=AsyncMock(),
        orders=orders,
        couriers=AsyncMock(),
        eligibility=eligibility,
        media=AsyncMock(),
        messages=AsyncMock(),
        ratings=ratings,
        redis=AsyncMock(),
        settings=SimpleNamespace(),
    )
    views = await service.list_views_for_actor(
        actor_id=uuid4(), role=UserRole.COURIER, status=None, limit=100, before_id=None
    )
    assert len(views) == 100
    assert all(view.current_actor_has_rated is False for view in views)
    ratings.current_actor_rating_states.assert_not_awaited()


@pytest.mark.parametrize("repository", [UserRepository, CourierRepository])
async def test_read_snapshot_reuses_lookup_only_in_current_transaction(repository, monkeypatch):
    session = AsyncSession()
    session.info["read_only_request"] = True
    actor_id = uuid4()
    row = SimpleNamespace(id=actor_id)
    lookup = AsyncMock(return_value=row)
    monkeypatch.setattr(session, "get", lookup)
    async with session.begin():
        assert await repository(session).get(actor_id) is row
        assert await repository(session).get(actor_id) is row
        assert lookup.await_count == 1
    async with session.begin():
        await repository(session).get(actor_id)
        assert lookup.await_count == 2
    await session.close()


@pytest.mark.parametrize("repository", [UserRepository, CourierRepository])
async def test_mutation_lookups_are_not_memoized(repository, monkeypatch):
    session = AsyncSession()
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(session, "get", lookup)
    actor_id = uuid4()
    async with session.begin():
        await repository(session).get(actor_id)
        await repository(session).get(actor_id)
    assert lookup.await_count == 2
    await session.close()

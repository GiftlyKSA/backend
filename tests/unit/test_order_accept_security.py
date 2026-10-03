"""City authorization is enforced while the assignment rows are locked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import ForbiddenError, NotFoundError, OrderAlreadyAssignedError
from app.models.enums import OrderStatus

from tests.unit.test_final_security_regressions import order_service


async def test_accept_other_city_hides_order_and_does_not_assign():
    service = order_service()
    order = SimpleNamespace(
        id=uuid4(), customer_id=uuid4(), delivery_city_id=uuid4(), status=OrderStatus.NEW
    )
    service._orders.now = Mock()
    service._orders.lock.return_value = order
    service._couriers.lock = AsyncMock(
        return_value=SimpleNamespace(city_of_residence_id=uuid4(), is_verified=True)
    )
    with pytest.raises(NotFoundError):
        await service._assign_locked(uuid4(), uuid4())
    service._orders.create_conversation.assert_not_awaited()


async def test_accept_requires_current_verified_profile():
    service = order_service()
    service._couriers.lock.return_value = None
    with pytest.raises(ForbiddenError):
        await service._assign_locked(uuid4(), uuid4())
    service._orders.lock.assert_not_awaited()


async def test_accept_same_city_assigns_and_creates_conversation():
    service = order_service()
    city_id, courier_id = uuid4(), uuid4()
    order = SimpleNamespace(
        id=uuid4(), customer_id=uuid4(), delivery_city_id=city_id, status=OrderStatus.NEW
    )
    service._orders.now = Mock()
    service._orders.lock.return_value = order
    service._couriers.lock.return_value = SimpleNamespace(
        city_of_residence_id=city_id, is_verified=True
    )
    result = await service._assign_locked(order.id, courier_id)
    assert result.courier_id == courier_id
    assert result.status is OrderStatus.ASSIGNED
    service._orders.create_conversation.assert_awaited_once_with(
        order_id=order.id, customer_id=order.customer_id, courier_id=courier_id
    )


async def test_profile_lock_filters_current_account_and_locks_profile():
    from app.repositories.courier_repository import CourierRepository
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    await CourierRepository(session).lock(uuid4())
    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF courier_profiles" in sql
    assert "users.role =" in sql and "users.status =" in sql
    assert "users.deleted_at IS NULL" in sql
    assert statement.get_execution_options()["populate_existing"] is True


@pytest.mark.parametrize("commit_fails", [False, True])
async def test_accept_publishes_only_after_successful_commit(monkeypatch, commit_fails):
    from app.routers import orders

    events = []
    order_id = uuid4()
    service = AsyncMock()
    service.accept_order.return_value = SimpleNamespace(id=order_id)
    db = AsyncMock()

    async def commit():
        events.append("commit")
        if commit_fails:
            raise RuntimeError("Database write failed")

    async def publish(*args):
        events.append("publish")

    db.commit.side_effect = commit
    monkeypatch.setattr(orders, "_service", lambda *args: service)
    monkeypatch.setattr(orders, "_detail", lambda view: SimpleNamespace(id=str(order_id)))
    monkeypatch.setattr(orders, "get_redis", lambda request: Mock())
    monkeypatch.setattr(orders, "emit_committed_audit_events", lambda session: None)
    monkeypatch.setattr(orders, "publish_order_change", publish)
    actor = SimpleNamespace(id=uuid4(), role="COURIER")
    if commit_fails:
        with pytest.raises(RuntimeError):
            await orders.accept_order(Mock(), db, order_id, actor)
        assert events == ["commit"]
    else:
        await orders.accept_order(Mock(), db, order_id, actor)
        assert events == ["commit", "publish"]


async def test_accept_already_assigned_same_city_cannot_overwrite():
    service = order_service()
    city_id = uuid4()
    courier_id = uuid4()
    assigned_courier = uuid4()
    order = SimpleNamespace(
        delivery_city_id=city_id, status=OrderStatus.ASSIGNED, courier_id=assigned_courier
    )
    service._orders.lock.return_value = order
    service._couriers.lock = AsyncMock(
        return_value=SimpleNamespace(city_of_residence_id=city_id, is_verified=True)
    )
    with pytest.raises(OrderAlreadyAssignedError):
        await service._assign_locked(uuid4(), courier_id)
    assert order.courier_id == assigned_courier
    service._orders.create_conversation.assert_not_awaited()

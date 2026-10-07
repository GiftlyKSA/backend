from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import UnauthorizedError
from app.core.jwt import create_access_token
from app.models.enums import UserRole, UserStatus
from app.repositories.order_repository import OrderRepository
from app.services.order_realtime_service import OrderRealtimeService
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


async def test_realtime_projection_uses_one_owned_query_without_city_eager_loading():
    session = AsyncMock()
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    actor_id, order_id = uuid4(), uuid4()
    assert await OrderRepository(session).get_live_state(order_id, actor_id) is None
    assert session.execute.await_count == 1
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN orders" in sql
    assert "customer_id" in sql and "courier_id" in sql
    assert "cities" not in sql


@pytest.mark.parametrize("change", ["ban", "delete", "role", "version", "logout"])
async def test_single_query_snapshot_still_rejects_revoked_accounts(monkeypatch, change):
    settings, actor_id = make_test_settings(), uuid4()
    user = SimpleNamespace(
        id=actor_id,
        role=UserRole.COURIER,
        status=UserStatus.ACTIVE,
        deleted_at=None,
        auth_version=0,
    )
    token, _, _ = create_access_token(settings, user_id=actor_id, role="COURIER")
    if change == "ban":
        user.status = UserStatus.BANNED
    elif change == "delete":
        user.deleted_at = object()
    elif change == "role":
        user.role = UserRole.CUSTOMER
    elif change == "version":
        user.auth_version = 1
    redis = AsyncMock()
    redis.get.return_value = "1" if change == "logout" else None
    session = AsyncMock()
    session.__aenter__.return_value = session
    projection = SimpleNamespace(
        user=user,
        courier_verified=True,
        order_id=uuid4(),
        status="ASSIGNED",
        courier_id=actor_id,
        assigned_at=None,
    )
    monkeypatch.setattr(OrderRepository, "get_live_state", AsyncMock(return_value=projection))
    service = OrderRealtimeService(settings, redis, Mock(return_value=session))
    with pytest.raises(UnauthorizedError):
        await service.snapshot(projection.order_id, token)

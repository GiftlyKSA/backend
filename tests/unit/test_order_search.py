"""Owned order search keeps literal input and filters in SQL."""

import uuid
from unittest.mock import AsyncMock

import pytest
from app.repositories.order_repository import OrderRepository
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_search_is_literal_prefix_and_owner_scoped():
    session = AsyncMock()
    repo = OrderRepository(session)
    repo._page = AsyncMock(return_value=[])
    owner = uuid.uuid4()
    await repo.list_for_customer(owner, status=None, limit=20, before_id=None, search="Gift_%")
    statement = repo._page.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "customer_id" in str(statement)
    assert "left(lower(orders.description)" in str(statement)
    assert "gift\\_\\%%" in statement.params.values()
    assert owner in statement.params.values()


@pytest.mark.asyncio
async def test_search_exact_uuid_keeps_owner_filter():
    session = AsyncMock()
    repo = OrderRepository(session)
    repo._page = AsyncMock(return_value=[])
    owner, order_id = uuid.uuid4(), uuid.uuid4()
    await repo.list_for_customer(owner, status=None, limit=20, before_id=None, search=str(order_id))
    statement = repo._page.call_args.args[0].compile(dialect=postgresql.dialect())
    assert owner in statement.params.values()
    assert order_id in statement.params.values()


@pytest.mark.asyncio
async def test_search_rejects_nul_before_database():
    from app.core.exceptions import ValidationDomainError
    from app.models.enums import UserRole
    from app.services.order_service import OrderService

    service = object.__new__(OrderService)
    service._orders = AsyncMock()
    with pytest.raises(ValidationDomainError):
        await service.list_views_for_actor(
            actor_id=uuid.uuid4(),
            role=UserRole.CUSTOMER,
            status=None,
            limit=25,
            before_id=None,
            search="gift\x00",
        )
    service._orders.list_for_customer.assert_not_awaited()

"""Delivery windows stay fixed as orders age."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.models import Order
from app.services.order_service import NewOrderInput, OrderService
from sqlalchemy import CheckConstraint


def test_delivery_constraint_does_not_change_with_current_date():
    constraint = next(
        item
        for item in Order.__table__.constraints
        if isinstance(item, CheckConstraint) and item.name == "chk_delivery_date_window"
    )
    sql = str(constraint.sqltext)
    assert "CURRENT_DATE" not in sql
    assert "created_at AT TIME ZONE 'UTC'" in sql
    assert "180" in sql


@pytest.mark.parametrize("offset", [-1, 181])
async def test_create_rejects_invalid_delivery_window_before_database_work(offset):
    now = datetime.now(UTC)
    service = object.__new__(OrderService)
    service._orders = SimpleNamespace(now=lambda: now, lock_actor=AsyncMock())
    with pytest.raises(ValidationDomainError, match="delivery date"):
        await service.create_order(
            customer_id=uuid4(),
            data=NewOrderInput(
                description=None,
                delivery_city="Riyadh",
                delivery_date=now.date() + timedelta(days=offset),
                request_media_keys=[],
            ),
        )
    service._orders.lock_actor.assert_not_awaited()

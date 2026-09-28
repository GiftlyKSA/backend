"""City catalog contract and seed coverage without a live database."""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.main import create_app
from app.models import Base
from app.services.order_service import NewOrderInput, OrderService

from tests.conftest import make_test_settings


def test_city_catalog_has_required_columns_and_protects_references() -> None:
    city = Base.metadata.tables["cities"]
    assert {"id", "name", "shortcut", "is_active", "created_at"} <= set(city.c.keys())
    assert city.c.name.unique and city.c.shortcut.unique
    for table_name, column_name in (
        ("orders", "delivery_city"),
        ("courier_profiles", "city_of_residence"),
    ):
        targets = {
            str(key.column) for key in Base.metadata.tables[table_name].c[column_name].foreign_keys
        }
        assert "cities.name" in targets


def test_public_city_list_is_in_openapi() -> None:
    schema = create_app(make_test_settings()).openapi()
    assert "/api/cities" in schema["paths"]
    assert "get" in schema["paths"]["/api/cities"]


@pytest.mark.asyncio
async def test_order_rejects_city_missing_from_active_catalog() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    orders = AsyncMock()
    orders.count_customer_active.return_value = 0
    service = OrderService(
        session=session,
        orders=orders,
        couriers=AsyncMock(),
        eligibility=AsyncMock(),
        media=AsyncMock(),
        messages=AsyncMock(),
        ratings=AsyncMock(),
        redis=AsyncMock(),
        settings=make_test_settings(),
    )
    data = NewOrderInput(
        description=None,
        delivery_city="Unknown City",
        latitude=24.7,
        longitude=46.7,
        delivery_date=date.today(),
        request_media_keys=[],
    )

    with pytest.raises(ValidationDomainError, match="active city"):
        await service.create_order(customer_id=uuid4(), data=data)
    orders.create.assert_not_awaited()

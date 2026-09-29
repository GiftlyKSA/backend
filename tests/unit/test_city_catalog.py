"""City catalog contract and seed coverage without a live database."""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.main import create_app
from app.models import Base, City
from app.schemas.orders import CreateOrderRequest
from app.services.order_service import NewOrderInput, OrderService

from tests.conftest import make_test_settings


def test_city_catalog_has_required_columns_and_protects_references() -> None:
    city = Base.metadata.tables["cities"]
    assert {"id", "name", "shortcut", "is_active", "created_at"} <= set(city.c.keys())
    assert city.c.name.unique and city.c.shortcut.unique
    for table_name, column_name in (
        ("orders", "delivery_city_id"),
        ("courier_profiles", "city_of_residence_id"),
    ):
        assert column_name in Base.metadata.tables[table_name].c
        assert column_name.removesuffix("_id") not in Base.metadata.tables[table_name].c
        targets = {
            str(key.column) for key in Base.metadata.tables[table_name].c[column_name].foreign_keys
        }
        assert "cities.id" in targets


def test_public_city_list_is_in_openapi() -> None:
    schema = create_app(make_test_settings()).openapi()
    assert "/api/cities" in schema["paths"]
    assert "get" in schema["paths"]["/api/cities"]


def test_order_request_accepts_city_id_and_rejects_ambiguous_selection() -> None:
    fields = {
        "delivery_city_id": str(uuid4()),
        "delivery_map_url": "https://maps.app.goo.gl/Test",
        "delivery_date": date.today(),
    }
    assert CreateOrderRequest.model_validate(fields).delivery_city is None
    with pytest.raises(ValueError, match="exactly one city"):
        CreateOrderRequest.model_validate({**fields, "delivery_city": "Riyadh"})


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
        delivery_map_url="https://maps.app.goo.gl/Test",
        delivery_date=date.today(),
        request_media_keys=[],
    )

    with pytest.raises(ValidationDomainError, match="active city"):
        await service.create_order(customer_id=uuid4(), data=data)
    orders.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_order_saves_selected_city_record() -> None:
    session = AsyncMock()
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
    city_id = uuid4()
    city = City(id=city_id, name="Jeddah", shortcut="JED", is_active=True)
    service._cities = AsyncMock()
    service._cities.require_active_id.return_value = city
    await service.create_order(
        customer_id=uuid4(),
        data=NewOrderInput(
            description=None,
            delivery_city=None,
            delivery_city_id=city_id,
            delivery_map_url="https://maps.app.goo.gl/Test",
            delivery_date=date.today(),
            request_media_keys=[],
        ),
    )
    assert orders.create.call_args.kwargs["delivery_city"] is city

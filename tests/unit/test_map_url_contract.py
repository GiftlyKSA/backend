"""Orders store a Google Maps link and delivery proof without spatial data."""

from datetime import date, timedelta

import pytest
from app.models import Order, OrderMedia
from app.schemas.fulfillment import DeliverRequest
from app.schemas.orders import CreateOrderRequest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def _order_payload(url: str) -> dict[str, object]:
    return {
        "delivery_city": "Riyadh",
        "delivery_map_url": url,
        "delivery_date": (date.today() + timedelta(days=1)).isoformat(),
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://maps.app.goo.gl/Example",
        "https://www.google.com/maps/place/Riyadh",
        "https://maps.google.com/?q=Riyadh",
    ],
)
def test_order_accepts_google_maps_link_without_coordinates(url: str) -> None:
    request = CreateOrderRequest.model_validate(_order_payload(url))
    assert request.delivery_map_url == url


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/maps",
        "http://www.google.com/maps/place/Riyadh",
        "javascript:alert(1)",
        "https://maps.app.goo.gl.evil.example/Test",
        "https://maps.app.goo.gl/Test\nhttps://evil.example",
    ],
)
def test_order_rejects_non_google_or_unsafe_link(url: str) -> None:
    with pytest.raises(ValidationError, match="Google Maps"):
        CreateOrderRequest.model_validate(_order_payload(url))


def test_delivery_proof_needs_no_coordinates() -> None:
    request = DeliverRequest.model_validate({"proof_media_keys": ["proof-key"]})
    assert request.proof_media_keys == ["proof-key"]


def test_order_map_url_may_be_omitted() -> None:
    payload = _order_payload("https://maps.app.goo.gl/Example")
    del payload["delivery_map_url"]
    assert CreateOrderRequest.model_validate(payload).delivery_map_url is None
    assert Order.__table__.c.delivery_map_url.nullable


def test_fresh_schema_has_map_url_and_no_spatial_constraint() -> None:
    assert "delivery_map_url" in Order.__table__.c
    assert "delivery_location" not in Order.__table__.c
    ddl = str(CreateTable(OrderMedia.__table__).compile(dialect=postgresql.dialect()))
    assert "capture_location" not in ddl

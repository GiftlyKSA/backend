"""Delivery proof is media-based without spatial data."""

from app.models import OrderMedia
from app.schemas.fulfillment import DeliverRequest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def test_delivery_proof_needs_no_coordinates() -> None:
    request = DeliverRequest.model_validate({"proof_media_keys": ["proof-key"]})
    assert request.proof_media_keys == ["proof-key"]
    ddl = str(CreateTable(OrderMedia.__table__).compile(dialect=postgresql.dialect()))
    assert "capture_location" not in ddl

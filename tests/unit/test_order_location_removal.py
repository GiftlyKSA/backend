"""Orders retain city/date without collecting precise delivery locations."""

from importlib import import_module
from unittest.mock import Mock

import pytest
from app.models import Order
from app.schemas.orders import CreateOrderRequest, OrderDetail
from pydantic import ValidationError


def test_order_storage_and_response_have_no_delivery_location() -> None:
    for field in ("delivery_map_url", "delivery_location"):
        assert field not in Order.__table__.c
        assert field not in OrderDetail.model_fields
        assert field not in CreateOrderRequest.model_fields
    assert "delivery_city_id" in Order.__table__.c
    assert "delivery_address_note" in Order.__table__.c
    assert "delivery_date" in CreateOrderRequest.model_fields


@pytest.mark.parametrize(
    "field", ["delivery_map_url", "delivery_address_note", "delivery_location"]
)
def test_create_order_rejects_removed_location_inputs(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateOrderRequest.model_validate(
            {"delivery_city": "Riyadh", "delivery_date": "2026-10-10", field: "location"}
        )


def test_location_migration_drops_only_removed_columns(monkeypatch) -> None:
    migration = import_module("app.migrations.versions.0010_remove_delivery_location")
    operations = Mock()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert [call.args for call in operations.drop_column.call_args_list] == [
        ("orders", "delivery_map_url"),
        ("orders", "delivery_address_note"),
    ]
    assert migration.down_revision == "0009_action_only_audit"
    migration.downgrade()
    columns = [call.args[1] for call in operations.add_column.call_args_list]
    assert {column.name for column in columns} == {"delivery_map_url", "delivery_address_note"}
    assert all(column.nullable for column in columns)


def test_corrective_migration_restores_address_note_only(monkeypatch) -> None:
    migration = import_module("app.migrations.versions.0011_restore_delivery_address_note")
    operations = Mock()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    operations.add_column.assert_called_once()
    table, column = operations.add_column.call_args.args
    assert table == "orders"
    assert column.name == "delivery_address_note"
    assert column.nullable
    assert column.type.length == 255

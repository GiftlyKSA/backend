"""Verify keyset query index ordering and reversible migration coverage."""

import importlib

import pytest
from app.models import Conversation, Invoice, Order
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex


@pytest.mark.parametrize(
    ("model", "name", "columns"),
    [
        (Order, "idx_orders_radar_keyset", "delivery_city_id, created_at DESC, id DESC"),
        (Order, "idx_orders_customer_created", "customer_id, created_at DESC, id DESC"),
        (Order, "idx_orders_courier_created", "courier_id, created_at DESC, id DESC"),
        (Invoice, "idx_invoices_order", "order_id, created_at DESC, id DESC"),
        (
            Conversation,
            "idx_conversations_customer_inbox",
            "customer_id, last_message_timestamp DESC, id DESC",
        ),
        (
            Conversation,
            "idx_conversations_courier_inbox",
            "courier_id, last_message_timestamp DESC, id DESC",
        ),
    ],
)
def test_keyset_indexes_cover_filter_and_deterministic_sort(model, name, columns):
    indexes = {index.name: index for index in model.__table__.indexes}
    assert name in indexes
    ddl = str(CreateIndex(indexes[name]).compile(dialect=postgresql.dialect()))
    assert f"({columns})" in ddl
    if name == "idx_orders_radar_keyset":
        assert "WHERE status = 'NEW'" in ddl


def test_read_index_migration_restores_previous_shapes(monkeypatch):
    migration = importlib.import_module("app.migrations.versions.0021_courier_read_indexes")
    created = []
    dropped = []
    monkeypatch.setattr(migration.op, "create_index", lambda *a, **kw: created.append((a, kw)))
    monkeypatch.setattr(migration.op, "drop_index", lambda *a, **kw: dropped.append((a, kw)))
    migration.upgrade()
    assert len(created) == 6
    assert len(dropped) == 5
    for args, kwargs in created:
        table = {"orders": Order, "invoices": Invoice, "conversations": Conversation}[args[1]]
        index = next(index for index in table.__table__.indexes if index.name == args[0])
        assert [str(expression) for expression in index.expressions] == [
            str(column) if not isinstance(column, str) else f"{args[1]}.{column}"
            for column in args[2]
        ]
        assert str(index.dialect_options["postgresql"].get("where")) == str(
            kwargs.get("postgresql_where")
        )
    created.clear()
    dropped.clear()
    migration.downgrade()
    assert len(created) == 5
    assert len(dropped) == 6
    assert {args[0]: [str(column) for column in args[2]] for args, _ in created} == {
        "idx_orders_customer_created": ["customer_id", "created_at DESC"],
        "idx_orders_courier_created": ["courier_id", "created_at DESC"],
        "idx_invoices_order": ["order_id"],
        "idx_conversations_customer_inbox": ["customer_id", "last_message_timestamp DESC"],
        "idx_conversations_courier_inbox": ["courier_id", "last_message_timestamp DESC"],
    }

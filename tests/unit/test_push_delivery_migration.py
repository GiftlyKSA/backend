"""Rollback must preserve pending delivery and receipt evidence."""

from importlib import import_module
from unittest.mock import Mock

import pytest


def test_downgrade_refuses_to_discard_device_delivery_history(monkeypatch):
    migration = import_module("app.migrations.versions.0040_push_deliveries")
    operations = Mock()
    operations.get_bind.return_value.scalar.return_value = True
    monkeypatch.setattr(migration, "op", operations)
    with pytest.raises(RuntimeError, match="preserve delivery state"):
        migration.downgrade()
    operations.drop_table.assert_not_called()
    operations.drop_column.assert_not_called()


def test_empty_downgrade_removes_receipt_link_before_delivery_table(monkeypatch):
    migration = import_module("app.migrations.versions.0040_push_deliveries")
    operations = Mock()
    operations.get_bind.return_value.scalar.return_value = False
    monkeypatch.setattr(migration, "op", operations)
    migration.downgrade()
    names = [call[0] for call in operations.mock_calls]
    assert names.index("drop_column") < names.index("drop_table")

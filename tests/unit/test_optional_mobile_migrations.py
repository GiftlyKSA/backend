"""Rollback refuses loss of annual settings or pending provider receipts."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_annual_downgrade_refuses_existing_recurrence(monkeypatch):
    migration = import_module("app.migrations.versions.0033_annual_occasions")
    operations = Mock()
    monkeypatch.setattr(migration.op, "execute", operations)
    monkeypatch.setattr(migration.op, "get_bind", lambda: SimpleNamespace(scalar=lambda _: True))
    with pytest.raises(RuntimeError, match="Preserve annual"):
        migration.downgrade()
    assert operations.call_count == 1
    assert operations.call_args.args[0].startswith("LOCK TABLE")


def test_expo_downgrade_checks_pending_before_dropping(monkeypatch):
    migration = import_module("app.migrations.versions.0034_expo_push_receipts")
    statements = []

    def execute(sql):
        statements.append(sql)
        if sql.startswith("DO "):
            raise RuntimeError("Cannot downgrade with pending Expo receipts")

    monkeypatch.setattr(migration.op, "execute", execute)
    drops = Mock()
    monkeypatch.setattr(migration.op, "drop_table", drops)
    with pytest.raises(RuntimeError, match="pending"):
        migration.downgrade()
    assert statements[0].startswith("LOCK TABLE")
    drops.assert_not_called()

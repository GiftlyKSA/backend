"""Mobile rollback cannot silently discard preserved notification state."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("has_state", [True, False])
def test_rollback_refuses_mobile_state_before_any_drop(monkeypatch, has_state) -> None:
    migration = import_module("app.migrations.versions.0030_mobile_notifications")
    operations = Mock()
    monkeypatch.setattr(migration.op, "execute", operations)
    monkeypatch.setattr(
        migration.op, "get_bind", lambda: SimpleNamespace(scalar=lambda _: has_state)
    )
    if has_state:
        with pytest.raises(RuntimeError, match="Preserve mobile"):
            migration.downgrade()
        assert operations.call_count == 1
        assert operations.call_args.args[0].startswith("LOCK TABLE")
    else:
        migration.downgrade()
        assert any(
            "DROP TABLE inbox_notifications" in call.args[0] for call in operations.call_args_list
        )

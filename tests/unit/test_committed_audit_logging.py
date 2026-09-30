"""Committed audit events have a scrubbed second output path."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.core.db import emit_committed_audit_events
from app.models import AuditLog


def test_only_persisted_audit_metadata_is_logged(caplog) -> None:
    actor_id = uuid4()
    row = AuditLog(
        actor_user_id=actor_id,
        action="ADMIN_TABLE_UPDATE",
        entity_type="users",
        entity_id=uuid4(),
        audit_metadata={"phone": "+966500000000", "actor_category": "ADMIN"},
    )
    rolled_back = AuditLog(action="ADMIN_TABLE_DELETE", entity_type="users")
    session = Mock()
    session.info = {"committed_audit_events": [row, rolled_back]}
    states = [SimpleNamespace(persistent=True), SimpleNamespace(persistent=False)]
    with (
        patch("app.core.db.inspect", side_effect=states),
        caplog.at_level(logging.INFO, logger="giftly.audit"),
    ):
        emit_committed_audit_events(session)

    assert session.info == {}
    assert len(caplog.records) == 1
    fields = caplog.records[0].extra_fields
    assert fields == {
        "action": "ADMIN_TABLE_UPDATE",
        "actor_user_id": str(actor_id),
        "entity_type": "users",
        "entity_id": str(row.entity_id),
        "actor_category": "ADMIN",
    }
    assert "phone" not in str(fields)

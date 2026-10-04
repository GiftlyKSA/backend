"""Recipient snapshots survive registration churn and retrying claims."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.repositories.order_notification_repository import (
    ClaimedNotification,
    OrderNotificationRepository,
)
from sqlalchemy.dialects import postgresql


async def test_first_claim_materializes_recipients_once_before_delivery() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        order_id=uuid.uuid4(),
        city_id=uuid.uuid4(),
        cursor_token_id=uuid.uuid4(),
        recipients_snapshotted_at=None,
        attempts=0,
    )
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=[row]),
        execute=AsyncMock(),
        flush=AsyncMock(),
    )
    repo = OrderNotificationRepository(session)  # type: ignore[arg-type]

    claims = await repo.claim_pending(now=now, limit=1, lease_seconds=60)

    assert row.recipients_snapshotted_at == now
    assert claims[0].cursor_token_id is None
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "INSERT INTO order_notification_recipients" in sql
    assert "SELECT" in sql
    assert "device_tokens.user_id" in sql
    assert "courier_profiles.is_verified IS true" in sql
    await repo.claim_pending(now=now, limit=1, lease_seconds=60)
    assert session.execute.call_count == 1


@pytest.mark.parametrize("owns_claim", [True, False])
async def test_only_owned_completed_claim_discards_snapshot(owns_claim: bool) -> None:
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=owns_claim)))
    repo = OrderNotificationRepository(session)  # type: ignore[arg-type]
    claim = ClaimedNotification(uuid.uuid4(), uuid.uuid4(), None, uuid.uuid4(), 1)
    saved = await repo.advance(claim, now=datetime.now(UTC), cursor=None, complete=True)

    assert saved is owns_claim
    assert session.execute.call_count == (2 if owns_claim else 1)
    statement = session.execute.call_args_list[0].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "order_notifications.lease_id =" in sql
    assert "order_notifications.leased_until >" in sql
    if owns_claim:
        deletion = session.execute.call_args_list[1].args[0]
        compiled = deletion.compile(dialect=postgresql.dialect())
        assert "DELETE FROM order_notification_recipients" in str(compiled)
        assert claim.order_id in compiled.params.values()


async def test_pages_only_include_original_snapshot_and_still_owned_tokens() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=Mock(return_value=[])))
    )
    repo = OrderNotificationRepository(session)  # type: ignore[arg-type]
    order_id = uuid.uuid4()
    await repo.token_page(order_id=order_id, city_id=uuid.uuid4(), after=None, limit=500)

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "order_notification_recipients.order_id =" in sql
    assert "order_notification_recipients.token_id = device_tokens.id" in sql
    assert "order_notification_recipients.user_id = device_tokens.user_id" in sql
    assert order_id in compiled.params.values()
    assert "users.status =" in sql
    assert "courier_profiles.is_verified IS true" in sql

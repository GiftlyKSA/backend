"""Delivery queries bound ownership, claim races, and retry exhaustion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.repositories.chat_notification_repository import (
    ChatNotificationRepository,
    ClaimedChatNotification,
)
from sqlalchemy.dialects import postgresql


async def test_claim_locks_only_ready_rows_and_marks_exhausted_crashes_failed() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        message_id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        cursor_token_id=None,
        attempts=7,
    )
    session = SimpleNamespace(scalars=AsyncMock(return_value=[row]), flush=AsyncMock())
    repository = ChatNotificationRepository(session)
    batch = await repository.claim_pending(now=now, limit=1, lease_seconds=60)
    assert batch.claims[0].attempts == 8
    assert batch.retired_count == 0
    assert row.leased_until == now + timedelta(seconds=60)
    compiled = session.scalars.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "completed_at IS NULL" in sql
    assert "failed_at IS NULL" in sql
    assert "available_at <=" in sql
    assert "leased_until IS NULL" in sql
    assert "leased_until <=" in sql
    assert "LIMIT" in sql
    recovered = await repository.claim_pending(now=now, limit=1, lease_seconds=60)
    assert recovered.claims == []
    assert recovered.retired_count == 1
    assert row.failed_at == now
    assert row.lease_id is None


async def test_tokens_remain_scoped_to_current_active_recipient_and_cursor() -> None:
    recipient, cursor = uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=Mock(return_value=[]))),
    )
    await ChatNotificationRepository(session).token_page(
        recipient_id=recipient,
        after=cursor,
        limit=500,
    )
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "device_tokens.user_id =" in sql
    assert "users.status =" in sql
    assert "users.deleted_at IS NULL" in sql
    assert "device_tokens.id >" in sql
    assert recipient in compiled.params.values()
    assert cursor in compiled.params.values()
    assert 500 in compiled.params.values()


async def test_completed_pages_are_fenced_and_failures_preserve_cursor() -> None:
    now = datetime.now(UTC)
    cursor = uuid.uuid4()
    claim = ClaimedChatNotification(uuid.uuid4(), uuid.uuid4(), cursor, uuid.uuid4(), 8)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)))
    repository = ChatNotificationRepository(session)
    assert await repository.advance(claim, now=now, cursor=cursor, complete=True) is False
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "chat_notifications.message_id =" in sql
    assert "chat_notifications.lease_id =" in sql
    assert "chat_notifications.leased_until >" in sql
    assert claim.lease_id in compiled.params.values()
    await repository.retry(claim, now=now)
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "cursor_token_id=" not in str(compiled)
    assert compiled.params["failed_at"] == now
    assert compiled.params["available_at"] == now + timedelta(seconds=256)
    assert "chat_notifications.lease_id =" in str(compiled)

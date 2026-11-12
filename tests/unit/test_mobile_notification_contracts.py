"""One-off reminder contract regressions."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.config import Environment
from app.integrations.push.fake import FakePushClient
from app.repositories.notification_reminder_repository import (
    NotificationReminderRepository,
    ReminderClaim,
)
from app.schemas.occasions import CreateOccasionRequest, UpdateOccasionRequest
from app.services import notification_reminder_service
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


def test_existing_caller_does_not_opt_in() -> None:
    request = CreateOccasionRequest(title="Birthday", occasion_date="2027-02-01")
    assert request.reminder_enabled is False


def test_disabling_is_distinct_from_omitting() -> None:
    request = UpdateOccasionRequest(reminder_enabled=False)
    assert request.reminder_enabled is False


async def test_reminder_device_page_rechecks_owner_and_customer_in_same_query() -> None:
    claim = ReminderClaim(uuid4(), uuid4(), 1, uuid4(), None, datetime.now(UTC))
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
    )
    assert await NotificationReminderRepository(session).token_page(claim) is None
    session.execute.assert_awaited_once()
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "occasions.user_id =" in sql and claim.user_id in compiled.params.values()
    assert "users.role =" in sql and "CUSTOMER" in compiled.params.values()
    assert "device_tokens.user_id =" in sql


async def test_ineligible_reminder_claim_does_not_send_or_finish(monkeypatch) -> None:
    claim = ReminderClaim(uuid4(), uuid4(), 1, uuid4(), None, datetime.now(UTC))
    session = AsyncMock()
    repository = SimpleNamespace(token_page=AsyncMock(return_value=None), finish=AsyncMock())
    monkeypatch.setattr(
        notification_reminder_service, "NotificationReminderRepository", lambda _: repository
    )
    push = SimpleNamespace(send_push=AsyncMock())

    assert not await notification_reminder_service._deliver_claim(
        claim, factory=lambda: session, push=push
    )
    push.send_push.assert_not_awaited()
    repository.finish.assert_not_awaited()


@pytest.mark.parametrize(("requested", "expected"), [(1000, 20), (0, 1)])
async def test_worker_clamps_device_pages(monkeypatch, requested, expected) -> None:
    session = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=context)
    repository = AsyncMock()
    repository.claim.return_value = object()
    monkeypatch.setattr(
        notification_reminder_service, "NotificationReminderRepository", lambda session: repository
    )
    monkeypatch.setattr(
        notification_reminder_service, "_deliver_claim", AsyncMock(return_value=True)
    )
    delivered = await notification_reminder_service.send_occasion_reminders(
        limit=requested,
        factory=factory,
        push=FakePushClient(Environment.TEST),
        settings=make_test_settings(),
    )
    assert delivered == expected
    assert repository.claim.await_count == expected

"""One-off reminder contract regressions."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import Environment
from app.integrations.push.fake import FakePushClient
from app.schemas.occasions import CreateOccasionRequest, UpdateOccasionRequest
from app.services import notification_reminder_service

from tests.conftest import make_test_settings


def test_existing_caller_does_not_opt_in() -> None:
    request = CreateOccasionRequest(title="Birthday", occasion_date="2027-02-01")
    assert request.reminder_enabled is False


def test_disabling_is_distinct_from_omitting() -> None:
    request = UpdateOccasionRequest(reminder_enabled=False)
    assert request.reminder_enabled is False


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

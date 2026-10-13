"""Annual recurrence remains opt-in and follows Gregorian leap years."""

from datetime import date

import pytest
from app.schemas.occasions import CreateOccasionRequest, UpdateOccasionRequest
from pydantic import ValidationError


def test_existing_occasions_default_to_none():
    body = CreateOccasionRequest(title="Birthday", occasion_date=date(2020, 2, 29))
    assert body.recurrence == "NONE"


def test_annual_is_explicit_and_invalid_modes_rejected():
    body = CreateOccasionRequest(
        title="Birthday", occasion_date=date(2020, 2, 29), recurrence="ANNUAL"
    )
    assert body.recurrence == "ANNUAL"
    with pytest.raises(ValidationError):
        CreateOccasionRequest(
            title="Birthday", occasion_date=date(2020, 2, 29), recurrence="MONTHLY"
        )
    with pytest.raises(ValidationError):
        UpdateOccasionRequest(recurrence=None)


@pytest.mark.parametrize("field", ["title", "occasion_date", "recurrence", "reminder_enabled"])
def test_partial_occasion_updates_reject_explicit_null(field):
    with pytest.raises(ValidationError):
        UpdateOccasionRequest.model_validate({field: None})


@pytest.mark.parametrize("mode", [None, "NONE", "ANNUAL"])
async def test_occasion_creation_preserves_old_idempotency_fingerprint(monkeypatch, mode):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.core.deps import Actor
    from app.models.enums import UserRole
    from app.routers import occasions

    from tests.conftest import make_test_settings

    values = {"title": "Birthday", "occasion_date": "2027-02-01"}
    if mode is not None:
        values["recurrence"] = mode
    body = CreateOccasionRequest.model_validate(values)
    actor = Actor(uuid4(), UserRole.CUSTOMER, "test")
    operations = AsyncMock()
    operations.begin.return_value = SimpleNamespace(result_encrypted=None)
    monkeypatch.setattr(occasions, "OperationService", lambda *args: operations)
    monkeypatch.setattr(occasions, "get_settings", lambda request: make_test_settings())
    service = AsyncMock()
    service.create.return_value = SimpleNamespace(
        id=uuid4(), **body.model_dump(), created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    monkeypatch.setattr(occasions, "_service", lambda db: service)
    await occasions.create_occasion(body, AsyncMock(), actor, SimpleNamespace(), uuid4())
    expected = {
        "title": "Birthday",
        "occasion_date": "2027-02-01",
        "reminder_enabled": False,
        "reminder_days_before": 7,
    }
    if mode == "ANNUAL":
        expected["recurrence"] = "ANNUAL"
    assert operations.begin.await_args.args[-1] == expected

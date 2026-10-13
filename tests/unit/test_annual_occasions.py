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

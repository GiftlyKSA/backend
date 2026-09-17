"""Authentication request phone-number normalization."""

from __future__ import annotations

import pytest
from app.schemas.auth import SendOtpRequest, VerifyOtpRequest
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("entered", "canonical"),
    [
        ("0501234567", "+966501234567"),
        ("501234567", "+966501234567"),
        ("+966 50 123 4567", "+966501234567"),
    ],
)
def test_otp_requests_normalize_common_saudi_mobile_entry(entered: str, canonical: str) -> None:
    assert SendOtpRequest(phone=entered).phone == canonical
    assert VerifyOtpRequest(phone=entered, otp="123456").phone == canonical


@pytest.mark.parametrize("entered", ["+971501234567", "050123456", "not-a-phone"])
def test_otp_requests_reject_invalid_mobile_numbers(entered: str) -> None:
    with pytest.raises(ValidationError):
        SendOtpRequest(phone=entered)

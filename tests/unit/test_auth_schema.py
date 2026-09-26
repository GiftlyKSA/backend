"""Authentication request phone-number normalization."""

from __future__ import annotations

import pytest
from app.schemas.auth import SendOtpRequest, SendOtpResponse, VerifyOtpRequest
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


def test_verify_request_accepts_five_digit_development_otp() -> None:
    assert VerifyOtpRequest(phone="0501234567", otp="12345").otp == "12345"
    for invalid in ("1234", "1234567"):
        with pytest.raises(ValidationError):
            VerifyOtpRequest(phone="0501234567", otp=invalid)


def test_development_otp_response_uses_numeric_otp_dev() -> None:
    response = SendOtpResponse(expires_in=180, otp_dev=12345)
    assert response.model_dump(exclude_none=True) == {"expires_in": 180, "otp_dev": 12345}

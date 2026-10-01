"""Withdrawal request contract validation."""

from __future__ import annotations

import pytest
from app.schemas.wallets import RejectWithdrawalRequest, WithdrawalRequest
from pydantic import ValidationError


def test_withdrawal_request_normalizes_and_masks_iban() -> None:
    body = WithdrawalRequest(amount="100.00", iban="sa03 8000 0000 6080 1016 7519")

    assert body.iban.get_secret_value() == "SA0380000000608010167519"
    assert "SA03" not in repr(body)


def test_withdrawal_request_rejects_non_saudi_iban() -> None:
    with pytest.raises(ValidationError):
        WithdrawalRequest(amount="100.00", iban="GB82WEST12345698765432")


@pytest.mark.parametrize(
    "model,body",
    [
        (
            WithdrawalRequest,
            {"amount": "100.00", "iban": "SA0380000000608010167519", "courier_id": "other"},
        ),
        (RejectWithdrawalRequest, {"reason": "Requested by courier", "status": "APPROVED"}),
    ],
)
def test_withdrawal_requests_reject_undeclared_fields(model, body) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(body)

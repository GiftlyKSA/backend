"""Withdrawal request contract validation."""

from __future__ import annotations

import pytest
from app.schemas.wallets import RejectWithdrawalRequest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "model,body",
    [
        (RejectWithdrawalRequest, {"reason": "Requested by courier", "status": "APPROVED"}),
    ],
)
def test_withdrawal_requests_reject_undeclared_fields(model, body) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(body)

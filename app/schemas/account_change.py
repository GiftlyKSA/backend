"""Explicit sensitive-operation scopes and restricted account-request receipts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.auth import SendOtpRequest, _normalize_saudi_mobile, _Otp, _Phone


class ReauthenticationRequest(BaseModel):
    """Choose the only operation authorized by this current-phone proof."""

    model_config = ConfigDict(extra="forbid")
    purpose: Literal["PHONE_CHANGE", "ACCOUNT_REQUEST"] = "PHONE_CHANGE"


class ChallengeResponse(BaseModel):
    """Metadata for a sixty-second scoped OTP; development code omitted elsewhere."""

    challenge_id: UUID
    expires_in: Literal[60] = 60
    resend_after: Literal[60] = 60
    otp_dev: int | None = None


class ChallengeConfirm(BaseModel):
    """Submit an OTP bound to the returned opaque challenge identifier."""

    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    otp: _Otp


class ReauthenticationResponse(BaseModel):
    """A one-use actor/purpose scoped proof that is never an access credential."""

    reauthentication_token: str
    expires_in: Literal[300] = 300


class PhoneChangeRequest(BaseModel):
    """The new canonical phone and a current-phone proof scoped to PHONE_CHANGE."""

    model_config = ConfigDict(extra="forbid")
    new_phone: _Phone
    reauthentication_token: str = Field(min_length=32, max_length=200)

    @field_validator("new_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: object) -> object:
        """Use the existing Saudi mobile normalizer."""
        return _normalize_saudi_mobile(value)


class PhoneChangeResponse(BaseModel):
    """The new login identity; all old credentials have been revoked."""

    phone: str
    reauthentication_required: Literal[True] = True


class AccountRequestCreate(BaseModel):
    """Request review after an explicitly confirmed, recoverable closure window."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["DELETE_ACCOUNT", "TERMINATE_CONTRACT"]
    confirmation: Literal["DELETE", "TERMINATE"]
    reauthentication_token: str = Field(min_length=32, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


class AccountRequestResponse(BaseModel):
    """Recovery and review state; no automatic erasure or financial settlement."""

    id: UUID
    kind: Literal["DELETE_ACCOUNT", "TERMINATE_CONTRACT"]
    status: Literal["RECOVERABLE", "PENDING_REVIEW", "RESTORED"]
    submitted_at: datetime
    updated_at: datetime
    restore_deadline: datetime
    administrative_review_required: Literal[True] = True
    financial_records_retained: Literal[True] = True
    receipt: str | None = Field(
        default=None,
        description=(
            "Restricted status credential. Send as X-Account-Request-Receipt; "
            "never as an access bearer token. Returned only on submission."
        ),
    )


class RestorationRequest(SendOtpRequest):
    """Current phone only; response does not disclose a matching account/request."""

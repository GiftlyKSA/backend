"""Pydantic contracts for the users endpoints (SPEC SECTION 19)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class CourierProfileResponse(BaseModel):
    """Safe courier details visible only through an authorized profile response."""

    city_of_residence: str
    bio: str | None = None
    verification_status: str
    rejection_reason: str | None = None
    avatar_url: str | None = None


class ParticipantProfile(BaseModel):
    """Minimal profile visible only to an order or conversation co-participant."""

    id: str
    display_name: str
    role: str
    rating: str
    rating_count: int
    initials: str
    avatar_url: str | None = None
    courier_city: str | None = None
    courier_bio: str | None = None


class UserMeResponse(BaseModel):
    """The authenticated user's own profile."""

    id: str = Field(..., description="User id.")
    phone: str = Field(..., description="E.164 phone.")
    role: str = Field(..., description="User role.")
    status: str = Field(..., description="Account status.")
    full_name: str | None = Field(None, description="Display name.")
    email: str | None = Field(None, description="Email, used only for the paid receipt.")
    rating: str = Field(..., description="Denormalized average rating as a decimal string.")
    rating_count: int = Field(..., description="Number of ratings received.")
    courier_profile: CourierProfileResponse | None = Field(
        None, description="Owner-only courier details; null for non-courier accounts."
    )


class UserUpdateRequest(BaseModel):
    """Editable profile fields."""

    model_config = ConfigDict(extra="forbid")
    full_name: Annotated[str, StringConstraints(max_length=120)] | None = None
    email: (
        Annotated[str, StringConstraints(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
        | None
    ) = None
    dob: date | None = None
    courier_city: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    courier_bio: Annotated[str, StringConstraints(max_length=1000)] | None = None

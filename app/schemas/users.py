"""Pydantic contracts for the users endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import UserGender


class CourierProfileResponse(BaseModel):
    """Safe courier details visible only through an authorized profile response."""

    city_of_residence: str
    city_of_residence_id: UUID
    bio: str | None = None
    verification_status: str
    rejection_reason: str | None = None
    avatar_url: str | None = None
    rating: str
    rating_count: int


class ParticipantProfile(BaseModel):
    """Minimal profile visible only to an order or conversation co-participant."""

    id: str
    display_name: str
    role: str
    public_identifier: int
    rating: str | None = None
    rating_count: int | None = None
    initials: str
    avatar_url: str | None = None
    courier_city: str | None = None
    courier_bio: str | None = None


class UserMeResponse(BaseModel):
    """The authenticated user's own profile."""

    id: str = Field(..., description="User id.")
    public_identifier: int = Field(..., description="Unique seven-digit public user identifier.")
    phone: str = Field(..., description="E.164 phone.")
    role: str = Field(..., description="User role.")
    status: str = Field(..., description="Account status.")
    full_name: str | None = Field(None, description="Display name.")
    email: str | None = Field(None, description="Email, used only for the paid receipt.")
    dob: date | None = None
    gender: UserGender | None = None
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
    gender: UserGender | None = None
    courier_city: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    courier_city_id: UUID | None = None
    courier_bio: Annotated[str, StringConstraints(max_length=1000)] | None = None

"""User profile routes (SPEC SECTION 19).

The client fetches profile data here rather than from the JWT (which carries only
ids), so edits take effect immediately. Ownership is implicit: the actor id comes from
the verified token, never the path or body.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_auth
from app.models import CourierProfile, User
from app.models.enums import UserStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.user_repository import UserRepository
from app.schemas.users import (
    CourierProfileResponse,
    ParticipantProfile,
    UserMeResponse,
    UserUpdateRequest,
)
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.user_service import ParticipantView, UserService

router = APIRouter(prefix="/api/users", tags=["users"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> UserService:
    return UserService(
        users=UserRepository(db),
        audit=AuditRepository(db),
        eligibility=CourierEligibilityService(
            users=UserRepository(db), couriers=CourierRepository(db)
        ),
    )


@router.get("/me", response_model=UserMeResponse)
async def get_me(db: DbDep, actor: Annotated[Actor, Depends(require_auth)]) -> UserMeResponse:
    """Return the authenticated user's own profile."""
    user, courier = await _service(db).get_me(actor.id)
    return _to_response(user, courier)


@router.patch("/me", response_model=UserMeResponse)
async def update_me(
    db: DbDep,
    body: UserUpdateRequest,
    actor: Annotated[Actor, Depends(require_auth)],
) -> UserMeResponse:
    """Update the authenticated user's editable profile fields."""
    user, courier = await _service(db).update_me(
        actor.id,
        full_name=body.full_name,
        email=body.email,
        dob=body.dob,
        courier_city=body.courier_city,
        courier_bio=body.courier_bio,
        supplied=body.model_fields_set,
    )
    return _to_response(user, courier)


@router.post("/me/courier-verification/resubmit", response_model=UserMeResponse)
async def resubmit_courier_verification(
    db: DbDep, actor: Annotated[Actor, Depends(require_auth)]
) -> UserMeResponse:
    """Resubmit a rejected courier profile for another audited review."""
    user, courier = await _service(db).resubmit_courier_verification(actor_id=actor.id)
    return _to_response(user, courier)


@router.get("/{user_id}/participant", response_model=ParticipantProfile)
async def get_participant_profile(
    db: DbDep,
    user_id: uuid.UUID,
    actor: Annotated[Actor, Depends(require_auth)],
) -> ParticipantProfile:
    """Return a minimal profile only after shared-order/conversation proof."""
    participant = await _service(db).get_participant(actor_id=actor.id, participant_id=user_id)
    return _participant_response(participant)


def _to_response(user: User, courier: CourierProfile | None) -> UserMeResponse:
    return UserMeResponse(
        id=str(user.id),
        phone=user.phone,
        role=str(user.role),
        status=str(user.status),
        full_name=user.full_name,
        email=user.email,
        rating=str(user.rating),
        rating_count=user.rating_count,
        courier_profile=(
            CourierProfileResponse(
                city_of_residence=courier.city_of_residence,
                bio=courier.bio,
                verification_status=str(user.status),
                rejection_reason=(
                    courier.verification_rejection_reason
                    if user.status is UserStatus.REJECTED
                    else None
                ),
                avatar_url=None,
            )
            if courier is not None
            else None
        ),
    )


def _participant_response(participant: ParticipantView) -> ParticipantProfile:
    return ParticipantProfile(
        id=str(participant.id),
        display_name=participant.display_name,
        role=str(participant.role),
        rating=str(participant.rating),
        rating_count=participant.rating_count,
        initials=participant.initials,
        avatar_url=None,
        courier_city=participant.courier_city,
        courier_bio=participant.courier_bio,
    )

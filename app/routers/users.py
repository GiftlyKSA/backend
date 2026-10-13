"""User profile routes.

The client fetches profile data here rather than from the JWT (which carries only
ids), so edits take effect immediately. Ownership is implicit: the actor id comes from
the verified token, never the path or body.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor, get_db, require_auth
from app.models import CourierProfile, User
from app.models.enums import UserStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.city_repository import CityRepository
from app.repositories.courier_avatar_repository import CourierAvatarRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.rating_repository import RatingRepository
from app.repositories.user_repository import UserRepository
from app.schemas.users import (
    CourierProfileResponse,
    ParticipantProfile,
    UserMeResponse,
    UserUpdateRequest,
)
from app.services.city_service import CityService
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.media_service import MediaService
from app.services.user_service import ParticipantView, UserService

router = APIRouter(prefix="/api/users", tags=["users"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> UserService:
    return UserService(
        users=UserRepository(db),
        audit=AuditRepository(db),
        cities=CityService(CityRepository(db)),
        eligibility=CourierEligibilityService(
            users=UserRepository(db), couriers=CourierRepository(db)
        ),
    )


@router.get("/me", response_model=UserMeResponse)
async def get_me(
    request: Request, response: Response, db: DbDep, actor: Annotated[Actor, Depends(require_auth)]
) -> UserMeResponse:
    """Return the authenticated user's own profile."""
    response.headers["Cache-Control"] = "private, no-store"
    user, courier = await _service(db).get_me(actor.id)
    return await _to_response(db, user, courier, request)


@router.patch("/me", response_model=UserMeResponse)
async def update_me(
    request: Request,
    response: Response,
    db: DbDep,
    body: UserUpdateRequest,
    actor: Annotated[Actor, Depends(require_auth)],
) -> UserMeResponse:
    """Update the authenticated user's editable profile fields."""
    response.headers["Cache-Control"] = "private, no-store"
    user, courier = await _service(db).update_me(
        actor.id,
        full_name=body.full_name,
        email=body.email,
        dob=body.dob,
        gender=body.gender,
        courier_city=body.courier_city,
        courier_city_id=body.courier_city_id,
        courier_bio=body.courier_bio,
        supplied=body.model_fields_set,
    )
    return await _to_response(db, user, courier, request)


@router.post(
    "/me/courier-verification/resubmit",
    response_model=UserMeResponse,
)
async def resubmit_courier_verification(
    request: Request, response: Response, db: DbDep, actor: Annotated[Actor, Depends(require_auth)]
) -> UserMeResponse:
    """Resubmit a rejected courier profile for another audited review."""
    response.headers["Cache-Control"] = "private, no-store"
    user, courier = await _service(db).resubmit_courier_verification(actor_id=actor.id)
    return await _to_response(db, user, courier, request)


@router.get(
    "/{user_id}/participant", response_model=ParticipantProfile, response_model_exclude_none=True
)
async def get_participant_profile(
    request: Request,
    response: Response,
    db: DbDep,
    user_id: uuid.UUID,
    actor: Annotated[Actor, Depends(require_auth)],
) -> ParticipantProfile:
    """Return a minimal profile only after shared-order/conversation proof."""
    response.headers["Cache-Control"] = "private, no-store"
    participant = await _service(db).get_participant(actor_id=actor.id, participant_id=user_id)
    return await _participant_response(db, participant, request)


async def _to_response(
    db: AsyncSession, user: User, courier: CourierProfile | None, request: Request | None = None
) -> UserMeResponse:
    rating, rating_count = (
        await RatingRepository(db).summary_for_user(user.id)
        if courier is not None
        else (None, None)
    )
    if courier is not None:
        assert rating is not None and rating_count is not None
    return UserMeResponse(
        id=str(user.id),
        public_identifier=user.public_identifier,
        phone=user.phone,
        role=str(user.role),
        status=str(user.status),
        full_name=user.full_name,
        email=user.email,
        dob=user.date_of_birth,
        gender=user.gender,
        courier_profile=(
            CourierProfileResponse(
                city_of_residence=courier.city_of_residence,
                city_of_residence_id=courier.city_of_residence_id,
                bio=courier.bio,
                verification_status=str(user.status),
                rejection_reason=(
                    courier.verification_rejection_reason
                    if user.status is UserStatus.REJECTED
                    else None
                ),
                avatar_url=await _avatar_url(db, user.id, request),
                rating=str(rating),
                rating_count=rating_count,
            )
            if courier is not None
            else None
        ),
    )


async def _participant_response(
    db: AsyncSession, participant: ParticipantView, request: Request | None = None
) -> ParticipantProfile:
    rating, rating_count = (
        await RatingRepository(db).summary_for_user(participant.id)
        if participant.role.value == "COURIER"
        else (None, None)
    )
    return ParticipantProfile(
        id=str(participant.id),
        public_identifier=participant.public_identifier,
        display_name=participant.display_name,
        role=str(participant.role),
        rating=str(rating) if rating is not None else None,
        rating_count=rating_count,
        initials=participant.initials,
        avatar_url=await _avatar_url(db, participant.id, request)
        if participant.role.value == "COURIER"
        else None,
        courier_city=participant.courier_city,
        courier_bio=participant.courier_bio,
    )


async def _avatar_url(db: AsyncSession, owner: uuid.UUID, request: Request | None) -> str | None:
    """Sign a private reference after the caller's profile authorization."""
    if request is None:
        return None
    key = await CourierAvatarRepository(db).get_key(owner)
    if key is None:
        return None
    return MediaService(
        request.app.state.clients.storage, request.app.state.settings, MediaRepository(db)
    ).avatar_read_url(key)

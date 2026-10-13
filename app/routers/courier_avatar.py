"""Courier private avatar attachment and removal."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import mark_request_transaction
from app.core.deps import Actor, get_db, require_auth
from app.core.exceptions import UnauthorizedError
from app.repositories.courier_avatar_repository import CourierAvatarRepository
from app.repositories.courier_repository import CourierRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.user_repository import UserRepository
from app.schemas.courier_avatar import AvatarRequest, AvatarResponse
from app.services.courier_avatar_service import CourierAvatarService
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.media_service import MediaService

router = APIRouter(prefix="/api/users/me/courier-avatar", tags=["users"])


@router.put("", response_model=AvatarResponse)
async def set_avatar(
    request: Request,
    response: Response,
    body: AvatarRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[Actor, Depends(require_auth)],
) -> AvatarResponse:
    """Attach a confirmed JPEG/PNG for an active verified courier."""
    eligibility = CourierEligibilityService(
        users=UserRepository(db), couriers=CourierRepository(db)
    )
    await eligibility.require_courier(actor.id)
    media = MediaService(
        request.app.state.clients.storage, request.app.state.settings, MediaRepository(db)
    )
    await media.prepare_claims(
        [body.storage_key], actor_id=actor.id, purpose="COURIER_AVATAR", release_reads=db.commit
    )
    await mark_request_transaction(db)
    if await require_auth(request, db) != actor:
        raise UnauthorizedError("This session is no longer valid.")
    avatar_url = media.avatar_read_url(body.storage_key)
    await CourierAvatarService(CourierAvatarRepository(db), eligibility).set_avatar(
        actor.id, body.storage_key, media
    )
    response.headers["Cache-Control"] = "private, no-store"
    return AvatarResponse(
        avatar_url=avatar_url,
        expires_in=300,
    )


@router.delete("", response_model=AvatarResponse)
async def remove_avatar(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[Actor, Depends(require_auth)],
) -> AvatarResponse:
    """Remove this courier's avatar without deleting another user's upload."""
    eligibility = CourierEligibilityService(
        users=UserRepository(db), couriers=CourierRepository(db)
    )
    await CourierAvatarService(CourierAvatarRepository(db), eligibility).remove_avatar(actor.id)
    response.headers["Cache-Control"] = "private, no-store"
    return AvatarResponse(avatar_url=None, expires_in=0)

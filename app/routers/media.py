"""Media routes.

Any authenticated user may request an upload URL and confirm it. The bytes never pass
through the API — the client PUTs straight to S3 with the pre-signed URL.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import mark_request_transaction
from app.core.deps import Actor, get_db, require_auth
from app.core.exceptions import UnauthorizedError
from app.integrations.storage.base import StorageClient
from app.repositories.courier_repository import CourierRepository
from app.repositories.media_repository import MediaRepository
from app.repositories.user_repository import UserRepository
from app.schemas.media import (
    ConfirmRequest,
    ConfirmResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.media_service import MediaService

router = APIRouter(prefix="/api/media", tags=["media"])


def _service(request: Request, db: AsyncSession) -> MediaService:
    storage: StorageClient = request.app.state.clients.storage
    return MediaService(storage, request.app.state.settings, MediaRepository(db))


@router.post("/upload-urls", response_model=UploadUrlResponse, status_code=201)
async def create_upload_url(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: UploadUrlRequest,
    actor: Annotated[Actor, Depends(require_auth)],
) -> UploadUrlResponse:
    """Issue a pre-signed upload URL with a server-generated key.

    Courier avatars require an active verified courier, real HTTPS storage outside test,
    JPEG/PNG and at most 10 MiB (or a lower configured cap). Storage failures return
    503 STORAGE_UNAVAILABLE. PUT the exact bytes with Content-Type, Content-Length and
    If-None-Match: *. URLs expire after 300 seconds; unclaimed avatar grants after 24 hours.
    """

    async def resume_writes() -> None:
        await mark_request_transaction(db)
        if await require_auth(request, db) != actor:
            raise UnauthorizedError("This session is no longer valid.")
        if body.purpose == "COURIER_AVATAR":
            await CourierEligibilityService(
                users=UserRepository(db), couriers=CourierRepository(db)
            ).require_courier(actor.id)

    if body.purpose == "COURIER_AVATAR":
        await CourierEligibilityService(
            users=UserRepository(db), couriers=CourierRepository(db)
        ).require_courier(actor.id)
    url, key, expires_in = await _service(request, db).request_upload_url(
        actor_id=actor.id,
        purpose=body.purpose,
        content_type=body.content_type,
        byte_size=body.byte_size,
        release_reads=db.commit,
        resume_writes=resume_writes,
    )
    return UploadUrlResponse(upload_url=url, storage_key=key, expires_in=expires_in)


@router.post("/confirm", response_model=ConfirmResponse, status_code=200)
async def confirm_upload(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: ConfirmRequest,
    actor: Annotated[Actor, Depends(require_auth)],
) -> ConfirmResponse:
    """Confirm an uploaded object exists and is a valid image."""

    async def resume_writes() -> None:
        await mark_request_transaction(db)
        if await require_auth(request, db) != actor:
            raise UnauthorizedError("This session is no longer valid.")

    await _service(request, db).confirm(
        body.storage_key,
        actor_id=actor.id,
        release_reads=db.commit,
        resume_writes=resume_writes,
    )
    return ConfirmResponse(storage_key=body.storage_key, confirmed=True)

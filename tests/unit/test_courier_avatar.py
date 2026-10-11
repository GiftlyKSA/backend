"""Courier avatar boundaries and ownership."""

import uuid
from unittest.mock import AsyncMock

import pytest
from app.core.exceptions import ForbiddenError
from app.services.courier_avatar_service import CourierAvatarService


@pytest.mark.asyncio
async def test_customer_cannot_set_avatar():
    eligibility = AsyncMock()
    eligibility.require_courier.side_effect = ForbiddenError("Courier required")
    repository, media = AsyncMock(), AsyncMock()
    service = CourierAvatarService(repository, eligibility)
    with pytest.raises(ForbiddenError):
        await service.set_avatar(uuid.uuid4(), "couriers/avatars/test.jpg", media)
    media.claim.assert_not_awaited()
    repository.set_avatar.assert_not_awaited()


@pytest.mark.asyncio
async def test_avatar_requires_owned_confirmed_purpose():
    repository, eligibility, media = AsyncMock(), AsyncMock(), AsyncMock()
    service = CourierAvatarService(repository, eligibility)
    owner = uuid.uuid4()
    key = f"couriers/avatars/{uuid.uuid4()}.jpg"
    await service.set_avatar(owner, key, media)
    media.claim.assert_awaited_once_with(key, actor_id=owner, purpose="COURIER_AVATAR")
    repository.set_avatar.assert_awaited_once_with(owner, key)


@pytest.mark.asyncio
async def test_avatar_rejects_image_header_without_valid_decodable_bytes(test_settings):
    from app.core.exceptions import BadRequestError
    from app.integrations.storage.fake import FakeStorageClient
    from app.repositories.media_repository import MediaRepository
    from app.services.media_service import MediaService, _UploadSnapshot

    storage = FakeStorageClient(test_settings.ENVIRONMENT)
    key = f"couriers/avatars/{uuid.uuid4()}.jpg"
    body = b"\xff\xd8\xffnot a decoded image"
    await storage.create_upload_url(
        storage_key=key, content_type="image/jpeg", byte_size=len(body), ttl_seconds=300
    )
    storage.recorded_bytes[key] = body
    service = MediaService(storage, test_settings, AsyncMock(spec=MediaRepository))
    with pytest.raises(BadRequestError):
        await service._verify_object(_UploadSnapshot(key, "image/jpeg", len(body)))

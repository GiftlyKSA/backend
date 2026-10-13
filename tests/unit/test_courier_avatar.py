"""Courier avatar boundaries and ownership."""

import uuid
from unittest.mock import AsyncMock

import pytest
from app.core.exceptions import ForbiddenError, StorageUnavailableError
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


async def test_development_fake_avatar_never_issues_successful_grant():
    from app.integrations.storage.fake import FakeStorageClient
    from app.services.media_service import MediaService

    from tests.conftest import make_test_settings

    settings = make_test_settings(ENVIRONMENT="development")
    uploads = AsyncMock()
    service = MediaService(FakeStorageClient(settings.ENVIRONMENT), settings, uploads)
    with pytest.raises(StorageUnavailableError) as error:
        await service.request_upload_url(
            actor_id=uuid.uuid4(), purpose="COURIER_AVATAR", content_type="image/png", byte_size=100
        )
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert error.value.status_code == 503
    uploads.issue.assert_not_awaited()


async def test_avatar_limit_never_exceeds_ten_mib(test_settings):
    from app.core.exceptions import BadRequestError
    from app.services.media_service import MediaService

    settings = test_settings.model_copy(update={"MAX_UPLOAD_BYTES": 20 * 1024 * 1024})
    storage, uploads = AsyncMock(), AsyncMock()
    service = MediaService(storage, settings, uploads)
    with pytest.raises(BadRequestError):
        await service.request_upload_url(
            actor_id=uuid.uuid4(),
            purpose="COURIER_AVATAR",
            content_type="image/jpeg",
            byte_size=10 * 1024 * 1024 + 1,
        )
    storage.create_upload_url.assert_not_awaited()


async def test_expired_avatar_grant_rejected_before_storage(test_settings):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.core.exceptions import BadRequestError
    from app.services.media_service import MediaService

    owner = uuid.uuid4()
    key = f"couriers/avatars/{uuid.uuid4()}.png"
    uploads, storage = AsyncMock(), AsyncMock()
    uploads.get.return_value = SimpleNamespace(
        owner_user_id=owner,
        storage_key=key,
        purpose="COURIER_AVATAR",
        created_at=datetime.now(UTC) - timedelta(days=2),
        attached_at=None,
        deleting_at=None,
        content_type="image/png",
        byte_size=100,
    )
    with pytest.raises(BadRequestError, match="expired"):
        await MediaService(storage, test_settings, uploads).confirm(key, actor_id=owner)
    storage.head_object.assert_not_awaited()


async def test_failed_replacement_preserves_existing_avatar():
    from app.core.exceptions import BadRequestError

    repository, eligibility, media = AsyncMock(), AsyncMock(), AsyncMock()
    media.claim.side_effect = BadRequestError("Invalid uploaded image.")
    with pytest.raises(BadRequestError):
        await CourierAvatarService(repository, eligibility).set_avatar(
            uuid.uuid4(), f"couriers/avatars/{uuid.uuid4()}.png", media
        )
    repository.set_avatar.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3000/avatar",
        "https://storage.internal/avatar",
        "https://other.example/avatar",
        "https://[invalid/avatar",
    ],
)
def test_avatar_signer_rejects_unexpected_origins(url):
    from unittest.mock import Mock

    from app.services.media_service import MediaService

    from tests.conftest import make_test_settings

    settings = make_test_settings(
        ENVIRONMENT="development",
        STORAGE_PROVIDER="s3",
        S3_ENDPOINT="https://objects.example.com",
        S3_BUCKET="private-media",
        S3_ACCESS_KEY_ID="test-access",
        S3_SECRET_ACCESS_KEY="test-secret",
    )
    storage = Mock()
    storage.signed_read_url.return_value = url
    with pytest.raises(StorageUnavailableError):
        MediaService(storage, settings, AsyncMock()).avatar_read_url("couriers/avatars/test.png")


async def test_fake_cleanup_cannot_remove_real_storage_grant_metadata():
    from app.services.media_cleanup_service import clean_abandoned_uploads

    from tests.conftest import make_test_settings

    factory = AsyncMock()
    assert (
        await clean_abandoned_uploads(
            factory=factory, settings=make_test_settings(ENVIRONMENT="development")
        )
        == 0
    )
    factory.assert_not_called()


def test_avatar_read_urls_renew_without_changing_reference():
    from unittest.mock import Mock

    from app.services.media_service import MediaService

    from tests.conftest import make_test_settings

    settings = make_test_settings(
        ENVIRONMENT="development",
        STORAGE_PROVIDER="s3",
        S3_ENDPOINT="https://objects.example.com",
        S3_BUCKET="private-media",
        S3_ACCESS_KEY_ID="test-access",
        S3_SECRET_ACCESS_KEY="test-secret",
    )
    storage = Mock()
    storage.signed_read_url.side_effect = [
        "https://objects.example.com/avatar?signature=one",
        "https://objects.example.com/avatar?signature=two",
    ]
    service = MediaService(storage, settings, AsyncMock())
    key = f"couriers/avatars/{uuid.uuid4()}.png"
    assert service.avatar_read_url(key) != service.avatar_read_url(key)
    assert storage.signed_read_url.call_count == 2
    storage.signed_read_url.assert_called_with(key, ttl_seconds=300)


async def test_avatar_confirmation_rechecks_jpeg_png_policy(test_settings):
    from app.core.exceptions import BadRequestError
    from app.integrations.storage.base import ObjectHead
    from app.services.media_service import MediaService, _UploadSnapshot

    storage = AsyncMock()
    storage.head_object.return_value = ObjectHead(True, 10, "image/heic")
    snapshot = _UploadSnapshot(f"couriers/avatars/{uuid.uuid4()}.heic", "image/heic", 10)
    with pytest.raises(BadRequestError, match="permitted image"):
        await MediaService(storage, test_settings, AsyncMock())._verify_object(snapshot)
    storage.read_bounded_object.assert_not_awaited()

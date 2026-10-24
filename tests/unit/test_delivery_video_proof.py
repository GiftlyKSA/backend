"""Video uploads are accepted only for bounded delivery proof."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import BadRequestError
from app.integrations.storage.base import ObjectHead
from app.schemas.fulfillment import DeliverRequest
from app.schemas.media import UploadUrlRequest
from app.services import media_service
from app.services.media_service import MediaService
from pydantic import ValidationError

from tests.conftest import make_test_settings


def test_video_upload_schema_allows_videos_only_for_delivery_proof() -> None:
    request = UploadUrlRequest(
        purpose="DELIVERY_PROOF",
        content_type="video/mp4",
        byte_size=1024,
    )
    assert request.content_type == "video/mp4"
    with pytest.raises(ValidationError):
        UploadUrlRequest(
            purpose="ORDER_REQUEST",
            content_type="video/mp4",
            byte_size=1024,
        )
    with pytest.raises(ValidationError):
        UploadUrlRequest(
            purpose="COURIER_AVATAR",
            content_type="video/mp4",
            byte_size=1024,
        )


def test_delivery_proof_accepts_up_to_ten_media_keys() -> None:
    keys = [f"orders/proof/{uuid4()}.jpg" for _ in range(10)]
    assert DeliverRequest(proof_media_keys=keys).proof_media_keys == keys
    with pytest.raises(ValidationError):
        DeliverRequest(proof_media_keys=keys + [f"orders/proof/{uuid4()}.jpg"])


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        ("video/mp4", "mp4"),
        ("video/webm", "webm"),
        ("video/quicktime", "mov"),
    ],
)
async def test_delivery_video_grant_uses_existing_video_limits_and_extension(
    content_type: str, extension: str
) -> None:
    actor_id = uuid4()
    storage, uploads = AsyncMock(), AsyncMock()
    settings = make_test_settings()
    byte_size = settings.CHAT_VIDEO_MAX_UPLOAD_BYTES
    uploads.issue.return_value = None
    storage.create_upload_url.return_value = "https://storage.example/upload"
    service = MediaService(storage, settings, uploads)

    _url, storage_key, expires_in = await service.request_upload_url(
        actor_id=actor_id,
        purpose="DELIVERY_PROOF",
        content_type=content_type,
        byte_size=byte_size,
    )

    assert storage_key.startswith("orders/proof/") and storage_key.endswith(f".{extension}")
    assert expires_in == 300
    storage.create_upload_url.assert_awaited_once_with(
        storage_key=storage_key,
        content_type=content_type,
        byte_size=byte_size,
        ttl_seconds=300,
    )
    issued = uploads.issue.await_args.kwargs
    assert issued["max_bytes"] >= 10 * byte_size


async def test_delivery_video_confirmation_uses_bounded_recording_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = uuid4()
    storage_key = f"orders/proof/{uuid4()}.mov"
    byte_size = 4096
    grant = SimpleNamespace(
        storage_key=storage_key,
        owner_user_id=actor_id,
        purpose="DELIVERY_PROOF",
        content_type="video/quicktime",
        byte_size=byte_size,
        created_at=datetime.now(UTC),
        attached_at=None,
        deleting_at=None,
    )
    storage, uploads = AsyncMock(), AsyncMock()
    storage.head_object.return_value = ObjectHead(True, byte_size, "video/quicktime")
    uploads.get.return_value = grant
    uploads.mark_confirmed.return_value = True
    settings = make_test_settings()
    service = MediaService(storage, settings, uploads)
    validation_path = Path("delivery-proof.mov")

    @asynccontextmanager
    async def bounded_file(_storage, _key: str, *, max_bytes: int):
        assert max_bytes == byte_size
        yield validation_path

    verify = AsyncMock(return_value=1.0)
    monkeypatch.setattr(media_service, "bounded_recording_file", bounded_file, raising=False)
    monkeypatch.setattr(media_service, "verify_recording_file", verify)

    await service.confirm(storage_key, actor_id=actor_id)

    verify.assert_awaited_once_with(
        validation_path,
        kind="VIDEO",
        mime="video/quicktime",
        maximum=settings.CHAT_VIDEO_MAX_DURATION_SECONDS,
    )
    storage.read_bounded_object.assert_not_awaited()
    uploads.mark_confirmed.assert_awaited_once_with(storage_key, actor_id)


async def test_order_request_service_rejects_video_even_without_schema_validation() -> None:
    service = MediaService(AsyncMock(), make_test_settings(), AsyncMock())
    with pytest.raises(BadRequestError):
        await service.request_upload_url(
            actor_id=uuid4(),
            purpose="ORDER_REQUEST",
            content_type="video/mp4",
            byte_size=1024,
        )


async def test_video_grant_rejects_size_above_configured_cap() -> None:
    storage, uploads = AsyncMock(), AsyncMock()
    settings = make_test_settings()
    service = MediaService(storage, settings, uploads)

    with pytest.raises(BadRequestError):
        await service.request_upload_url(
            actor_id=uuid4(),
            purpose="DELIVERY_PROOF",
            content_type="video/mp4",
            byte_size=settings.CHAT_VIDEO_MAX_UPLOAD_BYTES + 1,
        )

    storage.create_upload_url.assert_not_awaited()
    uploads.issue.assert_not_awaited()


async def test_video_validation_failure_does_not_confirm_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = uuid4()
    storage_key = f"orders/proof/{uuid4()}.mp4"
    byte_size = 4096
    grant = SimpleNamespace(
        storage_key=storage_key,
        owner_user_id=actor_id,
        purpose="DELIVERY_PROOF",
        content_type="video/mp4",
        byte_size=byte_size,
        created_at=datetime.now(UTC),
        attached_at=None,
        deleting_at=None,
    )
    storage, uploads = AsyncMock(), AsyncMock()
    storage.head_object.return_value = ObjectHead(True, byte_size, "video/mp4")
    uploads.get.return_value = grant
    service = MediaService(storage, make_test_settings(), uploads)

    @asynccontextmanager
    async def bounded_file(_storage, _key: str, *, max_bytes: int):
        assert max_bytes == byte_size
        yield Path("invalid-proof.mp4")

    verify = AsyncMock(side_effect=BadRequestError("Invalid recording."))
    monkeypatch.setattr(media_service, "bounded_recording_file", bounded_file, raising=False)
    monkeypatch.setattr(media_service, "verify_recording_file", verify, raising=False)

    with pytest.raises(BadRequestError):
        await service.confirm(storage_key, actor_id=actor_id)

    uploads.mark_confirmed.assert_not_awaited()

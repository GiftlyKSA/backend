"""Order photos and delivery proof require complete, bounded image decoding."""

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import BadRequestError
from app.integrations.storage.base import ObjectHead
from app.services.media_service import MediaService
from PIL import Image

from tests.conftest import make_test_settings


def image_body(mime: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG" if mime == "image/png" else "JPEG")
    return output.getvalue()


def workflow(purpose: str, mime: str, body: bytes):
    actor = uuid4()
    prefix = "orders/pending" if purpose == "ORDER_REQUEST" else "orders/proof"
    suffix = "png" if mime == "image/png" else "jpg"
    key = f"{prefix}/{uuid4()}.{suffix}"
    grant = SimpleNamespace(
        storage_key=key,
        owner_user_id=actor,
        purpose=purpose,
        content_type=mime,
        byte_size=len(body),
        created_at=datetime.now(UTC),
        attached_at=None,
        deleting_at=None,
    )
    storage, uploads = AsyncMock(), AsyncMock()
    storage.head_object.return_value = ObjectHead(True, len(body), mime)
    storage.read_bounded_object.return_value = body
    storage.verify_image_magic_bytes.return_value = True
    uploads.get.return_value = grant
    uploads.mark_confirmed.return_value = True
    return MediaService(storage, make_test_settings(), uploads), storage, uploads, grant, actor


@pytest.mark.parametrize("purpose", ["ORDER_REQUEST", "DELIVERY_PROOF"])
@pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
async def test_order_confirmation_rejects_header_with_invalid_contents(purpose, mime):
    prefix = b"\xff\xd8\xff" if mime == "image/jpeg" else b"\x89PNG\r\n\x1a\n"
    service, storage, uploads, grant, actor = workflow(purpose, mime, prefix + b"not-an-image")
    with pytest.raises(BadRequestError):
        await service.confirm(grant.storage_key, actor_id=actor)
    storage.read_bounded_object.assert_awaited_once_with(
        grant.storage_key, max_bytes=grant.byte_size
    )
    uploads.mark_confirmed.assert_not_awaited()


@pytest.mark.parametrize("purpose", ["ORDER_REQUEST", "DELIVERY_PROOF"])
@pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
async def test_order_confirmation_accepts_actual_decodable_image(purpose, mime):
    service, storage, uploads, grant, actor = workflow(purpose, mime, image_body(mime))
    await service.confirm(grant.storage_key, actor_id=actor)
    storage.read_bounded_object.assert_awaited_once_with(
        grant.storage_key, max_bytes=grant.byte_size
    )
    uploads.mark_confirmed.assert_awaited_once_with(grant.storage_key, actor)


async def test_order_confirmation_rejects_body_that_does_not_match_image_type():
    service, _, uploads, grant, actor = workflow(
        "ORDER_REQUEST", "image/png", image_body("image/jpeg")
    )
    with pytest.raises(BadRequestError):
        await service.confirm(grant.storage_key, actor_id=actor)
    uploads.mark_confirmed.assert_not_awaited()


async def test_order_confirmation_rejects_short_download_even_when_head_matches():
    service, storage, uploads, grant, actor = workflow(
        "ORDER_REQUEST", "image/png", image_body("image/png")
    )
    storage.read_bounded_object.return_value = b""
    with pytest.raises(BadRequestError):
        await service.confirm(grant.storage_key, actor_id=actor)
    uploads.mark_confirmed.assert_not_awaited()


async def test_order_confirmation_rejects_excessive_decoded_dimensions():
    import struct
    import zlib

    body = bytearray(image_body("image/png"))
    struct.pack_into(">II", body, 16, 5000, 5000)
    struct.pack_into(">I", body, 29, zlib.crc32(body[12:29]))
    service, _, uploads, grant, actor = workflow("ORDER_REQUEST", "image/png", bytes(body))
    with pytest.raises(BadRequestError):
        await service.confirm(grant.storage_key, actor_id=actor)
    uploads.mark_confirmed.assert_not_awaited()


async def test_order_validation_timeout_fails_without_confirmation():
    from app.core.exceptions import MediaValidationUnavailableError

    service, storage, uploads, grant, actor = workflow(
        "ORDER_REQUEST", "image/png", image_body("image/png")
    )
    storage.read_bounded_object.side_effect = TimeoutError
    with pytest.raises(MediaValidationUnavailableError):
        await service.confirm(grant.storage_key, actor_id=actor)
    uploads.mark_confirmed.assert_not_awaited()


async def test_order_validation_rejects_stream_exceeding_declared_size():
    service, storage, uploads, grant, actor = workflow(
        "DELIVERY_PROOF", "image/png", image_body("image/png")
    )
    storage.read_bounded_object.side_effect = ValueError("Synthetic oversized stream")
    with pytest.raises(BadRequestError):
        await service.confirm(grant.storage_key, actor_id=actor)
    uploads.mark_confirmed.assert_not_awaited()

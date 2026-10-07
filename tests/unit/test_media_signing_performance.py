"""Keep signing off the event loop and reuse encryption setup for bounded pages."""

import asyncio
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.crypto import CryptoError, blob_version, build_aad, build_cipher
from app.models.enums import MediaType, MessageType
from app.services.chat_service import ChatService
from app.services.order_media_read_service import OrderMediaReadService

from tests.conftest import make_test_settings
from tests.unit.test_chat_media_service import stack
from tests.unit.test_integrations_extra import _private_key_pem


class ResponsiveSigner:
    def __init__(self):
        self.loop_progress = threading.Event()
        self.calls = []

    def signed_read_url(self, key, *, ttl_seconds):
        progressed = self.loop_progress.wait(timeout=0.2)
        self.calls.append((key, ttl_seconds, threading.get_ident(), progressed))
        return f"https://signed.example/{key}"


@pytest.mark.parametrize("count", [1, 100])
async def test_order_media_signing_yields_and_uses_one_worker_per_page(count):
    orders = AsyncMock()
    orders.page_media_for_actor.return_value = [
        SimpleNamespace(
            id=uuid4(),
            media_type=MediaType.CUSTOMER_REQUEST,
            content_type="image/jpeg",
            byte_size=12,
            created_at=datetime.now(UTC),
            storage_key=f"orders/{index}",
        )
        for index in range(count + 1)
    ]
    signer = ResponsiveSigner()
    asyncio.get_running_loop().call_soon(signer.loop_progress.set)
    page = await OrderMediaReadService(orders, AsyncMock(), signer).list(
        uuid4(),
        uuid4(),
        purpose=None,
        limit=count,
        cursor=None,
    )
    assert len(page.items) == count
    assert page.next_cursor == str(page.items[-1].id)
    assert len(signer.calls) == count
    assert all(ttl == 300 and progressed for _, ttl, _, progressed in signer.calls)
    assert len({worker for _, _, worker, _ in signer.calls}) == 1
    assert signer.calls[0][2] != threading.get_ident()
    assert page.items[-1].access_url == f"https://signed.example/orders/{count - 1}"


async def test_chat_playback_signing_yields_after_authorization():
    service, deps = stack()
    signer = ResponsiveSigner()
    deps.repository.attachment_for_actor.return_value = SimpleNamespace(storage_key="chat/private")
    service._storage = signer
    asyncio.get_running_loop().call_soon(signer.loop_progress.set)
    assert await service.playback(attachment_id=uuid4(), actor_id=uuid4()) == (
        "https://signed.example/chat/private"
    )
    assert signer.calls == [("chat/private", 300, signer.calls[0][2], True)]
    assert signer.calls[0][2] != threading.get_ident()


@pytest.mark.parametrize("count", [1, 50])
async def test_chat_cipher_setup_is_constant_for_history_and_inbox(count):
    settings = make_test_settings()
    cipher = build_cipher(settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION)
    conversation, actor = uuid4(), uuid4()
    repository = AsyncMock()
    repository.list_messages.return_value = [
        SimpleNamespace(
            id=uuid4(),
            conversation_id=conversation,
            sender_id=actor,
            message_type=MessageType.TEXT,
            is_read=False,
            created_at=datetime.now(UTC),
            content_encrypted=cipher.encrypt(
                "Hello", build_aad("messages", "content", str(conversation))
            ),
        )
        for _ in range(count)
    ]
    repository.attachments_for_messages.return_value = []
    repository.list_for_user.return_value = [
        SimpleNamespace(
            id=conversation,
            order_id=uuid4(),
            customer_id=actor,
            courier_id=uuid4(),
            customer_unread_count=1,
            courier_unread_count=0,
            last_message_timestamp=datetime.now(UTC),
            last_message_preview_encrypted=cipher.encrypt(
                "Hello",
                build_aad("conversations", "last_message_preview", str(conversation)),
            ),
        )
        for _ in range(count)
    ]
    with patch("app.services.chat_service.build_cipher", wraps=build_cipher) as constructor:
        service = ChatService(
            chat=repository, redis=AsyncMock(), settings=settings, eligibility=AsyncMock()
        )
        messages = await service.list_messages(
            conversation_id=conversation,
            actor_id=actor,
            limit=count,
            before_id=None,
        )
        inbox = await service.list_inbox(user_id=actor, limit=count, before=None)
    assert [m.content for m in messages] == ["Hello"] * count
    assert [item.last_message_preview for item in inbox] == ["Hello"] * count
    assert constructor.call_count == 1


def test_service_cipher_keeps_rotation_snapshot_and_aad_binding():
    import base64
    import json

    keys = {1: bytes(32), 2: bytes([1]) * 32}
    settings = make_test_settings(
        FIELD_ENCRYPTION_KEYS=json.dumps(
            {str(v): base64.b64encode(k).decode() for v, k in keys.items()}
        ),
        FIELD_ENCRYPTION_KEY_VERSION=2,
    )
    service = ChatService(
        chat=AsyncMock(), redis=AsyncMock(), settings=settings, eligibility=AsyncMock()
    )
    conversation = uuid4()
    old = build_cipher(keys, 1).encrypt("old", build_aad("messages", "content", str(conversation)))
    settings.FIELD_ENCRYPTION_KEY_VERSION = 1
    assert service._decrypt_content(conversation, old) == "old"
    encrypted = service._encrypt_content(conversation, "new")
    assert blob_version(encrypted) == 2
    with pytest.raises(CryptoError):
        service._decrypt_content(uuid4(), encrypted)
    with pytest.raises(CryptoError):
        service._decrypt_preview(conversation, encrypted)


def test_cloudfront_signer_setup_is_constant_across_read_urls():
    from app.integrations.storage.real import S3StorageClient
    from botocore.signers import CloudFrontSigner

    with patch(
        "app.integrations.storage.real.CloudFrontSigner", wraps=CloudFrontSigner
    ) as constructor:
        client = S3StorageClient(
            bucket="private",
            region="me-south-1",
            access_key_id="testing",
            secret_access_key="testing",
            cloudfront_domain="private.example",
            cloudfront_key_pair_id="test-pair",
            cloudfront_private_key=_private_key_pem(),
        )
        urls = [client.signed_read_url(f"orders/{index}", ttl_seconds=300) for index in range(50)]
    assert all("Signature=" in url and "Hash-Algorithm=SHA256" in url for url in urls)
    assert constructor.call_count == 1

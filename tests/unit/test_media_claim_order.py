"""A multi-key attachment acquires grant locks in one order."""

from __future__ import annotations

import uuid

import pytest
from app.core.exceptions import ConflictError
from app.integrations.storage.base import ObjectHead
from app.services.media_service import MediaService


class _RecordingMediaService(MediaService):
    def __init__(self) -> None:
        self.claimed: list[str] = []

    async def claim(self, storage_key: str, *, actor_id: uuid.UUID, purpose: str) -> ObjectHead:
        self.claimed.append(storage_key)
        return ObjectHead(exists=True, byte_size=100, content_type="image/jpeg")


async def test_multi_key_claims_are_sorted_and_duplicate_keys_rejected() -> None:
    media = _RecordingMediaService()
    actor_id = uuid.uuid4()
    heads = await media.claim_many(
        ["orders/pending/b.jpg", "orders/pending/a.jpg"], actor_id=actor_id, purpose="ORDER_REQUEST"
    )
    assert media.claimed == ["orders/pending/a.jpg", "orders/pending/b.jpg"]
    assert set(heads) == set(media.claimed)

    with pytest.raises(ConflictError):
        await media.claim_many(
            ["orders/pending/a.jpg", "orders/pending/a.jpg"],
            actor_id=actor_id,
            purpose="ORDER_REQUEST",
        )
    assert media.claimed == ["orders/pending/a.jpg", "orders/pending/b.jpg"]

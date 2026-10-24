"""Bounded order media attachments are written in one ORM flush."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.models.enums import MediaType
from app.repositories.order_repository import NewOrderMedia, OrderRepository


async def test_add_media_many_flushes_a_ten_item_delivery_batch_once() -> None:
    session = Mock()
    session.flush = AsyncMock()
    media = [
        NewOrderMedia(
            storage_key=f"orders/proof/{uuid4()}.mp4",
            content_type="video/mp4",
            byte_size=1024,
        )
        for _ in range(10)
    ]

    await OrderRepository(session).add_media_many(
        order_id=uuid4(),
        uploaded_by_user_id=uuid4(),
        media_type=MediaType.DELIVERY_PROOF,
        media=media,
    )

    inserted = session.add_all.call_args.args[0]
    assert len(inserted) == 10
    assert {item.storage_key for item in inserted} == {item.storage_key for item in media}
    assert all(item.media_type is MediaType.DELIVERY_PROOF for item in inserted)
    session.flush.assert_awaited_once_with()

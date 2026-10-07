from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models.enums import MediaType
from app.repositories.order_repository import OrderRepository
from app.services.order_media_read_service import OrderMediaReadService
from sqlalchemy.dialects import postgresql


async def test_foreign_order_cannot_generate_signed_media_urls():
    orders = AsyncMock()
    orders.get_for_actor.return_value = None
    storage = Mock()
    service = OrderMediaReadService(orders, AsyncMock(), storage)
    with pytest.raises(NotFoundError):
        await service.list(uuid4(), uuid4(), purpose=None, limit=25, cursor=None)
    storage.signed_read_url.assert_not_called()
    orders.page_media_for_actor.assert_not_awaited()


async def test_media_page_exposes_only_owned_short_lived_urls_and_trusted_metadata():
    orders = AsyncMock()
    rows = [
        SimpleNamespace(
            id=uuid4(),
            media_type=MediaType.CUSTOMER_REQUEST,
            content_type="image/jpeg",
            byte_size=100,
            storage_key=f"private/key/{index}",
            created_at=datetime.now(UTC),
        )
        for index in range(3)
    ]
    orders.page_media_for_actor.return_value = rows
    storage = Mock()
    storage.signed_read_url.return_value = "https://signed.example/object"
    result = await OrderMediaReadService(orders, AsyncMock(), storage).list(
        uuid4(),
        uuid4(),
        purpose="ORDER_REQUEST",
        limit=2,
        cursor=None,
    )
    assert len(result.items) == 2
    assert result.next_cursor == str(rows[1].id)
    assert result.items[0].purpose == "ORDER_REQUEST"
    assert "storage_key" not in result.items[0].model_dump()
    assert storage.signed_read_url.call_count == 2
    assert storage.signed_read_url.call_args.kwargs["ttl_seconds"] == 300


async def test_media_anchor_is_scoped_to_order_actor_and_purpose():
    session = AsyncMock()
    session.scalar.return_value = None
    with pytest.raises(NotFoundError):
        await OrderRepository(session).page_media_for_actor(
            uuid4(),
            uuid4(),
            purpose=MediaType.DELIVERY_PROOF,
            limit=26,
            cursor=uuid4(),
        )
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "orders.customer_id" in sql and "orders.courier_id" in sql
    assert "order_media.order_id" in sql and "order_media.media_type" in sql

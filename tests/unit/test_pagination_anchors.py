"""Pagination must not restart or use an anchor from another owner's list."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models import Order, Transaction
from app.models.enums import OrderStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.wallet_repository import WalletRepository
from sqlalchemy.dialects import postgresql


@pytest.mark.parametrize("kind", ["customer", "courier", "available", "wallet"])
@pytest.mark.parametrize("exists_globally", [False, True])
async def test_missing_or_out_of_scope_cursor_does_not_restart_list(kind, exists_globally):
    session = AsyncMock()
    session.scalar.return_value = None
    session.get.return_value = (
        SimpleNamespace(id=uuid4(), created_at=datetime.now(UTC)) if exists_globally else None
    )
    session.scalars.return_value = []
    owner_id, cursor_id = uuid4(), uuid4()
    with pytest.raises(NotFoundError):
        if kind == "wallet":
            await WalletRepository(session).list_transactions(
                owner_id, limit=20, before_id=cursor_id
            )
        elif kind == "available":
            await OrderRepository(session).list_available(owner_id, limit=20, before_id=cursor_id)
        else:
            method = getattr(OrderRepository(session), f"list_for_{kind}")
            await method(owner_id, status=OrderStatus.NEW, limit=20, before_id=cursor_id)

    anchor_query = session.scalar.call_args.args[0]
    compiled = anchor_query.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    scope_column = {
        "customer": "customer_id",
        "courier": "courier_id",
        "available": "delivery_city_id",
        "wallet": "wallet_id",
    }[kind]
    assert f"{scope_column} =" in sql
    assert owner_id in compiled.params.values()
    assert cursor_id in compiled.params.values()
    if kind != "wallet":
        assert "orders.status =" in sql


@pytest.mark.parametrize("kind", ["order", "wallet"])
async def test_valid_anchor_preserves_stable_timestamp_id_keyset(kind):
    session = AsyncMock()
    anchor = SimpleNamespace(id=uuid4(), created_at=datetime(2026, 1, 1, tzinfo=UTC))
    session.scalar.return_value = anchor
    session.get.return_value = anchor
    session.scalars.return_value = ["older-row"]
    if kind == "wallet":
        result = await WalletRepository(session).list_transactions(
            uuid4(), limit=20, before_id=anchor.id
        )
        model = Transaction
    else:
        result = await OrderRepository(session).list_for_customer(
            uuid4(), status=None, limit=20, before_id=anchor.id
        )
        model = Order
    assert result == ["older-row"]
    compiled = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert f"({model.__tablename__}.created_at, {model.__tablename__}.id) <" in str(compiled)
    assert anchor.created_at in compiled.params.values()
    assert anchor.id in compiled.params.values()

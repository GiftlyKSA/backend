"""Customer promo catalog response behavior without external services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.core.deps import Actor
from app.models.enums import PromoDiscountType, UserRole
from app.routers import promos
from fastapi import Response


@pytest.mark.asyncio
async def test_promo_catalog_returns_display_fields_without_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serialize a bounded customer catalog without exposing internal promo IDs."""
    created_at = datetime(2026, 10, 24, tzinfo=UTC)
    promo = SimpleNamespace(
        id=uuid.uuid4(),
        code="WELCOME10",
        description="Ten percent off",
        discount_type=PromoDiscountType.PERCENT,
        percent_value=Decimal("10.00"),
        fixed_amount=None,
        max_discount_amount=Decimal("100.00"),
        min_order_amount=Decimal("50.00"),
        starts_at=None,
        ends_at=None,
        created_at=created_at,
    )

    class FakePromoRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def list_available(self, *, now: datetime, limit: int) -> list[object]:
            assert now.tzinfo is UTC
            assert limit == 25
            return [promo]

    monkeypatch.setattr(promos, "PromoRepository", FakePromoRepository)
    response = Response()
    result = await promos.list_available_promos(
        db=object(),
        response=response,
        _actor=Actor(id=uuid.uuid4(), role=UserRole.CUSTOMER, jti="test"),
        limit=25,
    )

    item = result.items[0].model_dump(mode="json")
    assert item["code"] == "WELCOME10"
    assert item["discount_type"] == "PERCENT"
    assert item["percent_value"] == "10.00"
    assert item["fixed_amount"] is None
    assert item["max_discount_amount"] == "100.00"
    assert item["min_order_amount"] == "50.00"
    assert "id" not in item
    assert response.headers["Cache-Control"] == "private, no-store"

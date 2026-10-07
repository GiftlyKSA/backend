from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.models.enums import InvoiceStatus, UserRole
from app.repositories.invoice_repository import InvoiceRepository
from app.services.mobile_invoice_service import MobileInvoiceService
from app.services.reporting_dates import reporting_bounds
from sqlalchemy.dialects import postgresql


def test_reporting_dates_use_inclusive_riyadh_calendar_days():
    start, end = reporting_bounds(date(2026, 10, 1), date(2026, 10, 1))
    assert start == datetime(2026, 9, 30, 21, tzinfo=UTC)
    assert end == datetime(2026, 10, 1, 21, tzinfo=UTC)


def test_reporting_dates_preserve_open_bounds():
    assert reporting_bounds(None, None) == (None, None)


def test_reporting_end_at_first_calendar_day_remains_representable():
    assert reporting_bounds(None, date.min) == (None, datetime(1, 1, 1, 21, tzinfo=UTC))


async def test_invoice_list_owns_anchor_and_applies_filters_before_limit():
    session = AsyncMock()
    session.scalar.return_value = None
    actor = uuid4()
    with pytest.raises(NotFoundError):
        await InvoiceRepository(session).list_for_actor(
            actor,
            role=UserRole.CUSTOMER,
            limit=26,
            cursor=uuid4(),
            status=InvoiceStatus.PAID,
            include_historical=False,
            start=datetime(2026, 10, 1, tzinfo=UTC),
            end=None,
        )
    query = session.scalar.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "orders.customer_id" in sql
    assert "invoices.issued_at >=" in sql
    assert "invoices.status" in sql
    assert "LIMIT" not in sql


async def test_invoice_list_overfetches_and_serializes_money_without_item_queries():
    repo = AsyncMock()
    now = datetime(2026, 10, 6, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=uuid4(),
            order_id=uuid4(),
            status=InvoiceStatus.PAID,
            currency="SAR",
            total_amount=Decimal("125.00"),
            issued_at=now,
            expires_at=now,
        )
        for _ in range(3)
    ]
    repo.list_for_actor.return_value = [(row, True) for row in rows]
    eligibility = AsyncMock()
    result = await MobileInvoiceService(repo, eligibility).list(
        uuid4(),
        role=UserRole.CUSTOMER,
        limit=2,
        cursor=None,
        status=None,
        include_historical=False,
        from_date=None,
        to_date=None,
    )
    assert len(result.items) == 2
    assert result.items[0].total_amount == "125.00"
    assert result.next_cursor == str(rows[1].id)
    assert repo.list_for_actor.call_args.kwargs["limit"] == 3
    eligibility.require_marketplace_actor.assert_awaited_once()
    repo.list_items.assert_not_awaited()

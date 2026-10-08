"""Private invoice downloads, repair invariants, and Saudi-day expiry."""

import base64
import json
import re
import zlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.core.exceptions import ConflictError
from app.integrations.email.sndr_client import SndrEmailClient
from app.models.enums import InvoiceStatus, OrderStatus
from app.repositories.order_repository import OrderRepository
from app.services.expiry_service import ExpiryService
from app.services.invoice_pdf import render_invoice_pdf
from app.services.invoice_vat_repair_service import (
    InvoiceVatRepairService,
    corrected_items_only_result,
)
from sqlalchemy.dialects import postgresql


def invoice():
    return SimpleNamespace(
        id=uuid4(),
        order_id=uuid4(),
        status=InvoiceStatus.ISSUED,
        currency="SAR",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        paid_at=None,
        items_net_amount=Decimal("100"),
        courier_fee_amount=Decimal("20"),
        service_fee_amount=Decimal("6"),
        discount_amount=Decimal("0"),
        net_after_discount_amount=Decimal("126"),
        tax_amount=Decimal("18.90"),
        total_amount=Decimal("144.90"),
        pricing_breakdown={},
        promo_id=None,
        promo_code_snapshot=None,
        issued_by_courier_id=uuid4(),
    )


def items():
    return [
        SimpleNamespace(
            position=1,
            title="Gift (sample)",
            description=None,
            unit_price_amount=Decimal("100"),
            quantity=1,
            tax_rate=Decimal("0.15"),
            line_net_amount=Decimal("100"),
            line_discount_amount=Decimal("0"),
            line_taxable_amount=Decimal("100"),
            line_tax_amount=Decimal("15"),
            line_total_amount=Decimal("115"),
        )
    ]


def test_repair_only_removes_fee_vat_and_preserves_source():
    original = invoice()
    result = corrected_items_only_result(original, items())
    assert result.tax_amount == Decimal("15")
    assert result.total_amount == Decimal("141")
    assert original.total_amount == Decimal("144.90")
    assert original.pricing_breakdown == {}


def test_repair_rejects_inconsistent_stored_allocations():
    original = invoice()
    original.items_net_amount = Decimal("200")
    with pytest.raises(ConflictError):
        corrected_items_only_result(original, items())


@pytest.mark.parametrize(
    "status", [InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.EXPIRED]
)
async def test_repair_refuses_non_issued_invoices(status):
    service = object.__new__(InvoiceVatRepairService)
    original = invoice()
    original.status = status
    service.invoices = AsyncMock()
    service.invoices.lock.return_value = original
    with pytest.raises(ConflictError):
        await service.repair(original.id, apply=True)
    service.invoices.create_draft.assert_not_awaited()


@pytest.mark.parametrize("pending", [True, False])
async def test_repair_preview_never_writes_and_blocks_pending_payments(pending):
    original = invoice()
    service = object.__new__(InvoiceVatRepairService)
    service.invoices, service.orders, service.payments = AsyncMock(), AsyncMock(), AsyncMock()
    service.invoices.lock.return_value = original
    service.invoices.get_active_for_order.return_value = original
    service.invoices.list_items.return_value = items()
    service.orders.lock.return_value = SimpleNamespace(status=OrderStatus.WAITING_PAYMENT)
    service.payments.get_open_intent_for_invoice.return_value = object() if pending else None
    if pending:
        with pytest.raises(ConflictError):
            await service.repair(original.id, apply=False)
    else:
        _, result = await service.repair(original.id, apply=False)
        assert result.total_amount == Decimal("141")
    assert original.status is InvoiceStatus.ISSUED
    service.invoices.create_draft.assert_not_awaited()


def test_pdf_is_a_static_document():
    document = render_invoice_pdf(invoice(), items())
    assert document.startswith(b"%PDF-")
    assert b"/JavaScript" not in document and b"/OpenAction" not in document
    assert len(document) < 100000


async def test_sndr_documented_contract_and_stable_retry_key():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "em_test", "status": "queued"})

    client = SndrEmailClient("https://api.sndr.sh", "test-key", "receipts@example.test", "Giftly")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        for _ in range(2):
            await client.send_transactional(
                "customer@example.test",
                "unused",
                {
                    "invoice_id": "test-invoice",
                    "total_amount": "141.00",
                    "pdf_base64": "JVBERg==",
                },
            )
    finally:
        await client.aclose()
    assert str(requests[0].url) == "https://api.sndr.sh/v1/send"
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
    body = json.loads(requests[0].content)
    assert isinstance(body["from"], str) and body["to"] == ["customer@example.test"]
    assert body["attachments"][0]["content_type"] == "application/pdf"
    assert "html" not in body


async def test_midnight_expiry_uses_saudi_date():
    service = object.__new__(ExpiryService)
    service._orders = AsyncMock()
    order = SimpleNamespace(status=OrderStatus.NEW, cancelled_reason=None)
    service._orders.lock_overdue_unaccepted.return_value = [order]
    assert await service.cancel_overdue_orders(now=datetime(2026, 10, 3, 21, tzinfo=UTC)) == 1
    assert (
        service._orders.lock_overdue_unaccepted.call_args.kwargs["before"].isoformat()
        == "2026-10-04"
    )
    assert order.status is OrderStatus.CANCELLED


async def test_expiry_query_locks_only_unaccepted_overdue_rows():
    session = AsyncMock()
    session.scalars.return_value = []
    await OrderRepository(session).lock_overdue_unaccepted(
        before=datetime.now(UTC).date(), limit=200
    )
    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF orders SKIP LOCKED" in sql
    assert "orders.courier_id IS NULL" in sql and "orders.delivery_date <" in sql
    assert "LIMIT" in sql


def test_pdf_has_branded_item_table_and_payment_details():
    source = invoice()
    document = render_invoice_pdf(source, items())
    streams = b"\n".join(
        zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
        for stream in re.findall(rb"stream\r?\n(.*?)endstream", document, re.DOTALL)
    )
    for label in (
        b"Giftly",
        b"Commercial registration",
        b"Athar Al-Taqnia",
        b"DESCRIPTION",
        b"QTY",
        b"UNIT PRICE",
        b"TOTAL",
        b"Issued",
    ):
        assert label in streams
    assert str(source.id).encode() in streams
    assert str(source.order_id).encode() in streams


def test_pdf_paginates_long_item_lists_without_active_content():
    source_items = [
        SimpleNamespace(**(vars(items()[0]) | {"position": position})) for position in range(1, 101)
    ]
    document = render_invoice_pdf(invoice(), source_items)
    assert len(re.findall(rb"/Type /Page\b", document)) > 1
    assert b"/JavaScript" not in document and b"/OpenAction" not in document


def test_pdf_dates_use_gmt_plus_three_without_changing_stored_values():
    source = invoice()
    source.issued_at = datetime(2026, 10, 6, 22, tzinfo=UTC)
    source.paid_at = datetime(2026, 10, 6, 23, 15, tzinfo=UTC)
    document = render_invoice_pdf(source, items())
    streams = b"\n".join(
        zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
        for stream in re.findall(rb"stream\r?\n(.*?)endstream", document, re.DOTALL)
    )
    assert b"07 Oct 2026, 01:00 GMT+3" in streams
    assert b"07 Oct 2026, 02:15 GMT+3" in streams
    assert source.issued_at.hour == 22 and source.issued_at.tzinfo is UTC
    assert source.paid_at.hour == 23 and source.paid_at.tzinfo is UTC

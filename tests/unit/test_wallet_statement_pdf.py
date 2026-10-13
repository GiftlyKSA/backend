"""Informational PDF exports are complete, owned and bounded."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.schemas.wallet_statement import StatementTotals, WalletStatement
from app.schemas.wallets import TransactionResponse
from app.services.wallet_statement_pdf import WalletStatementPdfService, render_statement_pdf


def snapshot(count=0):
    totals = StatementTotals(
        settled_credits="5.00",
        settled_debits="2.00",
        settled_net="3.00",
        pending_credits="0.00",
        pending_debits="0.00",
        reversed_credits="0.00",
        reversed_debits="0.00",
    )
    items = [
        TransactionResponse(
            id=str(uuid4()),
            amount="5.00",
            type="TOPUP",
            status="SETTLED",
            balance_after="5.00",
            created_at="2026-10-13T00:00:00Z",
            description="<script>alert(1)</script>",
            order_id=None,
            invoice_id=None,
            payment_intent_id=None,
        )
        for _ in range(count)
    ]
    return WalletStatement(
        currency="SAR",
        from_date=date(2026, 10, 1),
        to_date=date(2026, 10, 13),
        as_of=datetime(2026, 10, 13, tzinfo=UTC),
        totals=totals,
        items=items,
        next_cursor=None,
    )


async def test_pdf_reads_complete_bounded_range_and_renders():
    statements = AsyncMock()
    statements.read.return_value = snapshot(26)
    service = WalletStatementPdfService(statements)
    owner = uuid4()
    result = await service.export(owner, from_date=date(2026, 10, 1), to_date=date(2026, 10, 13))
    assert result.startswith(b"%PDF-")
    statements.read.assert_awaited_once_with(
        owner, from_date=date(2026, 10, 1), to_date=date(2026, 10, 13), limit=5000, cursor=None
    )


async def test_pdf_refuses_truncated_export():
    statements = AsyncMock()
    value = snapshot()
    value.next_cursor = str(uuid4())
    statements.read.return_value = value
    with pytest.raises(ValidationDomainError, match="narrower"):
        await WalletStatementPdfService(statements).export(
            uuid4(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 13)
        )


async def test_pdf_preserves_ownership_failure():
    from app.core.exceptions import NotFoundError

    statements = AsyncMock()
    statements.read.side_effect = NotFoundError()
    with pytest.raises(NotFoundError):
        await WalletStatementPdfService(statements).export(
            uuid4(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 13)
        )


def test_pdf_empty_snapshot_has_document_and_not_earnings():
    result = render_statement_pdf(snapshot())
    assert result.startswith(b"%PDF-")
    assert len(result) < 100_000


async def test_pdf_releases_reads_before_rendering(monkeypatch):
    import app.services.wallet_statement_pdf as module

    statements = AsyncMock()
    statements.read.return_value = snapshot()
    release = AsyncMock()

    def render(value):
        release.assert_awaited_once()
        assert value is statements.read.return_value
        return b"%PDF-test"

    monkeypatch.setattr(module, "render_statement_pdf", render)
    assert (
        await WalletStatementPdfService(statements).export(
            uuid4(),
            from_date=date(2026, 10, 1),
            to_date=date(2026, 10, 13),
            release_reads=release,
        )
        == b"%PDF-test"
    )


async def test_pdf_cancelled_render_holds_capacity_until_thread_finishes(monkeypatch):
    import asyncio
    import threading

    import app.services.wallet_statement_pdf as module

    slots = asyncio.Semaphore(2)
    monkeypatch.setattr(module, "_slots", slots)
    statements = AsyncMock()
    statements.read.return_value = snapshot()
    loop = asyncio.get_running_loop()
    started = asyncio.Queue()
    finish = threading.Event()

    def render(value):
        loop.call_soon_threadsafe(started.put_nowait, True)
        assert finish.wait(timeout=5)
        return b"%PDF-test"

    monkeypatch.setattr(module, "render_statement_pdf", render)
    service = WalletStatementPdfService(statements)

    async def export():
        return await service.export(
            uuid4(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 13)
        )

    first = asyncio.create_task(export())
    second = asyncio.create_task(export())
    try:
        await asyncio.wait_for(started.get(), timeout=2)
        await asyncio.wait_for(started.get(), timeout=2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(module.StatementExportUnavailableError):
            await export()
        assert slots.locked()
    finally:
        finish.set()
        await second
        for _ in range(100):
            if slots._value == 2:
                break
            await asyncio.sleep(0.01)
    assert slots._value == 2
    assert await export() == b"%PDF-test"


async def test_pdf_read_timeout_returns_capacity_without_render(monkeypatch):
    import asyncio

    import app.services.wallet_statement_pdf as module

    slots = asyncio.Semaphore(2)
    monkeypatch.setattr(module, "_slots", slots)
    monkeypatch.setattr(module, "_READ_SECONDS", 0.01)

    async def slow_read(*args, **kwargs):
        await asyncio.sleep(1)

    statements = AsyncMock()
    statements.read.side_effect = slow_read
    with pytest.raises(module.StatementExportUnavailableError):
        await WalletStatementPdfService(statements).export(
            uuid4(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 13)
        )
    assert slots._value == 2


async def test_pdf_render_failure_returns_capacity(monkeypatch):
    import asyncio

    import app.services.wallet_statement_pdf as module

    slots = asyncio.Semaphore(2)
    monkeypatch.setattr(module, "_slots", slots)
    statements = AsyncMock()
    statements.read.return_value = snapshot()

    def render(value):
        raise module.StatementExportUnavailableError()

    monkeypatch.setattr(module, "render_statement_pdf", render)
    with pytest.raises(module.StatementExportUnavailableError):
        await WalletStatementPdfService(statements).export(
            uuid4(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 13)
        )
    assert slots._value == 2


@pytest.mark.parametrize("count", [0, 26])
def test_pdf_deadline_covers_empty_and_populated_documents(monkeypatch, count):
    import app.services.wallet_statement_pdf as module

    monkeypatch.setattr(module, "_RENDER_SECONDS", -1)
    with pytest.raises(module.StatementExportUnavailableError):
        render_statement_pdf(snapshot(count))


def test_pdf_size_limit_rejects_whole_document(monkeypatch):
    import app.services.wallet_statement_pdf as module

    monkeypatch.setattr(module, "_MAX_BYTES", 10)
    with pytest.raises(module.StatementExportUnavailableError):
        render_statement_pdf(snapshot())


def test_pdf_all_entries_literal_descriptions_and_references_fit_pages(monkeypatch):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen.canvas import Canvas

    value = snapshot(60)
    recorded = []
    original = Canvas.drawString

    def draw(canvas, x, y, text, *args, **kwargs):
        recorded.append((canvas.getPageNumber(), x, y, text))
        return original(canvas, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Canvas, "drawString", draw)
    for index, item in enumerate(value.items):
        item.description = "W" * 120 if index else "<script>alert(1)</script>"
        item.order_id = str(uuid4())
        item.invoice_id = str(uuid4())
        item.payment_intent_id = str(uuid4())
        item.status = ("SETTLED", "PENDING", "REVERSED")[index % 3]
    assert render_statement_pdf(value).startswith(b"%PDF-")
    assert max(page for page, _, _, _ in recorded) > 1
    for item in value.items:
        transaction = [entry for entry in recorded if entry[3] == f"Transaction: {item.id}"]
        assert len(transaction) == 1
        for label, reference in (
            ("Order", item.order_id),
            ("Invoice", item.invoice_id),
            ("Payment", item.payment_intent_id),
        ):
            line = [entry for entry in recorded if entry[3] == f"{label}: {reference}"]
            assert len(line) == 1
            assert line[0][0] == transaction[0][0]
            assert line[0][2] >= 55
    assert any(text == "<script>alert(1)</script>" for _, _, _, text in recorded)
    for _, x, y, text in recorded:
        if 55 <= y < A4[1] - 110:
            assert x + stringWidth(text, "Helvetica", 8) <= A4[0] - 40
    assert any(text == "Settled Net: 3.00 SAR" for _, _, _, text in recorded)
    assert any(text == "Pending Credits: 0.00 SAR" for _, _, _, text in recorded)
    assert any(text == "Reversed Debits: 0.00 SAR" for _, _, _, text in recorded)


def test_pdf_preserves_complete_literal_descriptions(monkeypatch):
    from reportlab.pdfgen.canvas import Canvas

    recorded = []
    positioned = []
    original = Canvas.drawString

    def draw(canvas, x, y, text, *args, **kwargs):
        recorded.append(text)
        positioned.append((canvas.getPageNumber(), x, y, text))
        return original(canvas, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Canvas, "drawString", draw)
    value = snapshot(1)
    value.items[0].description = (
        "\u0648\u0635\u0641 \u0639\u0631\u0628\u064a <script>alert(1)</script> " + "W" * 5000
    )
    render_statement_pdf(value)
    assert "[Non-English description omitted]" not in recorded
    from app.services.wallet_statement_pdf import _entry_lines

    lines = _entry_lines(value.items[0], "SAR")
    assert value.items[0].description in "".join(lines)
    assert all(y >= 55 for _, _, y, text in positioned if not text.startswith("Informational"))


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2026, 10, 13), date(2026, 10, 12)), (date(2024, 1, 1), date(2025, 1, 1))],
)
async def test_pdf_authoritative_reader_rejects_invalid_range_before_reads(start, end):
    from app.services.wallet_statement_service import WalletStatementService

    wallets = AsyncMock()
    eligibility = AsyncMock()
    with pytest.raises(ValidationDomainError, match="1 and 366"):
        await WalletStatementPdfService(WalletStatementService(wallets, eligibility)).export(
            uuid4(), from_date=start, to_date=end
        )
    eligibility.require_marketplace_actor.assert_not_awaited()
    wallets.get_by_user.assert_not_awaited()


async def test_pdf_endpoint_private_binary_contract_and_derived_owner(monkeypatch):
    from app.core.deps import Actor, get_db
    from app.models.enums import UserRole
    from app.routers import wallets
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    app.include_router(wallets.router)
    actor = Actor(id=uuid4(), role=UserRole.CUSTOMER, jti="test")
    session = AsyncMock()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[wallets._CustomerOrCourier] = lambda: actor
    export = AsyncMock(return_value=b"%PDF-test")
    monkeypatch.setattr(WalletStatementPdfService, "export", export)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/wallets/me/statement/pdf?from_date=2026-10-01&to_date=2026-10-13"
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        'attachment; filename="giftly-statement-2026-10-01-2026-10-13.pdf"'
    )
    export.assert_awaited_once_with(
        actor.id,
        from_date=date(2026, 10, 1),
        to_date=date(2026, 10, 13),
        release_reads=session.commit,
    )
    assert set(
        app.openapi()["paths"]["/api/wallets/me/statement/pdf"]["get"]["responses"]["200"][
            "content"
        ]
    ) == {"application/pdf"}


@pytest.mark.parametrize("days", [1, 366])
async def test_pdf_reader_accepts_inclusive_day_boundaries(days):
    from datetime import timedelta
    from decimal import Decimal
    from types import SimpleNamespace

    from app.services.wallet_statement_service import WalletStatementService

    owner = uuid4()
    wallet_id = uuid4()
    wallets = AsyncMock()
    eligibility = AsyncMock()
    wallets.get_by_user.return_value = SimpleNamespace(id=wallet_id, currency="SAR")
    wallets.statement.return_value = SimpleNamespace(
        items=[],
        as_of=datetime(2026, 10, 13, tzinfo=UTC),
        totals={
            name: Decimal("0.00")
            for name in snapshot().totals.model_dump()
            if name != "settled_net"
        },
    )
    start = date(2024, 1, 1)
    end = start + timedelta(days=days - 1)
    result = await WalletStatementPdfService(WalletStatementService(wallets, eligibility)).export(
        owner, from_date=start, to_date=end
    )
    assert result.startswith(b"%PDF-")
    eligibility.require_marketplace_actor.assert_awaited_once_with(owner)
    wallets.get_by_user.assert_awaited_once_with(owner)
    assert wallets.statement.call_args.args == (wallet_id,)
    assert wallets.statement.call_args.kwargs["start"] == datetime(2023, 12, 31, 21, tzinfo=UTC)
    assert wallets.statement.call_args.kwargs["end"] == (
        datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        - timedelta(hours=3)
    )
    assert wallets.statement.call_args.kwargs["limit"] == 5001


async def test_pdf_ineligible_actor_denied_before_owned_wallet_read():
    from app.core.exceptions import ForbiddenError
    from app.services.wallet_statement_service import WalletStatementService

    wallets = AsyncMock()
    eligibility = AsyncMock()
    eligibility.require_marketplace_actor.side_effect = ForbiddenError()
    with pytest.raises(ForbiddenError):
        await WalletStatementPdfService(WalletStatementService(wallets, eligibility)).export(
            uuid4(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 13)
        )
    wallets.get_by_user.assert_not_awaited()


async def test_pdf_failed_read_release_does_not_render_or_leak_admission(monkeypatch):
    import asyncio
    from unittest.mock import Mock

    import app.services.wallet_statement_pdf as module

    slots = asyncio.Semaphore(2)
    monkeypatch.setattr(module, "_slots", slots)
    render = Mock()
    monkeypatch.setattr(module, "render_statement_pdf", render)
    statements = AsyncMock()
    statements.read.return_value = snapshot()
    with pytest.raises(RuntimeError, match="release failed"):
        await WalletStatementPdfService(statements).export(
            uuid4(),
            from_date=date(2026, 10, 1),
            to_date=date(2026, 10, 13),
            release_reads=AsyncMock(side_effect=RuntimeError("release failed")),
        )
    render.assert_not_called()
    assert slots._value == 2


def test_pdf_text_budget_rejects_oversized_snapshot(monkeypatch):
    import app.services.wallet_statement_pdf as module

    monkeypatch.setattr(module, "_MAX_TEXT_CHARACTERS", 10)
    with pytest.raises(ValidationDomainError, match="text exceeds"):
        render_statement_pdf(snapshot(1))


def test_pdf_unsupported_glyph_has_visible_lossless_notation():
    from app.services.wallet_statement_pdf import _display

    assert "\\U0001F680" in _display("Launch \U0001f680")
    assert _display("<script>alert(1)</script>") == "<script>alert(1)</script>"


def test_pdf_page_budget_rejects_complete_export_instead_of_truncating(monkeypatch):
    import app.services.wallet_statement_pdf as module

    monkeypatch.setattr(module, "_MAX_PAGES", 1)
    with pytest.raises(module.StatementExportUnavailableError):
        render_statement_pdf(snapshot(40))

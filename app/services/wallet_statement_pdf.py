"""Bounded informational PDF snapshots of the owned wallet statement."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from app.core.exceptions import DomainError, ValidationDomainError
from app.schemas.wallet_statement import WalletStatement
from app.schemas.wallets import TransactionResponse
from app.services.wallet_statement_service import WalletStatementService

_MAX_ROWS = 5000
_MAX_BYTES = 8 * 1024 * 1024
_RENDER_SECONDS = 20
_READ_SECONDS = 25
_slots = asyncio.Semaphore(2)
_RIYADH = timezone(timedelta(hours=3))
_MARGIN = 40
_LINE_HEIGHT = 12
_CONTENT_WIDTH = A4[0] - 2 * _MARGIN


class StatementExportUnavailableError(DomainError):
    """PDF admission or rendering is temporarily unavailable."""

    code = "STATEMENT_EXPORT_UNAVAILABLE"
    status_code = 503
    message = "Statement export is busy. Please try again later."


def _lines(text: str) -> list[str]:
    """Wrap literal text to the measured page width, including unbroken identifiers."""
    result: list[str] = []
    line = ""
    width = 0.0
    for character in text:
        character_width = stringWidth(character, "Helvetica", 8)
        if width + character_width > _CONTENT_WIDTH and line:
            result.append(line)
            line = ""
            width = 0.0
        line += character
        width += character_width
    result.append(line)
    return result


def _entry_lines(item: TransactionResponse, currency: str) -> list[str]:
    timestamp = datetime.fromisoformat(item.created_at).astimezone(_RIYADH)
    description = " ".join((item.description or "").split())[:120]
    if not description.isascii():
        description = "[Non-English description omitted]"
    fields = [
        f"{timestamp:%Y-%m-%d %H:%M:%S} GMT+3 | {item.type} | {item.amount} {currency}",
        f"Transaction: {item.id}",
        description,
    ]
    fields.extend(
        f"{label}: {value}"
        for label, value in (
            ("Order", item.order_id),
            ("Invoice", item.invoice_id),
            ("Payment", item.payment_intent_id),
        )
        if value
    )
    return [line for field in fields for line in _lines(field)]


class _StatementLayout:
    def __init__(self, canvas: Canvas, statement: WalletStatement) -> None:
        self.canvas = canvas
        self.statement = statement
        self.y = 0.0
        self.page()

    def page(self, status: str | None = None) -> None:
        if self.y:
            self.canvas.showPage()
        self.canvas.setFillColor(colors.HexColor("#68318E"))
        self.canvas.setFont("Helvetica-Bold", 22)
        self.canvas.drawString(_MARGIN, A4[1] - 45, "Giftly")
        self.canvas.setFont("Helvetica-Bold", 12)
        self.canvas.drawString(_MARGIN, A4[1] - 68, "WALLET STATEMENT")
        self.canvas.setFillColor(colors.black)
        self.canvas.setFont("Helvetica", 8)
        self.canvas.drawString(
            _MARGIN,
            A4[1] - 85,
            f"{self.statement.from_date} to {self.statement.to_date} | {self.statement.currency}",
        )
        generated = self.statement.as_of.astimezone(_RIYADH).strftime("%Y-%m-%d %H:%M:%S")
        self.canvas.drawString(_MARGIN, A4[1] - 98, f"Snapshot: {generated} GMT+3 (Asia/Riyadh)")
        self.canvas.drawString(
            _MARGIN, 26, "Informational snapshot; not a legal document or earnings report."
        )
        self.canvas.drawRightString(A4[0] - _MARGIN, 26, f"Page {self.canvas.getPageNumber()}")
        self.y = A4[1] - 120
        if status is not None:
            self.heading(f"{status} (continued)")

    def heading(self, status: str) -> None:
        self.canvas.setFont("Helvetica-Bold", 10)
        self.canvas.drawString(_MARGIN, self.y, status)
        self.y -= 18

    def block(self, lines: list[str], status: str | None = None) -> None:
        height = len(lines) * _LINE_HEIGHT + 10
        if self.y - height < 55:
            self.page(status)
        self.canvas.setFont("Helvetica", 8)
        for line in lines:
            self.canvas.drawString(_MARGIN, self.y, line)
            self.y -= _LINE_HEIGHT
        self.y -= 10


def render_statement_pdf(statement: WalletStatement) -> bytes:
    """Render a complete bounded snapshot with literal, abbreviated descriptions."""
    deadline = time.monotonic() + _RENDER_SECONDS
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4, pageCompression=1)
    canvas.setTitle("Giftly wallet statement")
    canvas.setAuthor("Giftly")
    layout = _StatementLayout(canvas, statement)
    layout.block(
        [
            f"{name.replace('_', ' ').title()}: {amount} {statement.currency}"
            for name, amount in statement.totals.model_dump().items()
        ]
    )
    layout.block(
        [
            "English PDF: descriptions abbreviated to 120 characters; non-English text omitted.",
            "Transaction and reference IDs are exact. All selected entries are included.",
        ]
    )
    for status in ("SETTLED", "PENDING", "REVERSED"):
        if layout.y < 165:
            layout.page()
        layout.heading(status)
        for item in statement.items:
            if item.status != status:
                continue
            if time.monotonic() > deadline:
                raise StatementExportUnavailableError()
            layout.block(_entry_lines(item, statement.currency), status)
    canvas.save()
    if time.monotonic() > deadline or output.tell() > _MAX_BYTES:
        raise StatementExportUnavailableError()
    return output.getvalue()


class WalletStatementPdfService:
    """Reuse current eligibility and whole-range totals, never export a partial page."""

    def __init__(self, statements: WalletStatementService) -> None:
        """Bind the authoritative owned statement reader."""
        self._statements = statements

    async def export(
        self,
        actor_id: UUID,
        *,
        from_date: date,
        to_date: date,
        release_reads: Callable[[], Awaitable[None]] | None = None,
    ) -> bytes:
        """Bound admission and retain its slot until a cancelled render actually finishes."""
        if _slots.locked():
            raise StatementExportUnavailableError()
        await _slots.acquire()
        rendering: asyncio.Task[bytes] | None = None
        try:
            async with asyncio.timeout(_READ_SECONDS):
                statement = await self._statements.read(
                    actor_id, from_date=from_date, to_date=to_date, limit=_MAX_ROWS, cursor=None
                )
                if statement.next_cursor is not None:
                    raise ValidationDomainError(
                        "Choose a narrower range; export exceeds 5000 entries."
                    )
                if release_reads is not None:
                    await release_reads()
            rendering = asyncio.create_task(asyncio.to_thread(render_statement_pdf, statement))
            rendering.add_done_callback(self._finished)
            return await asyncio.shield(rendering)
        except TimeoutError as exc:
            raise StatementExportUnavailableError() from exc
        finally:
            if rendering is None:
                _slots.release()

    @staticmethod
    def _finished(task: asyncio.Task[bytes]) -> None:
        """Release only after real rendering ends, including abandoned failures."""
        _slots.release()
        if not task.cancelled():
            task.exception()

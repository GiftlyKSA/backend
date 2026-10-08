"""Render stored invoice amounts in a professional English-only PDF."""

from datetime import timedelta, timezone
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.money import money_str
from app.models import Invoice, InvoiceItem

_COMMERCIAL_REGISTRATION_NAME = "Athar Al-Taqnia"
_INVOICE_TIMEZONE = timezone(timedelta(hours=3))
_PRIMARY = colors.HexColor("#68318E")
_TEXT = colors.HexColor("#201B26")
_MUTED = colors.HexColor("#746B7B")
_BORDER = colors.HexColor("#E9E2EE")
_SURFACE = colors.HexColor("#F6F0FA")
_SOFT_PURPLE = colors.HexColor("#E7DBF3")


def _footer(canvas: Canvas, document: BaseDocTemplate) -> None:
    """Add a restrained footer on every page."""
    canvas.saveState()
    canvas.setStrokeColor(_BORDER)
    canvas.line(42, 42, A4[0] - 42, 42)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_MUTED)
    canvas.drawString(42, 28, "Giftly | All amounts in the invoice currency. Dates shown in GMT+3.")
    canvas.drawRightString(A4[0] - 42, 28, f"Page {document.page}")
    canvas.restoreState()


def render_invoice_pdf(invoice: Invoice, items: list[InvoiceItem]) -> bytes:
    """Render a private document without fetching assets or recalculating prices."""
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=42,
        leftMargin=42,
        topMargin=40,
        bottomMargin=60,
        title="Giftly invoice",
        author="Giftly",
        pageCompression=1,
    )
    body = ParagraphStyle("Body", fontName="Helvetica", fontSize=9, leading=14, textColor=_TEXT)
    muted = ParagraphStyle("Muted", parent=body, textColor=_MUTED)
    wordmark = ParagraphStyle(
        "Wordmark", fontName="Helvetica-Bold", fontSize=30, leading=36, textColor=_PRIMARY
    )
    heading = ParagraphStyle(
        "Heading",
        fontName="Helvetica",
        fontSize=28,
        leading=36,
        alignment=2,
        textColor=_PRIMARY,
    )
    width = A4[0] - 84
    header = Table(
        [[Paragraph("Giftly", wordmark), Paragraph("INVOICE", heading)]],
        colWidths=[width / 2, width / 2],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SURFACE),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 16),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    issued_at = (
        invoice.issued_at.astimezone(_INVOICE_TIMEZONE).strftime("%d %b %Y, %H:%M GMT+3")
        if invoice.issued_at
        else "-"
    )
    paid_at = (
        invoice.paid_at.astimezone(_INVOICE_TIMEZONE).strftime("%d %b %Y, %H:%M GMT+3")
        if invoice.paid_at
        else "-"
    )
    details = Paragraph(
        f"<b>Commercial registration</b> &nbsp; {escape(_COMMERCIAL_REGISTRATION_NAME)}<br/>"
        f"<b>Invoice ID</b> &nbsp; {escape(str(invoice.id))}<br/>"
        f"<b>Order ID</b> &nbsp; {escape(str(invoice.order_id))}<br/>"
        f"<b>Payment status</b> &nbsp; {escape(str(invoice.status))}<br/>"
        f"<b>Issued</b> &nbsp; {issued_at}<br/>"
        f"<b>Paid</b> &nbsp; {paid_at}",
        body,
    )
    rows: list[list[object]] = [["DESCRIPTION", "QTY", "UNIT PRICE", "DISCOUNT", "AMOUNT"]]
    for item in items:
        title = item.title if item.title.isascii() else f"Item {item.position}"
        rows.append(
            [
                Paragraph(escape(title), body),
                str(item.quantity),
                money_str(item.unit_price_amount),
                money_str(item.line_discount_amount),
                money_str(item.line_total_amount),
            ]
        )
    table = Table(rows, colWidths=[width - 260, 28, 75, 75, 82], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), _TEXT),
                ("BACKGROUND", (0, 0), (-1, 0), _SOFT_PURPLE),
                ("TEXTCOLOR", (0, 0), (-1, 0), _PRIMARY),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, -1), 8),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("LINEBELOW", (0, 0), (-1, 0), 1, _PRIMARY),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, _BORDER),
            ]
        )
    )
    totals = Table(
        [
            [label, f"{invoice.currency} {money_str(amount)}"]
            for label, amount in (
                ("Items before discount", invoice.items_net_amount),
                ("Discount", invoice.discount_amount),
                ("Courier / delivery fee", invoice.courier_fee_amount),
                ("Service fee", invoice.service_fee_amount),
                ("TOTAL", invoice.total_amount),
            )
        ],
        colWidths=[180, 120],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), _TEXT),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, -1), (-1, -1), 12),
                ("BACKGROUND", (0, -1), (-1, -1), _SOFT_PURPLE),
                ("TEXTCOLOR", (0, -1), (-1, -1), _PRIMARY),
                ("LINEABOVE", (0, -1), (-1, -1), 1, _PRIMARY),
            ]
        )
    )
    document.build(
        [
            header,
            Spacer(1, 30),
            details,
            Spacer(1, 26),
            Paragraph(f"ITEMIZED CHARGES &nbsp; / &nbsp; {escape(invoice.currency)}", muted),
            Spacer(1, 8),
            table,
            Spacer(1, 22),
            KeepTogether(totals),
            Spacer(1, 24),
            Paragraph(
                "Thank you for choosing Giftly. Please keep this invoice for your records.", muted
            ),
        ],
        onFirstPage=_footer,
        onLaterPages=_footer,
    )
    return output.getvalue()

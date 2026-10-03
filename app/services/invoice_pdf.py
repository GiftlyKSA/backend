"""Render stored invoice amounts in a small, English-only PDF."""

from io import BytesIO
from textwrap import wrap

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from app.core.money import money_str
from app.models import Invoice, InvoiceItem


def render_invoice_pdf(invoice: Invoice, items: list[InvoiceItem]) -> bytes:
    """Render a private document without fetching assets or recalculating prices."""
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4, pageCompression=1)
    canvas.setTitle("Giftly invoice")
    y = 800

    def line(value: str, *, heading: bool = False) -> None:
        nonlocal y
        for part in wrap(value, width=92) or [""]:
            if y < 55:
                canvas.showPage()
                y = 800
            canvas.setFont("Helvetica-Bold" if heading else "Helvetica", 11)
            canvas.drawString(42, y, part)
            y -= 19

    line("GIFTLY | INVOICE", heading=True)
    line(f"Invoice: {invoice.id}")
    line(f"Order: {invoice.order_id}")
    line(f"Status: {invoice.status} | Currency: {invoice.currency}")
    line(f"Issued (UTC): {invoice.issued_at.isoformat() if invoice.issued_at else '-'}")
    line(f"Paid (UTC): {invoice.paid_at.isoformat() if invoice.paid_at else '-'}")
    line("")
    line("ITEMS", heading=True)
    for item in items:
        title = item.title if item.title.isascii() else f"Item {item.position}"
        line(f"{item.position}. {title}")
        line(
            f"Quantity: {item.quantity} | Unit: {money_str(item.unit_price_amount)} | "
            f"Discount: {money_str(item.line_discount_amount)}"
        )
        line(
            f"Net: {money_str(item.line_taxable_amount)} | "
            f"VAT: {money_str(item.line_tax_amount)} | "
            f"Total: {money_str(item.line_total_amount)}"
        )
    line("")
    for label, amount in (
        ("Items before discount", invoice.items_net_amount),
        ("Courier / delivery fee", invoice.courier_fee_amount),
        ("Service fee", invoice.service_fee_amount),
        ("Discount", invoice.discount_amount),
        ("VAT", invoice.tax_amount),
        ("TOTAL", invoice.total_amount),
    ):
        line(f"{label}: {invoice.currency} {money_str(amount)}", heading=label == "TOTAL")
    canvas.save()
    return output.getvalue()

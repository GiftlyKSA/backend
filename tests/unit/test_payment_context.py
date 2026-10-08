import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.payment_context import invoice_snapshot


def test_invoice_snapshot_preserves_all_line_details_and_full_total_for_split_payment() -> None:
    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        status="ISSUED",
        currency="SAR",
        items_net_amount=Decimal("200.00"),
        courier_fee_amount=Decimal("20.00"),
        service_fee_amount=Decimal("11.00"),
        discount_amount=Decimal("0.00"),
        net_after_discount_amount=Decimal("231.00"),
        tax_amount=Decimal("30.00"),
        total_amount=Decimal("261.00"),
        promo_code_snapshot=None,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    item = SimpleNamespace(
        position=1,
        title="Gift",
        description="Blue ribbon",
        quantity=2,
        unit_price_amount=Decimal("100.00"),
        tax_rate=Decimal("0.1500"),
        line_net_amount=Decimal("200.00"),
        line_discount_amount=Decimal("0.00"),
        line_taxable_amount=Decimal("200.00"),
        line_tax_amount=Decimal("30.00"),
        line_total_amount=Decimal("230.00"),
    )
    snapshot = invoice_snapshot(invoice, [item])
    line = snapshot.items[0]
    assert line.quantity == 2
    assert line.unit_price_amount == "100.00"
    assert line.description == "Blue ribbon"
    assert line.tax_rate == "0.1500"
    assert line.line_discount_amount == "0.00"
    assert line.line_taxable_amount == "200.00"
    assert line.line_tax_amount == "30.00"
    assert line.line_total_amount == "230.00"
    assert snapshot.total_amount == "261.00"
    assert snapshot.courier_fee_amount == "20.00"
    assert snapshot.service_fee_amount == "11.00"

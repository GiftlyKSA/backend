"""Offline PostgreSQL migration contracts; runtime checks still require PostgreSQL."""

from importlib import import_module
from io import StringIO

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_vat_migration_is_transactional_bounded_and_preserves_money(direction):
    migration = import_module("app.migrations.versions.0024_remove_invoice_vat")
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(migration, direction)()
    sql = output.getvalue()
    assert "SET LOCAL lock_timeout = '5s'" in sql
    assert "ACCESS EXCLUSIVE MODE" in sql
    assert "UPDATE transactions" not in sql
    assert "UPDATE payment_intents" not in sql
    if direction == "upgrade":
        assert "RAISE EXCEPTION" in sql
        assert "DROP COLUMN tax_amount" in sql
        assert "total_amount = net_after_discount_amount" in sql
        assert "line_total_amount = line_net_amount - line_discount_amount" in sql
        assert "DROP TYPE wallet_type_previous" in sql
        assert "DROP TYPE transaction_type_previous" in sql
    else:
        assert "ADD COLUMN tax_amount" in sql
        assert "DEFAULT '0.00'" in sql
        assert "DISABLE TRIGGER trg_giftly_guard_invoice_item" in sql
        assert "ENABLE TRIGGER trg_giftly_guard_invoice_item" in sql
        assert "INSERT INTO wallets" in sql
        assert "VALUES ('SYSTEM_TAX_PAYABLE', 0.00, 0.00, 'SAR', 0)" in sql

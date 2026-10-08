"""Remove separate VAT pricing from the pre-production schema."""

import sqlalchemy as sa
from alembic import op

revision = "0024_remove_invoice_vat"
down_revision = "0023_merge_pending_payments"
branch_labels = None
depends_on = None


def _replace_enum(name: str, table: str, labels: tuple[str, ...]) -> None:
    """Rebuild native enums using fixed migration-owned identifiers and values."""
    values = ", ".join(f"'{label}'" for label in labels)
    op.execute(f"ALTER TYPE {name} RENAME TO {name}_previous")
    op.execute(f"CREATE TYPE {name} AS ENUM ({values})")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN type TYPE {name} USING type::text::{name}")
    op.execute(f"DROP TYPE {name}_previous")


def _wallet_rules(include_tax: bool) -> None:
    """Restore type-dependent wallet constraints and singleton indexes."""
    systems = ["SYSTEM_ESCROW", "SYSTEM_REVENUE", "SYSTEM_GATEWAY"]
    if include_tax:
        systems.append("SYSTEM_TAX_PAYABLE")
    values = ",".join(f"'{name}'" for name in systems)
    op.create_check_constraint(
        "chk_user_wallet_pairing",
        "wallets",
        "(type IN ('CUSTOMER','COURIER') AND user_id IS NOT NULL) "
        f"OR (type IN ({values}) AND user_id IS NULL)",
    )
    op.create_check_constraint(
        "chk_balance_non_negative",
        "wallets",
        "type IN ('SYSTEM_GATEWAY','SYSTEM_REVENUE') OR (balance >= 0 AND held_balance >= 0)",
    )
    names = (
        ("escrow", "revenue", "gateway", "tax") if include_tax else ("escrow", "revenue", "gateway")
    )
    for name, value in zip(names, systems, strict=True):
        op.create_index(
            f"uq_wallets_one_{name}",
            "wallets",
            ["type"],
            unique=True,
            postgresql_where=sa.text(f"type='{value}'"),
        )


def _drop_wallet_rules(include_tax: bool) -> None:
    """Remove dependencies before changing the native wallet enum."""
    op.drop_constraint("chk_user_wallet_pairing", "wallets", type_="check")
    op.drop_constraint("chk_balance_non_negative", "wallets", type_="check")
    names = (
        ("escrow", "revenue", "gateway", "tax") if include_tax else ("escrow", "revenue", "gateway")
    )
    for name in names:
        op.drop_index(f"uq_wallets_one_{name}", table_name="wallets")


def upgrade() -> None:
    """Drop unused tax fields; refuse to discard tax-bearing financial history."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE invoices, invoice_items, wallets, transactions IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM invoices WHERE tax_amount <> 0)
               OR EXISTS (SELECT 1 FROM invoice_items WHERE line_tax_amount <> 0)
               OR EXISTS (SELECT 1 FROM transactions WHERE type::text = 'TAX')
               OR EXISTS (
                   SELECT 1 FROM wallets w WHERE w.type::text = 'SYSTEM_TAX_PAYABLE'
                   AND (w.balance <> 0 OR w.held_balance <> 0 OR EXISTS (
                       SELECT 1 FROM transactions t WHERE t.wallet_id = w.id
                   ))
               ) THEN
                RAISE EXCEPTION 'VAT removal requires a clean pre-production financial dataset; '
                    'back up and explicitly reconcile existing tax-bearing records first';
            END IF;
        END $$
    """)
    op.execute("DELETE FROM wallets WHERE type::text = 'SYSTEM_TAX_PAYABLE'")
    _drop_wallet_rules(True)
    _replace_enum(
        "wallet_type",
        "wallets",
        ("CUSTOMER", "COURIER", "SYSTEM_ESCROW", "SYSTEM_REVENUE", "SYSTEM_GATEWAY"),
    )
    _wallet_rules(False)
    _replace_enum(
        "transaction_type",
        "transactions",
        (
            "TOPUP",
            "WITHDRAWAL",
            "ESCROW_HOLD",
            "ESCROW_RELEASE",
            "PAYMENT",
            "REFUND",
            "COMMISSION",
            "SERVICE_FEE",
            "PROMO_SUBSIDY",
        ),
    )
    op.drop_constraint("chk_invoice_amounts_non_negative", "invoices", type_="check")
    op.drop_constraint("chk_invoice_total_math", "invoices", type_="check")
    op.drop_column("invoices", "tax_amount")
    op.create_check_constraint(
        "chk_invoice_amounts_non_negative",
        "invoices",
        "items_net_amount >= 0 AND courier_fee_amount >= 0 AND service_fee_amount >= 0 "
        "AND discount_amount >= 0 AND total_amount >= 0",
    )
    op.create_check_constraint(
        "chk_invoice_total_math", "invoices", "total_amount = net_after_discount_amount"
    )
    op.drop_constraint("chk_item_tax_rate", "invoice_items", type_="check")
    op.drop_constraint("chk_item_line_math", "invoice_items", type_="check")
    for column in ("tax_rate", "line_taxable_amount", "line_tax_amount"):
        op.drop_column("invoice_items", column)
    op.create_check_constraint(
        "chk_item_line_math",
        "invoice_items",
        "line_net_amount = unit_price_amount * quantity "
        "AND line_total_amount = line_net_amount - line_discount_amount",
    )


def downgrade() -> None:
    """Restore zero-tax columns without inventing charges or ledger movements."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE invoices, invoice_items, wallets, transactions IN ACCESS EXCLUSIVE MODE")
    op.add_column(
        "invoices",
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
    )
    op.drop_constraint("chk_invoice_amounts_non_negative", "invoices", type_="check")
    op.drop_constraint("chk_invoice_total_math", "invoices", type_="check")
    op.create_check_constraint(
        "chk_invoice_amounts_non_negative",
        "invoices",
        "items_net_amount >= 0 AND courier_fee_amount >= 0 AND service_fee_amount >= 0 "
        "AND discount_amount >= 0 AND tax_amount >= 0 AND total_amount >= 0",
    )
    op.create_check_constraint(
        "chk_invoice_total_math",
        "invoices",
        "total_amount = net_after_discount_amount + tax_amount",
    )
    op.add_column(
        "invoice_items",
        sa.Column("tax_rate", sa.Numeric(6, 4), nullable=False, server_default="0.0000"),
    )
    op.add_column(
        "invoice_items",
        sa.Column("line_tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
    )
    op.add_column(
        "invoice_items",
        sa.Column("line_taxable_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
    )
    op.execute("ALTER TABLE invoice_items DISABLE TRIGGER trg_giftly_guard_invoice_item")
    op.execute("UPDATE invoice_items SET line_taxable_amount = line_total_amount")
    op.execute("ALTER TABLE invoice_items ENABLE TRIGGER trg_giftly_guard_invoice_item")
    op.drop_constraint("chk_item_line_math", "invoice_items", type_="check")
    op.create_check_constraint(
        "chk_item_line_math",
        "invoice_items",
        "line_net_amount = unit_price_amount * quantity "
        "AND line_taxable_amount = line_net_amount - line_discount_amount "
        "AND line_total_amount = line_taxable_amount + line_tax_amount",
    )
    op.create_check_constraint(
        "chk_item_tax_rate", "invoice_items", "tax_rate >= 0 AND tax_rate <= 1"
    )
    for column in ("tax_rate", "line_tax_amount", "line_taxable_amount"):
        op.alter_column("invoice_items", column, server_default=None)
    _drop_wallet_rules(False)
    _replace_enum(
        "wallet_type",
        "wallets",
        (
            "CUSTOMER",
            "COURIER",
            "SYSTEM_ESCROW",
            "SYSTEM_REVENUE",
            "SYSTEM_GATEWAY",
            "SYSTEM_TAX_PAYABLE",
        ),
    )
    _wallet_rules(True)
    op.execute(
        "INSERT INTO wallets (type, balance, held_balance, currency, version) "
        "VALUES ('SYSTEM_TAX_PAYABLE', 0.00, 0.00, 'SAR', 0) ON CONFLICT DO NOTHING"
    )
    _replace_enum(
        "transaction_type",
        "transactions",
        (
            "TOPUP",
            "WITHDRAWAL",
            "ESCROW_HOLD",
            "ESCROW_RELEASE",
            "PAYMENT",
            "REFUND",
            "COMMISSION",
            "SERVICE_FEE",
            "TAX",
            "PROMO_SUBSIDY",
        ),
    )

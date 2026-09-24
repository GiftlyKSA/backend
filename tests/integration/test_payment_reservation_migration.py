"""Populated reservation migration checks on an explicitly disposable PostgreSQL DB."""

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from tests.integration.test_dhamen_migration import (
    _dedicated_migration_database_url,
    _run_alembic,
)

_PRIOR = "f6a7b8c9d0e1"
_REVISION = "a7b8c9d0e1f2"
_CONSTRAINTS = {"chk_intent_reservation_nonnegative", "chk_intent_reservation_purpose"}


@pytest_asyncio.fixture
async def migration_database() -> AsyncIterator[tuple[str, AsyncEngine]]:
    # Reuse the existing opt-in and *_migration_test name guard before any DDL.
    database_url = _dedicated_migration_database_url()
    _run_alembic(database_url, "downgrade", "base")
    _run_alembic(database_url, "upgrade", _PRIOR)
    engine = create_async_engine(database_url)
    try:
        yield database_url, engine
    finally:
        await engine.dispose()
        _run_alembic(database_url, "downgrade", "base")


async def _seed_prior_rows(connection: AsyncConnection) -> UUID:
    customer_id, courier_id, wallet_id, split_invoice_id = (uuid4() for _ in range(4))
    await connection.execute(
        text("INSERT INTO users (id, phone, role) VALUES (:id, :phone, :role)"),
        [
            {"id": customer_id, "phone": f"+9665{uuid4().int % 10**8:08d}", "role": "CUSTOMER"},
            {"id": courier_id, "phone": f"+9665{uuid4().int % 10**8:08d}", "role": "COURIER"},
        ],
    )
    # The split owns 300; another reservation owns the remaining 50 held in this wallet.
    await connection.execute(
        text("""
            INSERT INTO wallets (id, user_id, type, balance, held_balance)
            VALUES (:id, :user_id, 'CUSTOMER', 1000.00, 350.00)
        """),
        {"id": wallet_id, "user_id": customer_id},
    )
    gateway_id = await connection.scalar(
        text("SELECT id FROM wallets WHERE type = 'SYSTEM_GATEWAY'")
    )
    assert gateway_id is not None
    await connection.execute(
        text("UPDATE wallets SET balance = -1000.00 WHERE id = :id"), {"id": gateway_id}
    )
    correlation_id = uuid4()
    await connection.execute(
        text("""
            INSERT INTO transactions (wallet_id, amount, type, correlation_id, balance_after)
            VALUES (:wallet_id, :amount, 'TOPUP', :correlation_id, :amount)
        """),
        [
            {
                "wallet_id": wallet_id,
                "amount": Decimal("1000.00"),
                "correlation_id": correlation_id,
            },
            {
                "wallet_id": gateway_id,
                "amount": Decimal("-1000.00"),
                "correlation_id": correlation_id,
            },
        ],
    )
    for label, invoice_id, wallet_amount, gateway_amount in (
        ("split", split_invoice_id, Decimal("300.00"), Decimal("424.50")),
        ("gateway", uuid4(), Decimal("0.00"), Decimal("724.50")),
    ):
        order_id = uuid4()
        await connection.execute(
            text("""
                INSERT INTO orders (
                    id, customer_id, courier_id, delivery_city, delivery_location,
                    delivery_date, status, total_amount
                ) VALUES (
                    :id, :customer_id, :courier_id, 'Jeddah',
                    ST_SetSRID(ST_MakePoint(39.2, 21.5), 4326),
                    CURRENT_DATE + 5, 'WAITING_PAYMENT', 724.50
                )
            """),
            {"id": order_id, "customer_id": customer_id, "courier_id": courier_id},
        )
        await connection.execute(
            text("""
                INSERT INTO invoices (
                    id, order_id, issued_by_courier_id, status, items_net_amount,
                    net_after_discount_amount, total_amount, amount_from_wallet,
                    amount_from_gateway, expires_at
                ) VALUES (
                    :id, :order_id, :courier_id, 'ISSUED', 724.50, 724.50, 724.50,
                    :wallet_amount, :gateway_amount, now() + interval '1 hour'
                )
            """),
            {
                "id": invoice_id,
                "order_id": order_id,
                "courier_id": courier_id,
                "wallet_amount": wallet_amount,
                "gateway_amount": gateway_amount,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO payment_intents (
                    user_id, purpose, amount, status, reference_invoice_id,
                    gateway_reference, expires_at
                ) VALUES (
                    :customer_id, 'ORDER_INVOICE', :amount, 'NEW', :invoice_id,
                    :label, now() + interval '1 hour'
                )
            """),
            {
                "customer_id": customer_id,
                "amount": gateway_amount,
                "invoice_id": invoice_id,
                "label": label,
            },
        )
    for status in ("PAID", "FAILED", "EXPIRED"):
        await connection.execute(
            text("""
                INSERT INTO payment_intents (
                    user_id, purpose, amount, status, reference_invoice_id,
                    gateway_reference, expires_at
                ) VALUES (
                    :customer_id, 'ORDER_INVOICE', 424.50, :status, :invoice_id,
                    :label, now() - interval '1 hour'
                )
            """),
            {
                "customer_id": customer_id,
                "status": status,
                "label": status,
                "invoice_id": split_invoice_id,
            },
        )
    await connection.execute(
        text("""
            INSERT INTO payment_intents (user_id, purpose, amount, gateway_reference, expires_at)
            VALUES (:customer_id, 'WALLET_TOPUP', 100.00, 'topup', now() + interval '1 hour')
        """),
        {"customer_id": customer_id},
    )
    return split_invoice_id


async def _snapshot(connection: AsyncConnection, *, exact_intents: bool = False) -> list[object]:
    snapshots: list[object] = []
    for table in ("wallets", "transactions", "invoices", "orders", "payment_intents"):
        projection = "to_jsonb(record)"
        if table == "payment_intents" and not exact_intents:
            # Successful backfill legitimately stamps updated_at on active intents.
            projection += " - 'wallet_reserved_amount' - 'updated_at'"
        snapshots.append(
            list(
                await connection.scalars(
                    text(f"SELECT {projection} FROM {table} record ORDER BY id")
                )
            )
        )
    return snapshots


async def _assert_schema(connection: AsyncConnection, *, upgraded: bool) -> None:
    columns = set(
        await connection.scalars(
            text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'payment_intents'
    """)
        )
    )
    constraints = set(
        await connection.scalars(
            text("""
        SELECT conname FROM pg_constraint WHERE conrelid = 'payment_intents'::regclass
    """)
        )
    )
    assert ("wallet_reserved_amount" in columns) is upgraded
    assert constraints & _CONSTRAINTS == (_CONSTRAINTS if upgraded else set())
    assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
        _REVISION if upgraded else _PRIOR
    )


async def _assert_reservations(connection: AsyncConnection) -> None:
    amounts = dict(
        (
            await connection.execute(
                text("""
        SELECT gateway_reference, wallet_reserved_amount FROM payment_intents
    """)
            )
        ).all()
    )
    assert amounts == {
        "split": Decimal("300.00"),
        "gateway": Decimal("0.00"),
        "PAID": Decimal("0.00"),
        "FAILED": Decimal("0.00"),
        "EXPIRED": Decimal("0.00"),
        "topup": Decimal("0.00"),
    }


async def test_reservation_migration_backfill_downgrade_and_reupgrade(
    migration_database: tuple[str, AsyncEngine],
) -> None:
    database_url, engine = migration_database
    async with engine.begin() as connection:
        await _seed_prior_rows(connection)
        before = await _snapshot(connection)

    for command, revision, upgraded in (
        ("upgrade", _REVISION, True),
        ("downgrade", _PRIOR, False),
        ("upgrade", _REVISION, True),
    ):
        await engine.dispose()
        _run_alembic(database_url, command, revision)
        async with engine.connect() as connection:
            await _assert_schema(connection, upgraded=upgraded)
            assert await _snapshot(connection) == before
            if upgraded:
                await _assert_reservations(connection)


@pytest.mark.parametrize(
    "invalid_state", ["duplicate", "cancelled", "gateway_mismatch", "split_mismatch"]
)
async def test_reservation_migration_rejection_is_atomic(
    migration_database: tuple[str, AsyncEngine], invalid_state: str
) -> None:
    database_url, engine = migration_database
    async with engine.begin() as connection:
        invoice_id = await _seed_prior_rows(connection)
        if invalid_state == "duplicate":
            await connection.execute(
                text("""
                INSERT INTO payment_intents (
                    user_id, purpose, amount, status, reference_invoice_id, expires_at
                ) SELECT user_id, purpose, amount, status, reference_invoice_id, expires_at
                  FROM payment_intents WHERE gateway_reference = 'split'
            """)
            )
        else:
            mutations = {
                "cancelled": "UPDATE invoices SET status = 'CANCELLED' WHERE id = :id",
                "gateway_mismatch": (
                    "UPDATE payment_intents SET amount = 425.50 WHERE gateway_reference = 'split'"
                ),
                "split_mismatch": "UPDATE invoices SET amount_from_wallet = 301.00 WHERE id = :id",
            }
            await connection.execute(text(mutations[invalid_state]), {"id": invoice_id})
        before = await _snapshot(connection, exact_intents=True)

    await engine.dispose()
    result = _run_alembic(database_url, "upgrade", _REVISION, expect_success=False)
    assert result.returncode != 0
    assert "Ambiguous active payment reservations require reconciliation" in result.stderr
    async with engine.connect() as connection:
        await _assert_schema(connection, upgraded=False)
        assert await _snapshot(connection, exact_intents=True) == before

"""Real PostgreSQL courier settlement aggregation and optimistic target checks."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.exceptions import ConflictError
from app.models import City, CourierProfile, Invoice, Order, Transaction, User, Wallet
from app.models.enums import TransactionStatus, TransactionType, UserRole, WalletType
from app.repositories.courier_report_repository import CourierReportRepository
from app.schemas.courier_report_schemas import TargetUpdate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _courier(session: AsyncSession) -> tuple[User, Wallet]:
    city = await session.scalar(select(City).limit(1))
    user = User(phone=f"+96650{uuid4().int % 10000000:07d}", role=UserRole.COURIER)
    session.add(user)
    await session.flush()
    session.add(
        CourierProfile(
            user_id=user.id,
            city_of_residence_id=city.id,
            national_id_encrypted="test",
            is_verified=True,
        )
    )
    wallet = Wallet(user_id=user.id, type=WalletType.COURIER)
    session.add(wallet)
    await session.flush()
    return user, wallet


async def test_signed_settled_proceeds_riyadh_boundaries_exclusions_and_owner(
    db_session: AsyncSession,
):
    courier, wallet = await _courier(db_session)
    other, other_wallet = await _courier(db_session)
    city = await db_session.scalar(select(City).limit(1))
    order = Order(
        customer_id=other.id,
        courier_id=courier.id,
        delivery_city_id=city.id,
        delivery_date=date.today(),
    )
    db_session.add(order)
    await db_session.flush()
    invoice = Invoice(order_id=order.id, issued_by_courier_id=courier.id)
    db_session.add(invoice)
    await db_session.flush()
    for amount, hour, minute, status, kind, owned in [
        ("150.10", 21, 0, TransactionStatus.SETTLED, TransactionType.ESCROW_RELEASE, True),
        ("-20.01", 22, 0, TransactionStatus.SETTLED, TransactionType.ESCROW_RELEASE, True),
        ("500.00", 20, 59, TransactionStatus.SETTLED, TransactionType.ESCROW_RELEASE, True),
        ("600.00", 22, 0, TransactionStatus.PENDING, TransactionType.ESCROW_RELEASE, True),
        ("700.00", 22, 0, TransactionStatus.REVERSED, TransactionType.ESCROW_RELEASE, True),
        ("800.00", 22, 0, TransactionStatus.SETTLED, TransactionType.TOPUP, True),
        ("900.00", 22, 0, TransactionStatus.SETTLED, TransactionType.ESCROW_RELEASE, False),
    ]:
        db_session.add(
            Transaction(
                wallet_id=wallet.id if owned else other_wallet.id,
                amount=Decimal(amount),
                type=kind,
                status=status,
                reference_order_id=order.id,
                reference_invoice_id=invoice.id,
                correlation_id=uuid4(),
                balance_after=Decimal("0.00"),
                created_at=datetime(2026, 10, 10, hour, minute, tzinfo=UTC),
            )
        )
    await db_session.flush()
    repo = CourierReportRepository(db_session)
    start, end = datetime(2026, 10, 10, 21, tzinfo=UTC), datetime(2026, 10, 11, 21, tzinfo=UTC)
    assert await repo.total(courier.id, start, end) == Decimal("130.09")
    assert await repo.buckets(courier.id, start, end, "day") == {
        date(2026, 10, 11): Decimal("130.09")
    }
    assert await repo.buckets(courier.id, start, end, "week") == {
        date(2026, 10, 11): Decimal("130.09")
    }
    assert await repo.buckets(courier.id, start, end, "month") == {
        date(2026, 10, 1): Decimal("130.09")
    }
    assert await repo.totals(courier.id, start, end, datetime(2026, 10, 9, 21, tzinfo=UTC)) == (
        Decimal("130.09"),
        Decimal("500.00"),
    )


async def test_settings_owner_initialization_stale_insert_and_update(db_session: AsyncSession):
    courier, _ = await _courier(db_session)
    other, _ = await _courier(db_session)
    repo = CourierReportRepository(db_session)
    assert await repo.settings(courier.id) is None
    body = TargetUpdate(
        version=0, target_enabled=True, target_amount="1000.00", target_period="WEEK"
    )
    row = await repo.replace_settings(courier.id, body)
    assert row.version == 1
    assert row.amount == Decimal("1000.00")
    assert await repo.settings(other.id) is None
    with pytest.raises(ConflictError):
        await repo.replace_settings(courier.id, body)
    updated = await repo.replace_settings(courier.id, TargetUpdate(version=1, target_enabled=False))
    assert updated.version == 2
    assert updated.amount is None and updated.period is None
    with pytest.raises(ConflictError):
        await repo.replace_settings(courier.id, TargetUpdate(version=1, target_enabled=False))


async def test_settings_concurrent_updates_have_one_winner(db_session: AsyncSession):
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            courier, _ = await _courier(session)
            courier_id = courier.id
            await CourierReportRepository(session).replace_settings(
                courier_id,
                TargetUpdate(
                    version=0, target_enabled=True, target_amount="100.00", target_period="WEEK"
                ),
            )
            await session.commit()

        async def replace(amount):
            async with factory() as session:
                try:
                    await CourierReportRepository(session).replace_settings(
                        courier_id,
                        TargetUpdate(
                            version=1,
                            target_enabled=True,
                            target_amount=amount,
                            target_period="WEEK",
                        ),
                    )
                    await session.commit()
                    return "won"
                except ConflictError:
                    await session.rollback()
                    return "conflict"

        results = await asyncio.gather(replace("200.00"), replace("300.00"))
        assert sorted(results) == ["conflict", "won"]
        async with factory() as session:
            row = await CourierReportRepository(session).settings(courier_id)
            assert row.version == 2
            assert row.amount in {Decimal("200.00"), Decimal("300.00")}
    finally:
        await engine.dispose()


async def test_populated_settings_migration_downgrade_refuses_loss(db_session: AsyncSession):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("app.migrations.versions.0029_courier_report_settings")
    courier, _ = await _courier(db_session)
    await CourierReportRepository(db_session).replace_settings(
        courier.id, TargetUpdate(version=0, target_enabled=False)
    )
    connection = await db_session.connection()

    def downgrade(sync_connection):
        with Operations.context(MigrationContext.configure(sync_connection)):
            migration.downgrade()

    with pytest.raises(RuntimeError, match="Preserve persisted"):
        await connection.run_sync(downgrade)
    assert await CourierReportRepository(db_session).settings(courier.id) is not None


async def test_large_settlement_history_still_uses_two_aggregate_queries(db_session: AsyncSession):
    from sqlalchemy import event, func, text
    from sqlalchemy.dialects import postgresql

    courier, wallet = await _courier(db_session)
    city = await db_session.scalar(select(City).limit(1))
    order = Order(
        customer_id=courier.id,
        courier_id=courier.id,
        delivery_city_id=city.id,
        delivery_date=date.today(),
    )
    db_session.add(order)
    await db_session.flush()
    invoice = Invoice(order_id=order.id, issued_by_courier_id=courier.id)
    db_session.add(invoice)
    await db_session.flush()
    stamp = datetime(2026, 10, 11, 0, tzinfo=UTC)
    db_session.add_all(
        [
            Transaction(
                wallet_id=wallet.id,
                amount=Decimal("1.01"),
                type=TransactionType.ESCROW_RELEASE,
                status=TransactionStatus.SETTLED,
                reference_order_id=order.id,
                reference_invoice_id=invoice.id,
                correlation_id=uuid4(),
                balance_after=Decimal("0.00"),
                created_at=stamp,
            )
            for _ in range(2000)
        ]
    )
    await db_session.flush()
    connection = await db_session.connection()
    statements = []

    def count_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", count_statement)
    repo = CourierReportRepository(db_session)
    start, end = datetime(2026, 10, 10, 21, tzinfo=UTC), datetime(2026, 10, 11, 21, tzinfo=UTC)
    try:
        assert await repo.total(courier.id, start, end) == Decimal("2020.00")
        assert await repo.buckets(courier.id, start, end, "day") == {
            date(2026, 10, 11): Decimal("2020.00")
        }
        assert len(statements) == 2
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", count_statement)
    query = repo._proceeds(courier.id, start, end).with_only_columns(func.sum(Transaction.amount))
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    plan = (await db_session.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql))).scalars().all()
    assert any("Aggregate" in line for line in plan)
    print("Courier report synthetic 2000-row plan:\n" + "\n".join(plan))

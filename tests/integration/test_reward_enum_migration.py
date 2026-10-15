"""Forward reward enum upgrades and rollback refusal preserve financial history."""

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession


async def test_current_schema_accepts_forward_reward_labels(db_session: AsyncSession) -> None:
    labels = await db_session.scalar(text("SELECT enum_range(NULL::transaction_type)::text"))
    assert "GIVEAWAY" in labels
    assert "PROMO_CREDIT" in labels


@pytest.mark.parametrize("reward_type", ["GIVEAWAY", "PROMO_CREDIT", None])
async def test_reward_enum_downgrade_retains_used_values(
    db_session: AsyncSession, reward_type: str | None
) -> None:
    migration = import_module("app.migrations.versions.0035_reward_transaction_types")
    await db_session.execute(
        text("CREATE TEMP TABLE transactions (type transaction_type) ON COMMIT DROP")
    )
    if reward_type is not None:
        await db_session.execute(
            text("INSERT INTO transactions(type) VALUES (CAST(:kind AS transaction_type))"),
            {"kind": reward_type},
        )

    def attempt(sync_session):
        with Operations.context(MigrationContext.configure(sync_session.connection())):
            migration.downgrade()

    if reward_type is None:
        await db_session.run_sync(attempt)
    else:
        with pytest.raises(DBAPIError, match="Retain reward transaction support"):
            async with db_session.begin_nested():
                await db_session.run_sync(attempt)
        assert await db_session.scalar(text("SELECT count(*) FROM transactions")) == 1
    labels = await db_session.scalar(text("SELECT enum_range(NULL::transaction_type)::text"))
    assert "GIVEAWAY" in labels
    assert "PROMO_CREDIT" in labels

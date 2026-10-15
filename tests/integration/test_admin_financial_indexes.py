"""Financial admin chronology indexes exist and can be rolled back without data changes."""

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import Invoice, Transaction, WalletTopup
from sqlalchemy import text


@pytest.mark.parametrize("model", [Invoice, Transaction, WalletTopup])
async def test_financial_chronology_index_matches_models_and_database(db_session, model):
    name = f"idx_{model.__tablename__}_created"
    assert name in {index.name for index in model.__table__.indexes}
    definition = await db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND indexname=:name"
        ),
        {"name": name},
    )
    assert definition and "(created_at DESC, id DESC)" in definition


async def test_financial_chronology_downgrade_and_upgrade_are_data_preserving(db_session):
    migration = import_module("app.migrations.versions.0036_admin_financial_chronology")

    def run(sync_session, operation):
        with Operations.context(MigrationContext.configure(sync_session.connection())):
            operation()

    await db_session.run_sync(run, migration.downgrade)
    for model in (Invoice, Transaction, WalletTopup):
        assert (
            await db_session.scalar(
                text("SELECT to_regclass(:name)"), {"name": f"idx_{model.__tablename__}_created"}
            )
            is None
        )
    await db_session.run_sync(run, migration.upgrade)
    for model in (Invoice, Transaction, WalletTopup):
        assert (
            await db_session.scalar(
                text("SELECT to_regclass(:name)"), {"name": f"idx_{model.__tablename__}_created"}
            )
            is not None
        )

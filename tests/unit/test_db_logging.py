from app.core.db import build_engine

from tests.conftest import make_test_settings


async def test_debug_sql_logging_hides_bound_credentials_and_personal_data():
    engine = build_engine(make_test_settings(ENVIRONMENT="development", DEBUG=True))
    try:
        assert engine.sync_engine.echo is True
        assert engine.sync_engine.hide_parameters is True
    finally:
        await engine.dispose()


async def test_database_pool_uses_configured_connection_budget() -> None:
    engine = build_engine(
        make_test_settings(DB_POOL_SIZE=2, DB_MAX_OVERFLOW=0, DB_POOL_TIMEOUT_SECONDS=4)
    )
    try:
        pool = engine.sync_engine.pool
        assert pool.size() == 2
        assert pool._max_overflow == 0
        assert pool.timeout() == 4
    finally:
        await engine.dispose()

from app.core.db import build_engine

from tests.conftest import make_test_settings


async def test_debug_sql_logging_hides_bound_credentials_and_personal_data():
    engine = build_engine(make_test_settings(ENVIRONMENT="development", DEBUG=True))
    try:
        assert engine.sync_engine.echo is True
        assert engine.sync_engine.hide_parameters is True
    finally:
        await engine.dispose()

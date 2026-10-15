"""Public catalog caching retains current data and transactional invalidation."""

import os

from app.core.deps import get_db
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text

from tests.conftest import make_test_settings


async def test_catalog_second_read_avoids_reloading_city_rows(db_session):
    app = create_app(make_test_settings(REDIS_URL=os.environ["REDIS_URL"]))
    await db_session.execute(
        text("SELECT giftly_bump_read_cache('catalog', '00000000-0000-0000-0000-000000000000')")
    )

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    connection = await db_session.connection()
    statements = []

    def capture(conn, cursor, statement, params, context, many):
        if "FROM cities" in statement:
            statements.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", capture)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.get("/api/cities")
            second = await client.get("/api/cities")
            assert len(statements) == 1
            nested = await db_session.begin_nested()
            await db_session.execute(
                text("UPDATE cities SET name_ar='Updated catalog label' WHERE name='Riyadh'")
            )
            changed = await client.get("/api/cities")
            assert changed.json() != first.json()
            assert any(city["name_ar"] == "Updated catalog label" for city in changed.json())
            await nested.rollback()
            restored = await client.get("/api/cities")
            assert restored.json() == first.json()
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert len(statements) == 2
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", capture)
        await app.state.redis.aclose()
        await app.state.subscription_redis.aclose()
        await app.state.engine.dispose()


async def test_read_versions_are_available_without_exposing_business_data(db_session):
    assert await db_session.scalar(text("SELECT to_regclass('read_cache_versions')")) is not None

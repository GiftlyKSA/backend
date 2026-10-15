"""Measure audit catalog query reuse with disposable PostgreSQL and Redis."""

import os
from uuid import uuid4

import pytest
from app.repositories.audit_repository import AuditRepository
from app.services.audit_choice_cache import audit_filter_choices
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import event


async def test_audit_catalog_hit_skips_both_history_queries(db_session):
    redis = Redis.from_url(
        os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15"), decode_responses=True
    )
    namespace = f"test:audit-choices:{uuid4()}:"
    key = namespace + "cache:admin-audit-choices:v1:system"

    class ScopedRedis:
        async def get(self, name):
            return await redis.get(namespace + name)

        async def set(self, name, value, **kwargs):
            return await redis.set(namespace + name, value, **kwargs)

    connection = await db_session.connection()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "SELECT DISTINCT audit_logs." in statement:
            statements.append(statement)

    try:
        try:
            await redis.ping()
        except RedisError:
            pytest.skip("Disposable Redis is unavailable.")
        event.listen(connection.sync_connection, "before_cursor_execute", capture)
        repository = AuditRepository(db_session)
        first = await audit_filter_choices(ScopedRedis(), repository, ("SYSTEM",))
        assert len(statements) == 2
        assert await audit_filter_choices(ScopedRedis(), repository, ("SYSTEM",)) == first
        assert len(statements) == 2
        assert 0 < await redis.ttl(key) <= 30
        await redis.delete(key)
        assert await audit_filter_choices(ScopedRedis(), repository, ("SYSTEM",)) == first
        assert len(statements) == 4
    finally:
        if event.contains(connection.sync_connection, "before_cursor_execute", capture):
            event.remove(connection.sync_connection, "before_cursor_execute", capture)
        await redis.delete(key)
        await redis.aclose()

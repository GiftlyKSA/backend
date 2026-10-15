"""Audit dropdown cache preserves categories and falls back to authoritative reads."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.repositories.audit_repository import AuditFilterChoices
from app.services.admin_service import AdminService
from app.services.audit_choice_cache import audit_filter_choices
from redis.exceptions import ConnectionError


async def test_audit_dropdown_reuses_category_cache_without_caching_rows():
    from tests.conftest import make_test_settings

    stored = {}

    async def get(key):
        return stored.get(key)

    async def set_value(key, value, **kwargs):
        assert kwargs["ex"] == 30
        stored[key] = value

    redis = SimpleNamespace(get=AsyncMock(side_effect=get), set=AsyncMock(side_effect=set_value))
    audit = SimpleNamespace(
        filter_choices=AsyncMock(return_value=AuditFilterChoices(["CREATE"], ["users"]))
    )
    service = AdminService(
        reads=None,
        users=None,
        couriers=None,
        orders=None,
        promos=None,
        audit=audit,
        auth_repo=None,
        redis=redis,
        settings=make_test_settings(),
    )
    assert (await service.audit_filter_choices(("SYSTEM",))).actions == ["CREATE"]
    assert (await service.audit_filter_choices(("SYSTEM",))).entities == ["users"]
    audit.filter_choices.assert_awaited_once_with(("SYSTEM",))
    await service.audit_filter_choices(("ADMIN",))
    assert audit.filter_choices.await_count == 2
    assert len(stored) == 2


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        '{"actions":[123],"entities":[]}',
        json.dumps({"actions": ["a"] * 201, "entities": []}),
        json.dumps({"actions": ["a" * 101], "entities": []}),
        "x" * 262145,
    ],
    ids=["malformed", "wrong-shape", "non-string", "too-many", "long-label", "oversized"],
)
async def test_invalid_cache_falls_back_without_breaking_dropdown(stored):
    redis = SimpleNamespace(get=AsyncMock(return_value=stored), set=AsyncMock())
    repo = SimpleNamespace(filter_choices=AsyncMock(return_value=AuditFilterChoices([], [])))
    assert await audit_filter_choices(redis, repo, ("ADMIN",)) == AuditFilterChoices([], [])
    repo.filter_choices.assert_awaited_once()


async def test_cache_connection_failure_does_not_hide_authoritative_choices():
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=ConnectionError("unavailable")),
        set=AsyncMock(side_effect=ConnectionError("unavailable")),
    )
    repo = SimpleNamespace(
        filter_choices=AsyncMock(return_value=AuditFilterChoices(["UPDATE"], ["cities"]))
    )
    assert (await audit_filter_choices(redis, repo, ("SYSTEM",))).actions == ["UPDATE"]
    repo.filter_choices.assert_awaited_once()


async def test_arbitrary_category_combinations_do_not_grow_cache():
    redis = SimpleNamespace(get=AsyncMock(), set=AsyncMock())
    repo = SimpleNamespace(filter_choices=AsyncMock(return_value=AuditFilterChoices([], [])))
    await audit_filter_choices(redis, repo, ("CUSTOMER",))
    redis.get.assert_not_awaited()
    redis.set.assert_not_awaited()


async def test_invalid_utf8_cache_falls_back_to_database():
    redis = SimpleNamespace(
        get=AsyncMock(side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")),
        set=AsyncMock(),
    )
    repo = SimpleNamespace(filter_choices=AsyncMock(return_value=AuditFilterChoices([], [])))
    assert await audit_filter_choices(redis, repo, ("SYSTEM",)) == AuditFilterChoices([], [])
    repo.filter_choices.assert_awaited_once()

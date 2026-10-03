"""Customer occasion boundaries, ownership and pagination regressions."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.deps import Actor, get_db, require_auth
from app.core.exceptions import NotFoundError
from app.main import create_app
from app.models.enums import UserRole
from app.repositories.planning_repository import PlanningRepository
from app.routers import occasions
from app.schemas.occasions import CreateOccasionRequest, UpdateOccasionRequest
from app.services.occasion_service import OccasionService
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tests.conftest import make_test_settings


def row():
    return SimpleNamespace(
        id=uuid4(),
        title="Anniversary",
        occasion_date=date(2026, 12, 15),
        reminder_days_before=7,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "body",
    [
        {"title": " ", "occasion_date": "2026-12-15"},
        {"title": "Test", "occasion_date": "2026-12-15", "reminder_days_before": 366},
        {"title": "Test", "occasion_date": "2026-12-15", "reminder_days_before": True},
        {"title": "Test", "occasion_date": "2026-12-15", "user_id": str(uuid4())},
    ],
)
def test_create_rejects_invalid_and_mass_assignment(body):
    with pytest.raises(ValidationError):
        CreateOccasionRequest.model_validate(body)


@pytest.mark.parametrize("body", [{}, {"title": None}, {"title": " "}, {"id": str(uuid4())}])
def test_patch_rejects_empty_null_and_readonly_fields(body):
    with pytest.raises(ValidationError):
        UpdateOccasionRequest.model_validate(body)


async def test_create_binds_customer_and_preserves_code_as_text():
    repository = AsyncMock()
    actor = uuid4()
    await OccasionService(repository).create(
        actor,
        title="<script>alert(1)</script>",
        occasion_date=date(2026, 12, 15),
        reminder_days_before=0,
    )
    repository.create_occasion.assert_awaited_once_with(
        user_id=actor,
        title="<script>alert(1)</script>",
        occasion_date=date(2026, 12, 15),
        reminder_days_before=0,
        featured_gift_id=None,
    )


async def test_update_foreign_record_never_writes():
    repository = AsyncMock()
    repository.get_occasion_for_actor.return_value = None
    actor, identifier = uuid4(), uuid4()
    with pytest.raises(NotFoundError):
        await OccasionService(repository).update(
            actor, identifier, title="Other", occasion_date=None, reminder_days_before=None
        )
    repository.get_occasion_for_actor.assert_awaited_once_with(identifier, actor, lock=True)
    repository.update_occasion.assert_not_awaited()


async def test_delete_foreign_record_is_not_found():
    repository = AsyncMock()
    repository.delete_occasion_for_actor.return_value = False
    with pytest.raises(NotFoundError):
        await OccasionService(repository).delete(uuid4(), uuid4())


async def test_list_foreign_cursor_cannot_restart_history():
    repository = AsyncMock()
    repository.get_occasion_for_actor.return_value = None
    with pytest.raises(NotFoundError):
        await OccasionService(repository).list(uuid4(), limit=25, cursor=uuid4(), from_date=None)
    repository.list_occasions_for_actor.assert_not_awaited()


async def test_list_reads_one_extra_row_for_real_next_cursor():
    repository = AsyncMock()
    rows = [row() for _ in range(3)]
    repository.list_occasions_for_actor.return_value = rows
    actor = uuid4()
    items, cursor = await OccasionService(repository).list(
        actor, limit=2, cursor=None, from_date=None
    )
    assert items == rows[:2] and cursor == rows[1].id
    repository.list_occasions_for_actor.assert_awaited_once_with(
        actor, limit=3, from_date=None, after=None
    )
    repository.list_occasions_for_actor.return_value = rows[:2]
    _, cursor = await OccasionService(repository).list(actor, limit=2, cursor=None, from_date=None)
    assert cursor is None


async def test_repository_list_scopes_and_bounds_single_query():
    session = AsyncMock()
    session.scalars.return_value = []
    await PlanningRepository(session).list_occasions_for_actor(uuid4(), limit=26, after=row())
    query = session.scalars.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "occasions.user_id =" in sql and "LIMIT" in sql
    assert "(occasions.occasion_date, occasions.id) >" in sql
    session.scalars.assert_awaited_once()


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.COURIER, UserRole.ADMIN])
async def test_routes_enforce_customer_role_and_wire_contract(monkeypatch, role):
    app = create_app(make_test_settings())
    actor = Actor(id=uuid4(), role=role, jti="test")
    service = AsyncMock()
    saved = row()
    service.create.return_value = saved
    service.get.return_value = saved
    service.update.return_value = saved
    service.list.return_value = ([saved], None)
    monkeypatch.setattr(occasions, "_service", lambda db: service)

    async def no_database():
        yield AsyncMock()

    app.dependency_overrides[get_db] = no_database
    app.dependency_overrides[require_auth] = lambda: actor
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, path, body in [
            ("POST", "/api/occasions", {"title": "Anniversary", "occasion_date": "2026-12-15"}),
            ("GET", "/api/occasions", None),
            ("GET", f"/api/occasions/{saved.id}", None),
            ("PATCH", f"/api/occasions/{saved.id}", {"title": "Birthday"}),
            ("DELETE", f"/api/occasions/{saved.id}", None),
        ]:
            response = await client.request(method, path, json=body)
            if role is not UserRole.CUSTOMER:
                assert response.status_code == 403
            elif method == "DELETE":
                assert response.status_code == 204 and not response.content
            elif method == "GET" and path == "/api/occasions":
                assert response.status_code == 200 and response.json()["next_cursor"] is None
            else:
                assert response.status_code == (201 if method == "POST" else 200)
                assert response.json()["occasion_date"] == "2026-12-15"
                assert "user_id" not in response.json()

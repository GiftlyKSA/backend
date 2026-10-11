from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.schemas.courier_report_schemas import TargetUpdate
from app.services.courier_report_service import CourierReportService, bucket_start
from pydantic import ValidationError


def test_sunday_and_month_buckets():
    assert bucket_start(date(2026, 10, 10), "week") == date(2026, 10, 4)
    assert bucket_start(date(2026, 10, 11), "week") == date(2026, 10, 11)
    assert bucket_start(date(2026, 10, 10), "month") == date(2026, 10, 1)


@pytest.mark.parametrize("value", [0, -1, "1000000.01", 1.5, "NaN"])
def test_invalid_target_money(value):
    with pytest.raises(ValidationError):
        TargetUpdate(version=0, target_enabled=True, target_amount=value, target_period="WEEK")


async def test_report_exact_totals_zero_fill_and_previous_bounds():
    repo, eligibility = AsyncMock(), AsyncMock()
    repo.totals.return_value = (Decimal("123.45"), Decimal("100.00"))
    repo.buckets.return_value = {date(2026, 10, 1): Decimal("123.45")}
    repo.settings.return_value = None
    result = await CourierReportService(repo, eligibility).earnings(
        uuid4(), date(2026, 10, 1), date(2026, 10, 3), "day"
    )
    assert result.settled_total == "123.45"
    assert result.previous.difference == "23.45"
    assert result.previous.percentage == "23.45"
    assert [b.amount for b in result.buckets] == ["123.45", "0.00", "0.00"]
    assert result.previous.from_date == date(2026, 9, 28)
    assert repo.totals.call_args.args[1:] == (
        datetime(2026, 9, 30, 21, tzinfo=UTC),
        datetime(2026, 10, 3, 21, tzinfo=UTC),
        datetime(2026, 9, 27, 21, tzinfo=UTC),
    )
    eligibility.require_courier.assert_awaited_once()


async def test_report_rejects_unbounded_or_extreme_range():
    repo = AsyncMock()
    service = CourierReportService(repo, AsyncMock())
    for start, end in [
        (date(2025, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 2), date(2026, 1, 1)),
        (date.min, date.min),
    ]:
        with pytest.raises(ValidationDomainError):
            await service.earnings(uuid4(), start, end, "day")
    repo.totals.assert_not_awaited()


async def test_zero_baseline_and_disabled_target():
    repo = AsyncMock()
    repo.totals.return_value = (Decimal("0.00"), Decimal("0.00"))
    repo.buckets.return_value = {}
    repo.settings.return_value = None
    service = CourierReportService(repo, AsyncMock())
    result = await service.earnings(uuid4(), date(2026, 10, 1), date(2026, 10, 1), "day")
    assert result.previous.percentage is None
    assert result.previous.comparison == "UNCHANGED"
    assert result.target is None
    repo.totals.return_value = (Decimal("1.00"), Decimal("0.00"))
    result = await service.earnings(uuid4(), date(2026, 10, 1), date(2026, 10, 1), "day")
    assert result.previous.comparison == "NO_BASELINE"


async def test_target_progress_uses_complete_current_period_and_exceeds_100():
    from types import SimpleNamespace

    from app.services.courier_report_service import RIYADH, next_bucket

    repo = AsyncMock()
    repo.settings.return_value = SimpleNamespace(
        version=1,
        enabled=True,
        amount=Decimal("100.00"),
        period="WEEK",
        updated_at=datetime.now(UTC),
    )
    repo.total.return_value = Decimal("125.01")
    result = await CourierReportService(repo, AsyncMock()).settings(uuid4())
    assert result.progress.progress_percent == "125.01"
    assert result.progress.amount == "100.00"
    assert result.progress.settled_total == "125.01"
    today = datetime.now(UTC).astimezone(RIYADH).date()
    assert result.progress.period_start == bucket_start(today, "week")
    assert result.progress.period_end == next_bucket(
        bucket_start(today, "week"), "week"
    ) - timedelta(days=1)


async def test_partial_setting_update_preserves_amount_and_period():
    from types import SimpleNamespace

    repo = AsyncMock()
    repo.settings.return_value = SimpleNamespace(
        version=1,
        enabled=True,
        amount=Decimal("200.00"),
        period="MONTH",
        updated_at=datetime.now(UTC),
    )
    repo.total.return_value = Decimal("0.00")
    await CourierReportService(repo, AsyncMock()).update_settings(
        uuid4(), TargetUpdate(version=1, target_amount="250.00")
    )
    updated = repo.replace_settings.call_args.args[1]
    assert updated.target_enabled is True
    assert updated.target_amount == "250.00"
    assert updated.target_period == "MONTH"


async def test_eligibility_failure_never_reads_private_reports():
    from app.core.exceptions import ForbiddenError

    repo, eligibility = AsyncMock(), AsyncMock()
    eligibility.require_courier.side_effect = ForbiddenError()
    service = CourierReportService(repo, eligibility)
    with pytest.raises(ForbiddenError):
        await service.earnings(uuid4(), date(2026, 10, 1), date(2026, 10, 1), "day")
    with pytest.raises(ForbiddenError):
        await service.settings(uuid4())
    repo.totals.assert_not_awaited()
    repo.settings.assert_not_awaited()


async def test_sql_scope_settlement_and_signed_sum():
    from unittest.mock import MagicMock

    from app.repositories.courier_report_repository import CourierReportRepository
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.one.return_value = (Decimal("90.00"), Decimal("0.00"))
    actor = uuid4()
    now = datetime(2026, 10, 1, tzinfo=UTC)
    await CourierReportRepository(session).totals(actor, now, now, now)
    query = session.execute.call_args.args[0]
    compiled = query.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "sum(CASE" in sql
    assert "wallets.user_id =" in sql
    assert "transactions.amount >" not in sql
    assert "ESCROW_RELEASE" in compiled.params.values()
    assert "SETTLED" in compiled.params.values()
    assert actor in compiled.params.values()
    assert "transactions.reference_order_id IS NOT NULL" in sql
    assert "transactions.created_at <" in sql


async def test_stale_settings_conflict_and_owner_compare_and_swap():
    from app.core.exceptions import ConflictError
    from app.repositories.courier_report_repository import CourierReportRepository
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    session.scalar.return_value = None
    actor = uuid4()
    with pytest.raises(ConflictError):
        await CourierReportRepository(session).replace_settings(
            actor, TargetUpdate(version=5, target_enabled=False)
        )
    compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "courier_report_settings.version =" in str(compiled)
    assert actor in compiled.params.values()
    assert 5 in compiled.params.values()
    assert 6 in compiled.params.values()


async def test_courier_report_http_contract_role_and_validation(monkeypatch):
    from app.core.deps import Actor, get_db, require_auth
    from app.core.middleware import register_exception_handlers
    from app.models.enums import UserRole
    from app.routers import courier_report_routes
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(courier_report_routes.router)
    actor = Actor(uuid4(), UserRole.COURIER, "test")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: actor
    repo = AsyncMock()
    repo.totals.return_value = (Decimal("0.00"), Decimal("0.00"))
    repo.buckets.return_value = {}
    repo.settings.return_value = None
    monkeypatch.setattr(
        courier_report_routes, "_service", lambda db: CourierReportService(repo, AsyncMock())
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        url = "/api/couriers/me/earnings"
        result = await client.get(url, params={"from_date": "2026-10-01", "to_date": "2026-10-03"})
        assert result.status_code == 200
        assert result.headers["cache-control"] == "private, no-store"
        assert result.json()["basis"] == "SETTLED_COURIER_PROCEEDS"
        assert result.json()["pending_total"] is None
        assert result.json()["target"] is None
        for params in [
            {},
            {"from_date": "2026-10-01", "to_date": "2026-10-03", "granularity": "year"},
            {"from_date": "2026-10-01T00:00:00", "to_date": "2026-10-03"},
        ]:
            assert (await client.get(url, params=params)).status_code == 422
        settings = await client.get("/api/couriers/me/report-settings")
        assert settings.status_code == 200
        assert settings.json()["version"] == 0
        assert "financial_notifications" not in settings.json()
        assert (
            await client.patch(
                "/api/couriers/me/report-settings", json={"version": 0, "target_enabled": True}
            )
        ).status_code == 422
        actor = Actor(uuid4(), UserRole.CUSTOMER, "test")
        assert (
            await client.get(url, params={"from_date": "2026-10-01", "to_date": "2026-10-03"})
        ).status_code == 403
        assert (await client.get("/api/couriers/me/report-settings")).status_code == 403

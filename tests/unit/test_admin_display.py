"""Readable object labels and consistent Riyadh timestamps at admin boundaries."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.admin.router import browse_options
from app.core.admin_time import admin_datetime, admin_input, finalize_admin_value
from app.models import Base
from app.repositories.admin_table_repository import AdminTableRepository, record_label


def test_admin_times_round_trip_and_date_only_values_are_unchanged():
    instant = datetime(2026, 10, 1, 23, 30, tzinfo=UTC)
    assert admin_datetime(instant) == "2026-10-02 02:30:00 UTC+3"
    assert admin_input(datetime(2026, 10, 2, 2, 30)).astimezone(UTC) == instant
    birthday = date(2000, 1, 1)
    assert finalize_admin_value(birthday) == birthday
    options = browse_options(
        page_size=25,
        sort_by="created_at",
        direction="desc",
        filter_field="",
        filter_value="",
        start_at=datetime(2026, 10, 2, 2, 30),
        end_at=None,
        after=None,
        before=None,
        cursor_at=instant,
    )
    assert options.start_at.astimezone(UTC) == instant
    assert options.cursor_at == instant


def test_user_label_prefers_name_email_and_public_identifier_to_uuid():
    record_id = uuid4()
    table = Base.metadata.tables["users"]
    row = {column.name: None for column in table.columns}
    row.update(
        id=record_id, full_name="Alice", email="alice@example.com", public_identifier=1234567
    )
    assert record_label(table, row) == "Alice · alice@example.com · 1234567"
    assert str(record_id) not in record_label(table, row)


async def test_entity_labels_are_batched_and_do_not_fetch_secrets():
    ids = {uuid4(), uuid4()}
    table = Base.metadata.tables["users"]
    rows = []
    for record_id in ids:
        row = {column.name: None for column in table.columns}
        row.update(id=record_id, full_name="Alice", email="alice@example.com")
        rows.append(row)
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: rows))
    )
    labels = await AdminTableRepository(session).labels_for_records({"users": ids, "unknown": ids})
    assert len(labels) == 2
    session.execute.assert_awaited_once()
    sql = str(session.execute.call_args.args[0])
    assert "users.email" in sql and "users.full_name" in sql
    assert "encrypted" not in sql and "password" not in sql

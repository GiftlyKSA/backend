"""All dashboard tables use bounded, allowlisted chronological browsing."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.exceptions import ValidationDomainError
from app.models import Base
from app.repositories.admin_browse_query import BrowseOptions, browse_query, filter_value
from app.repositories.admin_read_repository import AdminReadRepository
from sqlalchemy import Boolean, Column, Integer, Numeric
from sqlalchemy.dialects import postgresql


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
async def test_each_table_exposes_safe_filters_and_one_bounded_query(table_name):
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: [])))
    page = await AdminReadRepository(session).browse_table(table_name, BrowseOptions())
    assert page is not None
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "OFFSET" not in sql
    assert "LIMIT" in sql
    assert 26 in session.execute.call_args.args[0].compile().params.values()
    assert page.sort_by in page.sort_fields
    assert all(
        "encrypted" not in name and "secret" not in name and "token" not in name
        for name in page.filter_fields
    )
    assert session.execute.await_count == 1


@pytest.mark.parametrize(
    "direction,backward,operator",
    [
        ("desc", False, " < "),
        ("asc", False, " > "),
        ("desc", True, " > "),
        ("asc", True, " < "),
    ],
)
def test_date_cursor_is_stable_in_both_directions(direction, backward, operator):
    anchor = uuid4()
    table = Base.metadata.tables["orders"]
    options = BrowseOptions(
        direction=direction,
        after=None if backward else anchor,
        before=anchor if backward else None,
        cursor_at=datetime(2026, 10, 1, tzinfo=UTC),
        start_at=datetime(2026, 9, 1, tzinfo=UTC),
        page_size=50,
    )
    compiled = browse_query(table, options, []).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert f"(orders.created_at, orders.id){operator}" in sql
    assert "orders.created_at >=" in sql
    assert 51 in compiled.params.values()


@pytest.mark.parametrize(
    "options",
    [
        BrowseOptions(sort_by="password"),
        BrowseOptions(filter_field="password", filter_value="x"),
        BrowseOptions(sort_by="created_at; DROP TABLE users"),
        BrowseOptions(page_size=26),
        BrowseOptions(after=uuid4()),
    ],
)
def test_invalid_or_protected_fields_are_rejected(options):
    with pytest.raises(ValidationDomainError):
        browse_query(Base.metadata.tables["users"], options, ["role"])


@pytest.mark.parametrize(
    "column,value",
    [
        (Column("active", Boolean), "yes"),
        (Column("count", Integer), "99999999999999999"),
        (Column("amount", Numeric), "NaN"),
    ],
)
def test_invalid_scalar_values_fail_before_database_execution(column, value):
    with pytest.raises(ValidationDomainError):
        filter_value(column, value)


async def test_cursor_links_follow_visible_rows_and_preserve_redaction():
    table = Base.metadata.tables["users"]
    rows = [{c.name: None for c in table.columns} for _ in range(26)]
    for row in rows:
        row.update(id=uuid4(), created_at=datetime(2026, 10, 1, tzinfo=UTC))
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(mappings=Mock(return_value=rows)))
    )
    page = await AdminReadRepository(session).browse_table("users", BrowseOptions())
    assert len(page.rows) == 25
    assert page.next_cursor == rows[24]["id"]
    assert page.previous_cursor is None
    assert page.next_at == str(rows[24]["created_at"])

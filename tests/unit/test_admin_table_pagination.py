"""The generic browser must never turn deep pages into large OFFSET scans."""

from uuid import uuid4

import pytest
from app.models import Base
from app.repositories.admin_read_repository import table_page_query
from sqlalchemy.dialects import postgresql


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_every_mapped_table_uses_bounded_primary_key_pagination(table_name: str) -> None:
    anchor = uuid4()
    forward = table_page_query(table_name, after=anchor)
    backward = table_page_query(table_name, before=anchor)
    for query in (forward, backward):
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert "OFFSET" not in sql
        assert "LIMIT" in sql
        assert "created_at" not in sql.split("ORDER BY")[-1]
    assert " < " in str(forward.compile(dialect=postgresql.dialect()))
    assert " > " in str(backward.compile(dialect=postgresql.dialect()))

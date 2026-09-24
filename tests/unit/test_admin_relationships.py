from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.repositories.admin_table_repository import relationship_query
from sqlalchemy.dialects import postgresql


def sql_for(table, field, **kwargs):
    return str(relationship_query(table, field, **kwargs).compile(dialect=postgresql.dialect()))


def test_courier_primary_key_relationship_excludes_existing_profiles():
    sql = sql_for("courier_profiles", "user_id")
    assert "NOT (EXISTS" in sql
    assert "courier_profiles.user_id = users.id" in sql
    assert "users.role =" in sql
    assert "LIMIT" in sql
    assert "token" not in sql and "encrypted" not in sql


def test_edit_excludes_other_owners_but_keeps_current_relationship():
    sql = sql_for("conversations", "order_id", record_id=uuid4())
    assert "conversations.id !=" in sql


def test_many_to_one_choices_remain_reusable():
    assert "EXISTS" not in sql_for("orders", "customer_id")


def test_partial_unique_index_limits_only_active_invoice_owners():
    sql = sql_for("invoices", "order_id")
    assert "status IN ('DRAFT','ISSUED','PAID')" in sql


def test_edit_keeps_current_choice_even_if_partial_unique_owner_is_another_row():
    sql = sql_for("invoices", "order_id", record_id=uuid4())
    assert "OR orders.id = (SELECT invoices.order_id" in sql


@pytest.mark.parametrize("table,field", [("users; DROP TABLE users", "id"), ("users", "phone")])
def test_unknown_relationships_rejected(table, field):
    with pytest.raises(NotFoundError):
        relationship_query(table, field)


def test_choices_use_keyset_pagination_and_bound_search():
    sql = sql_for("orders", "customer_id", search="A%_", after=uuid4())
    assert "users.id >" in sql
    assert "OFFSET" not in sql
    assert "ILIKE" in sql

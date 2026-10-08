"""Batched SQL retains locks, claim predicates and insert authorization."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.repositories.chat_repository import AttachmentInput, ChatRepository
from app.repositories.media_repository import MediaRepository
from sqlalchemy.dialects import postgresql


@pytest.mark.parametrize("count", [1, 5])
async def test_locked_grants_use_one_sorted_fresh_query(count):
    session = AsyncMock()
    session.scalars.return_value = []
    keys = [f"chat/conversation/{index}.jpg" for index in range(count)]
    assert await MediaRepository(session).get_many(keys, for_update=True) == []
    session.scalars.assert_awaited_once()
    statement = session.scalars.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ORDER BY media_uploads.storage_key FOR UPDATE" in sql
    assert statement.get_execution_options()["populate_existing"] is True


async def test_claim_is_one_conditional_update_and_reports_partial_matches():
    session = AsyncMock()
    session.scalars.return_value = ["first"]
    actor = uuid4()
    claimed = await MediaRepository(session).confirm_and_claim_many(
        ["first", "second"], actor, "CHAT_ATTACHMENT"
    )
    assert claimed == {"first"}
    compiled = session.scalars.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    for predicate in ("owner_user_id =", "purpose =", "attached_at IS NULL", "deleting_at IS NULL"):
        assert predicate in sql
    assert "RETURNING media_uploads.storage_key" in sql
    assert compiled.params["confirmed_at"] == compiled.params["attached_at"]
    assert actor in compiled.params.values()
    session.scalars.assert_awaited_once()


@pytest.mark.parametrize("count", [1, 5])
async def test_insert_is_single_parameterized_statement_scoped_to_sender_and_membership(count):
    session = AsyncMock()
    session.scalars.return_value = []
    actor, message = uuid4(), uuid4()
    inputs = [
        AttachmentInput(f"private/{index}.jpg", "image/jpeg", 12, index, None)
        for index in range(count)
    ]
    inputs[0] = AttachmentInput("private/0.jpg", "image/jpeg", 12, 0, Decimal("1.250"))
    assert (
        await ChatRepository(session).add_attachments(
            actor_id=actor, message_id=message, attachments=inputs
        )
        == []
    )
    compiled = session.scalars.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "INSERT INTO message_attachments" in sql
    assert "messages.sender_id =" in sql
    assert "conversations.customer_id =" in sql and "conversations.courier_id =" in sql
    assert "RETURNING message_attachments" in sql
    assert "private/" not in sql
    assert sql.count("UNION ALL") == count - 1
    assert message in compiled.params.values() and actor in compiled.params.values()
    assert all(item.storage_key in compiled.params.values() for item in inputs)
    session.scalars.assert_awaited_once()

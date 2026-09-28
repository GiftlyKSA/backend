"""Chat mutations read fresh conversation state under a database row lock."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.repositories.chat_repository import ChatRepository
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_mutation_lookup_locks_and_refreshes_conversation() -> None:
    session = AsyncMock()
    await ChatRepository(session).get_for_actor(uuid4(), uuid4(), for_update=True)
    query = session.scalar.await_args.args[0]
    assert "FOR UPDATE" in str(query.compile(dialect=postgresql.dialect()))
    assert query.get_execution_options().get("populate_existing") is True

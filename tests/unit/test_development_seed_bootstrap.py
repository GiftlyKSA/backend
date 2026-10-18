"""Development fixtures run once before the API, never from job processes."""

from unittest.mock import AsyncMock

import pytest
from app import entrypoint, seed_development


@pytest.mark.parametrize(
    "environment,command,expected",
    [
        ("development", ["gunicorn", "app.main:create_app()"], True),
        ("production", ["gunicorn", "app.main:create_app()"], False),
        ("test", ["gunicorn", "app.main:create_app()"], False),
        ("development", ["python", "-m", "app.workers.runtime", "--component", "worker"], False),
        ("development", ["python", "-m", "app.workers.runtime", "--component", "scheduler"], False),
    ],
)
def test_only_development_api_seeds_after_migrations(monkeypatch, environment, command, expected):
    events = []
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint", *command])
    monkeypatch.setattr(entrypoint, "upgrade_database", lambda: events.append("migrate"))

    async def seed():
        events.append("seed")
        return 0

    mock = AsyncMock(side_effect=seed)
    monkeypatch.setattr(seed_development, "seed_development_couriers", mock)
    monkeypatch.setattr(entrypoint.os, "execvp", lambda *args: events.append("exec"))
    entrypoint.main()
    assert events == (["migrate", "seed", "exec"] if expected else ["migrate", "exec"])
    assert mock.await_count == int(expected)

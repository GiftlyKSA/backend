"""The isolated worker has bounded concurrency and a minimal health surface."""

from unittest.mock import Mock

import pytest
from app.workers.runtime import health_app, worker_commands
from httpx import ASGITransport, AsyncClient


def test_worker_process_and_prefetch_are_bounded():
    worker, scheduler = worker_commands()
    assert worker[worker.index("--workers") + 1] == "1"
    assert worker[worker.index("--max-async-tasks") + 1] == "2"
    assert worker[worker.index("--max-prefetch") + 1] == "2"
    assert "app.workers.scheduler:scheduler" in scheduler


@pytest.mark.asyncio
async def test_worker_exposes_health_without_mobile_or_admin_routes():
    processes = [Mock(returncode=None), Mock(returncode=None)]
    app = health_app(processes)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://worker") as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/orders")).status_code == 404
        assert (await client.get("/docs")).status_code == 404
        processes[0].returncode = 1
        assert (await client.get("/api/health")).status_code == 503


@pytest.mark.asyncio
async def test_supervisor_owns_shutdown_and_reaps_children(monkeypatch):
    import asyncio
    import signal
    from unittest.mock import AsyncMock

    from app.workers import runtime

    handlers = {}
    monkeypatch.setattr(runtime.signal, "getsignal", lambda number: signal.SIG_DFL)
    monkeypatch.setattr(
        runtime.signal, "signal", lambda number, handler: handlers.__setitem__(number, handler)
    )

    class Process:
        returncode = None

        def __init__(self):
            self.stopped = asyncio.Event()
            self.terminated = False

        async def wait(self):
            await self.stopped.wait()
            self.returncode = 0
            return 0

        def terminate(self):
            self.terminated = True
            self.stopped.set()

    processes = [Process(), Process()]
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", AsyncMock(side_effect=processes))

    class Server:
        def __init__(self, config):
            self.stopped = asyncio.Event()

        @property
        def should_exit(self):
            return self.stopped.is_set()

        @should_exit.setter
        def should_exit(self, value):
            if value:
                self.stopped.set()

        async def serve(self):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            await self.stopped.wait()

    monkeypatch.setattr(runtime, "_ManagedServer", Server)
    assert await runtime.run() == 0
    assert all(process.terminated and process.returncode == 0 for process in processes)


@pytest.mark.asyncio
async def test_health_server_failure_is_not_a_success(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from app.workers import runtime

    monkeypatch.setattr(runtime.signal, "signal", lambda *args: None)

    class Process:
        returncode = None

        def __init__(self):
            self.stopped = asyncio.Event()

        async def wait(self):
            await self.stopped.wait()
            self.returncode = 0
            return 0

        def terminate(self):
            self.stopped.set()

    processes = [Process(), Process()]
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", AsyncMock(side_effect=processes))

    class Server:
        should_exit = False

        def __init__(self, config):
            pass

        async def serve(self):
            raise RuntimeError("Health server unavailable")

    monkeypatch.setattr(runtime, "_ManagedServer", Server)
    assert await runtime.run() == 1
    assert all(process.returncode == 0 for process in processes)

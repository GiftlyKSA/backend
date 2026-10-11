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
@pytest.mark.parametrize("component", ["combined", "worker", "scheduler"])
async def test_supervisor_owns_shutdown_and_reaps_children(monkeypatch, component):
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

    processes = [Process() for _ in worker_commands(component)]
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
    assert await runtime.run(component) == 0
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


@pytest.mark.parametrize("component,service", [("worker", "worker"), ("scheduler", "scheduler")])
def test_separate_component_never_starts_other_service(component, service):
    commands = worker_commands(component)
    assert len(commands) == 1
    assert commands[0][3] == service


@pytest.mark.asyncio
async def test_single_component_health_requires_one_running_child():
    process = Mock(returncode=None)
    async with AsyncClient(
        transport=ASGITransport(app=health_app([process], expected_processes=1)),
        base_url="http://worker",
    ) as client:
        assert (await client.get("/api/health")).status_code == 200
        process.returncode = 1
        assert (await client.get("/api/health")).status_code == 503


@pytest.mark.parametrize("component", ["worker", "scheduler"])
def test_explicit_runtime_command_survives_worker_role(monkeypatch, component):
    from app import entrypoint

    migrated = Mock()
    started = Mock()
    command = ["python", "-m", "app.workers.runtime", "--component", component]
    monkeypatch.setattr(entrypoint, "upgrade_database", migrated)
    monkeypatch.setattr(entrypoint, "get_settings", lambda: Mock(PROCESS_ROLE="worker"))
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint", *command])
    monkeypatch.setattr(entrypoint.os, "execvp", started)
    entrypoint.main()
    migrated.assert_called_once_with()
    started.assert_called_once_with(command[0], command)


def test_default_worker_role_still_starts_combined_runtime(monkeypatch):
    import sys

    from app import entrypoint

    monkeypatch.setattr(entrypoint, "upgrade_database", Mock())
    monkeypatch.setattr(entrypoint, "get_settings", lambda: Mock(PROCESS_ROLE="worker"))
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint"])
    started = Mock()
    monkeypatch.setattr(entrypoint.os, "execvp", started)
    entrypoint.main()
    started.assert_called_once_with(sys.executable, [sys.executable, "-m", "app.workers.runtime"])

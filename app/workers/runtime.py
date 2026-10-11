"""Run bounded job consumers in an app separate from the public API."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

_logger = logging.getLogger(__name__)


def worker_commands() -> tuple[list[str], list[str]]:
    """Use one consumer process, two tasks and two prefetched messages."""
    return (
        [
            sys.executable,
            "-m",
            "taskiq",
            "worker",
            "app.workers.broker:broker",
            "--workers",
            "1",
            "--max-async-tasks",
            "2",
            "--max-prefetch",
            "2",
            "--max-fails",
            "3",
            "--wait-tasks-timeout",
            "30",
        ],
        [sys.executable, "-m", "taskiq", "scheduler", "app.workers.scheduler:scheduler"],
    )


def health_app(processes: list[asyncio.subprocess.Process]) -> FastAPI:
    """Expose liveness only, with no business routes or diagnostic details."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        healthy = len(processes) == 2 and all(process.returncode is None for process in processes)
        return JSONResponse(
            {"status": "ok" if healthy else "unavailable"},
            status_code=200 if healthy else 503,
            headers={"Cache-Control": "no-store"},
        )

    return app


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=40)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class _ManagedServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The supervisor owns signals so Uvicorn cannot terminate it before child cleanup.
        yield


def _server_exit_status(serving: asyncio.Task[None]) -> int:
    try:
        serving.result()
    except Exception:
        _logger.exception("Worker health server failed; the worker app is stopping")
    else:
        _logger.error("Worker health server stopped unexpectedly")
    return 1


async def run() -> int:
    """Stop and reap every child on a signal, service failure or startup failure."""
    processes: list[asyncio.subprocess.Process] = []
    waits: list[asyncio.Task[None] | asyncio.Task[int] | asyncio.Task[bool]] = []
    server: uvicorn.Server | None = None
    serving: asyncio.Task[None] | None = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handlers = {
        number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)
    }

    def request_stop(number: int, frame: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for number in previous_handlers:
        signal.signal(number, request_stop)
    try:
        for command in worker_commands():
            processes.append(await asyncio.create_subprocess_exec(*command))
        server = _ManagedServer(
            uvicorn.Config(
                health_app(processes),
                host="0.0.0.0",  # noqa: S104 - dedicated PaaS liveness listener
                port=3000,
                access_log=False,
            )
        )
        serving = asyncio.create_task(server.serve())
        child_waits = [asyncio.create_task(process.wait()) for process in processes]
        stopping = asyncio.create_task(stop.wait())
        waits = [serving, stopping, *child_waits]
        done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        if stopping in done:
            return 0
        if serving in done:
            return _server_exit_status(serving)
        _logger.error("A background service exited; the worker app is stopping")
        return 1
    finally:
        if server is not None:
            server.should_exit = True
        await asyncio.gather(*(_stop(process) for process in processes))
        if serving is not None:
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(serving, timeout=5)
        for task in waits:
            task.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)


def main() -> None:
    """Start the isolated worker after the entrypoint migration gate."""
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()

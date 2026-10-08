from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.exceptions import BadRequestError
from PIL import Image


async def test_isolated_image_decoder_accepts_valid_bytes_and_rejects_disguised_markup():
    from app.services.chat_media_validation import verify_image_isolated

    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    await verify_image_isolated(buffer.getvalue(), "image/png")
    with pytest.raises(BadRequestError):
        await verify_image_isolated(b"<svg onload='alert(1)' />", "image/png")
    with pytest.raises(BadRequestError):
        await verify_image_isolated(buffer.getvalue(), "image/jpeg")


async def test_cancel_during_process_creation_waits_and_reaps_child(monkeypatch):
    import asyncio

    from app.services.chat_media_validation import _run

    spawned, finish_spawn = asyncio.Event(), asyncio.Event()
    process = SimpleNamespace(
        returncode=None, kill=Mock(), wait=AsyncMock(return_value=0), stdout=None, stderr=None
    )

    async def create(*args, **kwargs):
        spawned.set()
        await finish_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    task = asyncio.create_task(_run("trusted-decoder"))
    await spawned.wait()
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        finish_spawn.set()
        await asyncio.gather(task, return_exceptions=True)
    process.kill.assert_called_once()
    process.wait.assert_awaited()


async def test_oversized_native_decoder_output_is_reaped_without_stuck_admission(monkeypatch):
    import asyncio
    import sys

    from app.services.chat_media_validation import _run

    original = asyncio.create_subprocess_exec
    processes = []

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(_run(sys.executable, "-c", "print('x' * 2000000)"))
    try:
        done, _ = await asyncio.wait([task], timeout=3)
        assert task in done
        with pytest.raises(BadRequestError):
            await task
    finally:
        if not task.done():
            for process in processes:
                await process.communicate()
        await asyncio.gather(task, return_exceptions=True)

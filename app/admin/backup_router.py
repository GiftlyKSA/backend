"""Authenticated database snapshot downloads."""

import asyncio
import shutil
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from app.admin.deps import get_db, get_redis_from, get_settings_from, require_admin, verify_csrf
from app.core.exceptions import ConflictError
from app.core.locks import LockNotAcquiredError, redis_lock
from app.services.database_export_service import create_database_export

router = APIRouter()


class BackupResponse(FileResponse):
    """Remove the private snapshot even when the browser disconnects."""

    def __init__(
        self,
        path: Path,
        *,
        encrypted: bool,
        lease: AsyncExitStack | None = None,
        deadline: float | None = None,
    ) -> None:
        """Bind cleanup to the response rather than only successful downloads."""
        self._directory = path.parent
        self._lease = lease
        self._deadline = (
            deadline if deadline is not None else asyncio.get_running_loop().time() + 120
        )
        super().__init__(
            path,
            filename=path.name,
            media_type="application/octet-stream" if encrypted else "application/sql",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send bounded file chunks and always remove the snapshot afterward."""
        try:
            # Cleanup must follow actual streaming, not deferred server path-send.
            scope = {**scope, "extensions": {**scope.get("extensions", {})}}
            scope["extensions"].pop("http.response.pathsend", None)
            async with asyncio.timeout_at(self._deadline):
                await super().__call__(scope, receive, send)
        finally:
            try:
                shutil.rmtree(self._directory)
            finally:
                if self._lease is not None:
                    await self._lease.aclose()


@router.post("/database/export")
async def export_database(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    csrf_token: Annotated[str, Form(max_length=256)],
    mode: Annotated[Literal["plain", "encrypted"], Form()],
    password: Annotated[str, Form(max_length=1024)] = "",
) -> FileResponse:
    """Download a consistent snapshot after admin and CSRF checks."""
    ctx = await require_admin(request, db)
    settings = get_settings_from(request)
    verify_csrf(ctx, csrf_token, settings)
    await db.commit()
    path: Path | None = None
    lease = AsyncExitStack()
    try:
        async with redis_lock(get_redis_from(request), "lock:database_export", ttl_seconds=300):
            path = await create_database_export(settings, password if mode == "encrypted" else None)
            await lease.enter_async_context(
                redis_lock(
                    get_redis_from(request), "lock:database_export_download", ttl_seconds=180
                )
            )
            deadline = asyncio.get_running_loop().time() + 120
        return BackupResponse(path, encrypted=mode == "encrypted", lease=lease, deadline=deadline)
    except LockNotAcquiredError:
        if path is not None:
            shutil.rmtree(path.parent)
        await lease.aclose()
        raise ConflictError(
            "A database export is already running. Please try again later."
        ) from None
    except BaseException:
        try:
            if path is not None:
                shutil.rmtree(path.parent)
        finally:
            await lease.aclose()
        raise

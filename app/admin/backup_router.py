"""Authenticated database snapshot downloads."""

import shutil
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

    def __init__(self, path: Path, *, encrypted: bool) -> None:
        """Bind cleanup to the response rather than only successful downloads."""
        self._directory = path.parent
        super().__init__(
            path,
            filename=path.name,
            media_type="application/octet-stream" if encrypted else "application/sql",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send bounded file chunks and always remove the snapshot afterward."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            shutil.rmtree(self._directory)


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
    try:
        async with redis_lock(get_redis_from(request), "lock:database_export", ttl_seconds=300):
            path = await create_database_export(settings, password if mode == "encrypted" else None)
    except LockNotAcquiredError:
        raise ConflictError(
            "A database export is already running. Please try again later."
        ) from None
    except BaseException:
        if path is not None:
            shutil.rmtree(path.parent)
        raise
    return BackupResponse(path, encrypted=mode == "encrypted")

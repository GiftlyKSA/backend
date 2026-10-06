"""Database export preserves bytes and keeps credentials out of commands."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.admin import backup_router
from app.admin.deps import get_db
from app.core.exceptions import ValidationDomainError
from app.decrypt_backup import decrypt_backup
from app.services.database_export_service import (
    DatabaseExportUnavailableError,
    create_database_export,
    dump_environment,
    encrypt_export,
)
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings


def test_dump_environment_handles_escaped_credentials_without_changing_process_environment():
    original = dict(os.environ)
    env = dump_environment("postgresql+asyncpg://user:p%40ss@db:5432/giftly?ssl=require")
    assert env["PGPASSWORD"] == "p@ss"
    assert env["PGDATABASE"] == "giftly"
    assert env["PGSSLMODE"] == "require"
    assert dict(os.environ) == original


def test_encrypted_export_round_trips_and_rejects_wrong_password(tmp_path: Path):
    source = tmp_path / "backup.sql"
    source.write_bytes(b"CREATE TABLE example (id integer);\n" * 100)
    target = tmp_path / "backup.sql.enc"
    encrypt_export(source, target, "password123")
    payload = target.read_bytes()
    assert payload[:10] == b"GIFTLYSQL1"
    salt, nonce, tag = payload[10:26], payload[26:38], payload[-16:]
    key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(b"password123")
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(payload[:38])
    assert decryptor.update(payload[38:-16]) + decryptor.finalize() == source.read_bytes()
    wrong_key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(b"wrongpass")
    wrong = Cipher(algorithms.AES(wrong_key), modes.GCM(nonce, tag)).decryptor()
    wrong.authenticate_additional_data(payload[:38])
    wrong.update(payload[38:-16])
    with pytest.raises(InvalidTag):
        wrong.finalize()


def test_short_password_rejected_before_writing(tmp_path: Path):
    with pytest.raises(ValidationDomainError):
        encrypt_export(tmp_path / "missing.sql", tmp_path / "out", "short")
    assert not (tmp_path / "out").exists()


def test_decrypt_command_rejects_wrong_password_and_publishes_verified_sql(tmp_path):
    source = tmp_path / "original.sql"
    source.write_bytes(b"CREATE TABLE sample (id integer);\n")
    encrypted = tmp_path / "backup.enc"
    encrypt_export(source, encrypted, "password123")
    target = tmp_path / "restored.sql"
    with pytest.raises(InvalidTag):
        decrypt_backup(encrypted, target, "wrongpass")
    assert not target.exists()
    assert not target.with_name("restored.sql.partial").exists()
    decrypt_backup(encrypted, target, "password123")
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError):
        decrypt_backup(encrypted, target, "password123")


@pytest.mark.parametrize("exit_code", [0, 1])
async def test_dump_streams_snapshot_and_cleans_failed_exports(monkeypatch, tmp_path, exit_code):
    directory = tmp_path / "private"
    directory.mkdir()
    monkeypatch.setattr(
        "app.services.database_export_service.tempfile.mkdtemp", lambda **kw: str(directory)
    )
    process = SimpleNamespace(
        stdout=SimpleNamespace(read=AsyncMock(side_effect=[b"SQL snapshot", b""])),
        returncode=exit_code,
        wait=AsyncMock(return_value=exit_code),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(
        "app.services.database_export_service.asyncio.create_subprocess_exec", spawn
    )
    if exit_code:
        with pytest.raises(DatabaseExportUnavailableError):
            await create_database_export(make_test_settings(), None)
        assert not directory.exists()
    else:
        path = await create_database_export(make_test_settings(), None)
        assert path.read_bytes() == b"SQL snapshot"
    assert spawn.call_args.args == ("pg_dump", "--format=plain", "--create", "--no-password")
    assert "JWT_SECRET" not in spawn.call_args.kwargs["env"]


@pytest.mark.parametrize("csrf_valid", [True, False])
async def test_admin_export_checks_csrf_and_cleans_download(monkeypatch, tmp_path, csrf_valid):
    app = FastAPI()
    app.state.settings = make_test_settings()
    app.include_router(backup_router.router, prefix="/v1/admin/admin")
    db = SimpleNamespace(commit=AsyncMock())

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(backup_router, "require_admin", AsyncMock(return_value=SimpleNamespace()))

    def verify(*args):
        if not csrf_valid:
            raise HTTPException(status_code=403)

    monkeypatch.setattr(backup_router, "verify_csrf", verify)
    monkeypatch.setattr(backup_router, "get_redis_from", lambda request: None)

    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield "lock"

    monkeypatch.setattr(backup_router, "redis_lock", lock)
    directory = tmp_path / "private"
    directory.mkdir()
    path = directory / "giftly.sql"
    path.write_bytes(b"SQL backup")
    export = AsyncMock(return_value=path)
    monkeypatch.setattr(backup_router, "create_database_export", export)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/admin/database/export",
            data={"csrf_token": "test", "mode": "plain"},
        )
    assert response.status_code == (200 if csrf_valid else 403)
    assert export.await_count == int(csrf_valid)
    if csrf_valid:
        assert response.content == b"SQL backup"
        assert response.headers["cache-control"] == "no-store"
        assert not directory.exists()


async def test_backup_cleanup_runs_when_download_send_fails(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir()
    path = directory / "giftly.sql"
    path.write_bytes(b"SQL backup")
    response = backup_router.BackupResponse(path, encrypted=False)

    async def send(message):
        raise OSError("Browser disconnected")

    with pytest.raises(OSError):
        await response({"type": "http", "method": "GET", "headers": []}, AsyncMock(), send)
    assert not directory.exists()


async def test_export_never_runs_when_admin_authorization_fails(monkeypatch):
    app = FastAPI()
    app.include_router(backup_router.router, prefix="/v1/admin/admin")

    async def database():
        yield SimpleNamespace(commit=AsyncMock())

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(
        backup_router,
        "require_admin",
        AsyncMock(side_effect=HTTPException(status_code=403)),
    )
    export = AsyncMock()
    monkeypatch.setattr(backup_router, "create_database_export", export)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/admin/database/export",
            data={"csrf_token": "test", "mode": "plain"},
        )
    assert response.status_code == 403
    export.assert_not_awaited()


async def test_size_limit_kills_dump_and_removes_partial_file(monkeypatch, tmp_path):
    directory = tmp_path / "private"
    directory.mkdir()
    monkeypatch.setattr(
        "app.services.database_export_service.tempfile.mkdtemp", lambda **kw: str(directory)
    )
    monkeypatch.setattr("app.services.database_export_service._MAX_BYTES", 2)
    process = SimpleNamespace(
        stdout=SimpleNamespace(read=AsyncMock(return_value=b"too big")),
        returncode=None,
        kill=lambda: None,
        communicate=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.database_export_service.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    with pytest.raises(DatabaseExportUnavailableError, match="512 MiB"):
        await create_database_export(make_test_settings(), None)
    process.communicate.assert_awaited_once()
    assert not directory.exists()

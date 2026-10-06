"""Bounded PostgreSQL snapshots and optional authenticated file encryption."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.exceptions import DomainError, ValidationDomainError

logger = logging.getLogger(__name__)
_MAX_BYTES = 512 * 1024 * 1024
_CHUNK_BYTES = 65536


async def _write_chunk(output: BinaryIO, chunk: bytes) -> None:
    """Finish each offloaded write before cancellation can close its file."""
    write = asyncio.create_task(asyncio.to_thread(output.write, chunk))
    try:
        await asyncio.shield(write)
    except asyncio.CancelledError:
        await write
        raise


class DatabaseExportUnavailableError(DomainError):
    """The database snapshot could not be completed safely."""

    code = "DATABASE_EXPORT_UNAVAILABLE"
    status_code = 503
    message = "Database export failed. Check the database connection and backup client."


def dump_environment(database_url: str) -> dict[str, str]:
    """Pass connection credentials privately without shell or command arguments."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or not url.host or not url.database:
        raise DatabaseExportUnavailableError()
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TMPDIR", "TEMP"}
    }
    env.update(
        PGHOST=url.host,
        PGPORT=str(url.port or 5432),
        PGDATABASE=url.database,
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
        PGCONNECT_TIMEOUT="10",
        PGOPTIONS="-c statement_timeout=55000 -c lock_timeout=10000",
    )
    ssl = url.query.get("sslmode", url.query.get("ssl"))
    if ssl is not None:
        if ssl not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise DatabaseExportUnavailableError()
        env["PGSSLMODE"] = str(ssl)
    return env


def encrypt_export(source: Path, target: Path, password: str) -> None:
    """Encrypt a SQL file in bounded chunks using AES-256-GCM and scrypt."""
    if not 8 <= len(password) <= 1024:
        raise ValidationDomainError("The export password must contain 8 to 1024 characters.")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = b"GIFTLYSQL1" + salt + nonce
    key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode("utf-8"))
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        os.chmod(target, 0o600)
        outgoing.write(header)
        while chunk := incoming.read(_CHUNK_BYTES):
            outgoing.write(encryptor.update(chunk))
        outgoing.write(encryptor.finalize())
        outgoing.write(encryptor.tag)


async def create_database_export(settings: Settings, password: str | None) -> Path:
    """Return a private temporary snapshot; the caller owns download cleanup."""
    if password is not None and not 8 <= len(password) <= 1024:
        raise ValidationDomainError("The export password must contain 8 to 1024 characters.")
    directory = Path(tempfile.mkdtemp(prefix="giftly-backup-"))
    source = directory / "giftly.sql"
    process: asyncio.subprocess.Process | None = None
    try:
        with source.open("xb") as output:
            os.chmod(source, 0o600)
            process = await asyncio.create_subprocess_exec(
                "pg_dump",
                "--format=plain",
                "--create",
                "--no-password",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=dump_environment(settings.DATABASE_URL.get_secret_value()),
            )
            assert process.stdout is not None
            async with asyncio.timeout(60):
                size = 0
                while chunk := await process.stdout.read(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        raise DatabaseExportUnavailableError("Database export exceeds 512 MiB.")
                    await _write_chunk(output, chunk)
                if await process.wait() != 0:
                    logger.error("Database export failed: pg_dump exit code %s", process.returncode)
                    raise DatabaseExportUnavailableError()
        if password is None:
            return source
        target = directory / "giftly.sql.enc"
        encryption = asyncio.create_task(
            asyncio.to_thread(encrypt_export, source, target, password)
        )
        try:
            await asyncio.shield(encryption)
        except asyncio.CancelledError:
            await encryption
            raise
        source.unlink()
        return target
    except BaseException as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        shutil.rmtree(directory)
        if isinstance(exc, (OSError, TimeoutError)):
            logger.error("Database export failed: %s", type(exc).__name__)
            raise DatabaseExportUnavailableError() from None
        raise

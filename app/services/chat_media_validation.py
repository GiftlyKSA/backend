"""Bounded local decoding of private chat recordings; never accept remote inputs."""

import asyncio
import json
import math
import os
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.core.exceptions import BadRequestError, MediaValidationUnavailableError

MEDIA_TYPES = {
    "IMAGE": {"image/jpeg": "jpg", "image/png": "png"},
    "VIDEO": {"video/mp4": "mp4", "video/webm": "webm"},
    "VOICE": {
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/webm": "webm",
        "audio/wav": "wav",
        "audio/aac": "aac",
    },
}
_DEMUXERS = "mov,matroska,webm,ogg,mp3,wav,aac"


def media_policy(settings: Settings, kind: str, mime: str, size: int) -> tuple[str, int]:
    """Apply trusted MIME, byte and duration limits before issuing an S3 grant."""
    extension = MEDIA_TYPES.get(kind, {}).get(mime)
    if extension is None:
        raise BadRequestError("Unsupported chat media type.")
    byte_limits = {
        "IMAGE": settings.CHAT_IMAGE_MAX_UPLOAD_BYTES,
        "VIDEO": settings.CHAT_VIDEO_MAX_UPLOAD_BYTES,
        "VOICE": settings.CHAT_AUDIO_MAX_UPLOAD_BYTES,
    }
    if not 0 < size <= byte_limits[kind]:
        raise BadRequestError("Chat media exceeds its size limit.")
    duration = (
        settings.CHAT_VIDEO_MAX_DURATION_SECONDS
        if kind == "VIDEO"
        else settings.CHAT_AUDIO_MAX_DURATION_SECONDS
    )
    return extension, duration


def verify_image(body: bytes, mime: str) -> None:
    """Reject malformed images and decompression bombs without interpreting markup."""
    try:
        with Image.open(BytesIO(body)) as image:
            expected = "JPEG" if mime == "image/jpeg" else "PNG"
            if image.format != expected or image.width * image.height > 20_000_000:
                raise BadRequestError("Invalid image type or dimensions.")
            image.verify()
        with Image.open(BytesIO(body)) as image:
            image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ) as exc:
        raise BadRequestError("The image is malformed.") from exc


async def _read_output(stream: asyncio.StreamReader | None) -> bytes:
    assert stream is not None
    output = bytearray()
    while chunk := await stream.read(8192):
        output.extend(chunk)
        if len(output) > 65536:
            raise BadRequestError("Media metadata is too large.")
    return bytes(output)


async def _run(*args: str) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={key: os.environ[key] for key in ("PATH", "SystemRoot") if key in os.environ},
        )
    except (OSError, NotImplementedError) as exc:
        raise MediaValidationUnavailableError() from exc
    tasks = [
        asyncio.create_task(_read_output(process.stdout)),
        asyncio.create_task(_read_output(process.stderr)),
    ]
    waiter = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(20):
            output, errors = await asyncio.gather(*tasks)
            code = await waiter
        if code != 0 or errors:
            raise BadRequestError("Media could not be decoded safely.")
        return output
    except TimeoutError as exc:
        raise BadRequestError("Media validation took too long.") from exc
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await asyncio.shield(process.wait())
        for task in tasks:
            task.cancel()
        waiter.cancel()
        await asyncio.gather(*tasks, waiter, return_exceptions=True)


def probe_duration(output: bytes, kind: str, mime: str, maximum: int) -> float | None:
    """Check actual container and streams rather than caller-supplied metadata."""
    try:
        probe = json.loads(output)
        streams = probe["streams"]
        formats = set(probe["format"]["format_name"].split(","))
        declared_duration = probe["format"].get("duration")
        duration = float(declared_duration) if declared_duration is not None else None
        expected = {
            "video/mp4": "mov",
            "video/webm": "webm",
            "audio/mp4": "mov",
            "audio/mpeg": "mp3",
            "audio/ogg": "ogg",
            "audio/webm": "webm",
            "audio/wav": "wav",
            "audio/aac": "aac",
        }[mime]
        types = [stream["codec_type"] for stream in streams]
        if expected not in formats or not 1 <= len(streams) <= 2:
            raise ValueError("Invalid container")
        if kind == "VOICE" and types != ["audio"]:
            raise ValueError("Voice notes must contain audio only")
        if kind == "VIDEO" and (
            types.count("video") != 1 or any(t not in {"audio", "video"} for t in types)
        ):
            raise ValueError("Invalid video streams")
        for stream in streams:
            if stream["codec_type"] == "video" and (
                not 1 <= int(stream["width"]) <= 1920
                or not 1 <= int(stream["height"]) <= 1920
                or int(stream["width"]) * int(stream["height"]) > 2_073_600
            ):
                raise ValueError("Video must fit 1080p")
        if duration is not None and (not math.isfinite(duration) or not 0 < duration <= maximum):
            raise ValueError("Invalid duration")
        return duration
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise BadRequestError("Invalid media format, streams, dimensions or duration.") from exc


async def verify_recording(body: bytes, *, kind: str, mime: str, maximum: int) -> float:
    """Probe and decode a bounded private file with network protocols disabled."""
    with TemporaryDirectory(prefix="giftly-media-") as directory:
        path = Path(directory) / "recording"
        await asyncio.to_thread(path.write_bytes, body)
        output = await _run(
            "ffprobe",
            "-max_alloc",
            "67108864",
            "-v",
            "fatal",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            _DEMUXERS,
            "-show_entries",
            "stream=codec_type,width,height:format=duration,format_name",
            "-of",
            "json",
            str(path),
        )
        duration = probe_duration(output, kind, mime, maximum)
        progress = await _run(
            "ffmpeg",
            "-max_alloc",
            "67108864",
            "-nostdin",
            "-v",
            "fatal",
            "-xerror",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            _DEMUXERS,
            "-i",
            str(path),
            "-t",
            str(maximum + 1),
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-threads",
            "1",
            "-f",
            "null",
            "-progress",
            "pipe:1",
            "-nostats",
            "-",
        )
        try:
            times = [
                int(line.split(b"=", 1)[1]) / 1_000_000
                for line in progress.splitlines()
                if line.startswith(b"out_time_us=") and line != b"out_time_us=N/A"
            ]
        except ValueError as exc:
            raise BadRequestError("Invalid decoded recording duration.") from exc
        if not times or not 0 < max(times) <= maximum:
            raise BadRequestError("The recording exceeds the duration limit.")
        return max(max(times), duration or 0)

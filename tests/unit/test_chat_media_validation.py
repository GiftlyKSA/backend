"""Chat media policy rejects unsupported and oversized recordings."""

import json
import shutil
import wave
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from app.core.exceptions import BadRequestError, MediaValidationUnavailableError
from app.services.chat_media_validation import (
    media_policy,
    probe_duration,
    verify_image,
    verify_recording,
)
from PIL import Image

from tests.conftest import make_test_settings


@pytest.mark.parametrize(
    "kind,mime,size",
    [
        ("IMAGE", "image/svg+xml", 100),
        ("IMAGE", "image/jpeg", 10485761),
        ("VIDEO", "video/mp4", 125829121),
        ("VOICE", "audio/mpeg", 10485761),
        ("VOICE", "video/mp4", 100),
    ],
)
def test_rejects_bad_chat_media(kind, mime, size):
    with pytest.raises(BadRequestError):
        media_policy(make_test_settings(), kind, mime, size)


def test_video_limit_accepts_exactly_120_mib():
    extension, limit = media_policy(make_test_settings(), "VIDEO", "video/mp4", 125829120)
    assert extension == "mp4" and limit == 120


@pytest.mark.parametrize("duration", ["121", "nan", "inf", "0", "-1", "invalid"])
def test_rejects_invalid_actual_duration(duration):
    output = json.dumps(
        {
            "streams": [{"codec_type": "audio"}],
            "format": {"format_name": "ogg", "duration": duration},
        }
    ).encode()
    with pytest.raises(BadRequestError):
        probe_duration(output, "VOICE", "audio/ogg", 120)


def test_voice_note_rejects_embedded_video():
    output = json.dumps(
        {
            "streams": [{"codec_type": "video", "width": 320, "height": 240}],
            "format": {"format_name": "mov", "duration": "10"},
        }
    ).encode()
    with pytest.raises(BadRequestError):
        probe_duration(output, "VOICE", "audio/mp4", 120)


@pytest.mark.parametrize(
    "output",
    [
        b"{}",
        b"null",
        b"[]",
        b"not json",
        b'{"streams": [], "format": {"format_name": 10, "duration": "1"}}',
    ],
)
def test_malformed_probe_returns_stable_client_error(output):
    with pytest.raises(BadRequestError):
        probe_duration(output, "VOICE", "audio/ogg", 120)


def test_real_image_is_decoded_and_disguised_markup_rejected():
    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    verify_image(buffer.getvalue(), "image/png")
    with pytest.raises(BadRequestError):
        verify_image(b"<svg onload='alert(1)' />", "image/png")
    with pytest.raises(BadRequestError):
        verify_image(buffer.getvalue(), "image/jpeg")


def test_corrupt_png_checksum_returns_a_client_error():
    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    body = bytearray(buffer.getvalue())
    position = body.index(b"IDAT")
    length = int.from_bytes(body[position - 4 : position], "big")
    body[position + 4 + length] ^= 1
    with pytest.raises(BadRequestError):
        verify_image(bytes(body), "image/png")


@pytest.mark.asyncio
async def test_decoder_rejects_recording_longer_than_probe_claims():
    probe = json.dumps(
        {"streams": [{"codec_type": "audio"}], "format": {"format_name": "ogg", "duration": "10"}}
    ).encode()
    with patch(
        "app.services.chat_media_validation._run",
        new=AsyncMock(
            side_effect=[probe, b"out_time_us=121000000\nprogress=end\n"],
        ),
    ):
        with pytest.raises(BadRequestError):
            await verify_recording(b"recording", kind="VOICE", mime="audio/ogg", maximum=120)


@pytest.mark.asyncio
async def test_recorded_webm_without_container_duration_uses_decoded_duration():
    probe = json.dumps(
        {"streams": [{"codec_type": "audio"}], "format": {"format_name": "matroska,webm"}}
    ).encode()
    with patch(
        "app.services.chat_media_validation._run",
        new=AsyncMock(
            side_effect=[probe, b"out_time_us=1000000\nprogress=end\n"],
        ),
    ):
        assert (
            await verify_recording(b"recording", kind="VOICE", mime="audio/webm", maximum=120) == 1
        )


@pytest.mark.asyncio
async def test_missing_decoder_fails_closed():
    from app.services.chat_media_validation import _run

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=FileNotFoundError)):
        with pytest.raises(MediaValidationUnavailableError):
            await _run("ffprobe")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Native media decoder not installed",
)
async def test_real_decoder_checks_waveform_duration():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(b"\x00\x00" * 8000)
    duration = await verify_recording(
        buffer.getvalue(), kind="VOICE", mime="audio/wav", maximum=120
    )
    assert 0.9 <= duration <= 1.1

"""Original device formats must be decoded, not relabelled."""

from io import BytesIO

import pytest
from app.core.exceptions import BadRequestError
from app.services.chat_media_validation import media_policy, verify_image, verify_image_isolated
from PIL import Image
from pillow_heif import register_heif_opener

from tests.conftest import make_test_settings


@pytest.mark.parametrize(
    ("kind", "mime", "extension"),
    [
        ("IMAGE", "image/heic", "heic"),
        ("IMAGE", "image/heif", "heif"),
        ("VIDEO", "video/quicktime", "mov"),
    ],
)
def test_original_formats_allowed(kind, mime, extension):
    assert media_policy(make_test_settings(), kind, mime, 100)[0] == extension


@pytest.mark.parametrize("mime", ["image/heic", "image/heif"])
async def test_real_heif_decode_and_mime_spoof(mime):
    register_heif_opener(thumbnails=False)
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "purple").save(buffer, format="HEIF")
    body = buffer.getvalue()
    verify_image(body, mime)
    await verify_image_isolated(body, mime)
    with pytest.raises(BadRequestError):
        verify_image(body, "image/jpeg")
    with pytest.raises(BadRequestError):
        verify_image(b"<script>run()</script>", mime)


async def test_real_mov_decode_and_relabel_rejected(tmp_path):
    import asyncio
    import shutil

    from app.services.chat_media_validation import verify_recording_file

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Native decoder unavailable")
    path = tmp_path / "original.mov"
    process = await asyncio.create_subprocess_exec(
        *[
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=purple:s=64x64:d=1",
            "-c:v",
            "mpeg4",
            "-y",
            str(path),
        ],
    )
    assert await process.wait() == 0
    duration = await verify_recording_file(path, kind="VIDEO", mime="video/quicktime", maximum=120)
    assert 0.9 <= duration <= 1.1
    with pytest.raises(BadRequestError):
        await verify_recording_file(path, kind="VIDEO", mime="video/mp4", maximum=120)
    with pytest.raises(BadRequestError):
        await verify_recording_file(path, kind="VIDEO", mime="video/quicktime", maximum=0.5)

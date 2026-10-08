"""Decode private image bytes in a process that its caller can terminate."""

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def decode_image(body: bytes, mime: str) -> None:
    """Require the declared raster format and bounded dimensions, then fully decode."""
    try:
        with Image.open(BytesIO(body)) as image:
            expected = "JPEG" if mime == "image/jpeg" else "PNG"
            if image.format != expected or image.width * image.height > 20_000_000:
                raise ValueError("Invalid image type or dimensions.")
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
        raise ValueError("The image is malformed.") from exc


if __name__ == "__main__":
    try:
        decode_image(Path(sys.argv[1]).read_bytes(), sys.argv[2])
    except ValueError:
        sys.exit(1)

"""Decode private image bytes in a process that its caller can terminate."""

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def decode_image(body: bytes, mime: str) -> None:
    """Require the declared raster format and bounded dimensions, then fully decode."""
    try:
        expected = {
            "image/jpeg": "JPEG",
            "image/png": "PNG",
            "image/heic": "HEIF",
            "image/heif": "HEIF",
        }.get(mime)
        if expected is None:
            raise ValueError("Unsupported image type.")
        if expected == "HEIF":
            from pillow_heif import register_heif_opener

            register_heif_opener(
                thumbnails=False, depth_images=False, aux_images=False, decode_threads=1
            )
        with Image.open(BytesIO(body)) as image:
            if image.format != expected or image.width * image.height > 20_000_000:
                raise ValueError("Invalid image type or dimensions.")
            if expected == "HEIF" and getattr(image, "n_frames", 1) != 1:
                raise ValueError("Only single-image HEIF files are supported.")
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

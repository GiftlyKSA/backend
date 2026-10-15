"""Real image bytes for fake-storage upload and attachment integration checks."""

from io import BytesIO

from app.integrations.storage.fake import FakeStorageClient
from PIL import Image


async def record_image(storage: FakeStorageClient, storage_key: str) -> None:
    """Populate a fake upload with decodable bytes matching its issued metadata."""
    head = await storage.head_object(storage_key)
    assert head is not None
    formats = {"image/jpeg": "JPEG", "image/png": "PNG"}
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format=formats[head.content_type])
    body = output.getvalue()
    assert len(body) <= head.byte_size, "Use a realistic image byte size in this fixture."
    storage.recorded_bytes[storage_key] = body + b"\0" * (head.byte_size - len(body))

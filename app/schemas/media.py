"""Pydantic contracts for the media endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UploadUrlRequest(BaseModel):
    """Request a pre-signed upload URL for a media object."""

    model_config = ConfigDict(extra="forbid")
    purpose: Literal["ORDER_REQUEST", "DELIVERY_PROOF", "COURIER_AVATAR"] = Field(
        ..., description="What the media is for; determines the key prefix."
    )
    content_type: Literal[
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/heif",
        "video/mp4",
        "video/webm",
        "video/quicktime",
    ] = Field(
        ...,
        description=(
            "JPEG, PNG, HEIC or HEIF images for all purposes; MP4, WebM or QuickTime video "
            "only for DELIVERY_PROOF. The value is pinned into the pre-signed URL."
        ),
    )
    byte_size: int = Field(
        ...,
        gt=0,
        description=(
            "Exact object size in bytes. Images use MAX_UPLOAD_BYTES (10 MiB default); "
            "delivery-proof videos use CHAT_VIDEO_MAX_UPLOAD_BYTES (120 MiB maximum/default)."
        ),
    )

    @model_validator(mode="after")
    def validate_video_purpose(self) -> UploadUrlRequest:
        """Keep non-proof media image-only at the request boundary."""
        if self.content_type.startswith("video/") and self.purpose != "DELIVERY_PROOF":
            raise ValueError("Video uploads are only supported for delivery proof.")
        return self


class UploadUrlResponse(BaseModel):
    """A pre-signed upload URL and the server-generated key."""

    upload_url: str = Field(
        ...,
        description=(
            "PUT bytes directly to this URL with the issued Content-Type and Content-Length, "
            "plus If-None-Match: *. An existing object rejects a second PUT."
        ),
    )
    storage_key: str = Field(..., description="Confirm this key after uploading.")
    expires_in: int = Field(..., description="Seconds the URL is valid.")


class ConfirmRequest(BaseModel):
    """Confirm that an object was uploaded to a key."""

    model_config = ConfigDict(extra="forbid")
    storage_key: str = Field(..., description="The key returned by upload-urls.")


class ConfirmResponse(BaseModel):
    """Confirmation that an upload passed validation."""

    storage_key: str = Field(..., description="The confirmed key.")
    confirmed: bool = Field(..., description="True when the object is valid.")

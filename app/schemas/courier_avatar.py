"""Private courier avatar API contracts."""

from pydantic import BaseModel, ConfigDict, Field


class AvatarRequest(BaseModel):
    """Reference an owned confirmed avatar upload."""

    model_config = ConfigDict(extra="forbid")
    storage_key: str = Field(min_length=1, max_length=512)


class AvatarResponse(BaseModel):
    """Short-lived private read reference."""

    avatar_url: str | None
    expires_in: int

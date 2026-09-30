"""Public city-choice response schema."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class CityResponse(BaseModel):
    """One currently active Saudi city choice."""

    id: uuid.UUID
    name: str
    name_ar: str
    shortcut: str

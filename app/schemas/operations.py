"""Retained write-result contract; payment recovery is not payment verification."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, JsonValue

OperationName = Literal["order.create", "occasion.create", "occasion.update", "wallet.topup"]


class OperationResponse(BaseModel):
    """An owned committed snapshot or an explicitly unresolved operation."""

    operation_key: UUID
    operation: OperationName
    status: Literal["COMPLETED", "OUTCOME_UNKNOWN"]
    resource_id: UUID | None
    result: dict[str, JsonValue] | None
    created_at: datetime
    expires_at: datetime

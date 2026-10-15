"""Small category-scoped catalogs; never cache activity rows or actor identities."""

import asyncio
import logging
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.repositories.audit_repository import AuditFilterChoices, AuditRepository

logger = logging.getLogger(__name__)
_CATEGORIES = {
    ("SYSTEM",): "system",
    ("ADMIN",): "admin",
    ("USER", "CUSTOMER", "COURIER"): "users",
}


class _Choices(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actions: list[Annotated[str, StringConstraints(max_length=100)]] = Field(max_length=200)
    entities: list[Annotated[str, StringConstraints(max_length=50)]] = Field(max_length=200)


async def audit_filter_choices(
    redis: Redis, repository: AuditRepository, categories: tuple[str, ...]
) -> AuditFilterChoices:
    """Reuse at most three 30-second label catalogs, with bounded cache I/O."""
    category = _CATEGORIES.get(categories)
    if category is None:
        return await repository.filter_choices(categories)
    key = f"cache:admin-audit-choices:v1:{category}"
    try:
        async with asyncio.timeout(0.1):
            value = await redis.get(key)
        if isinstance(value, str) and len(value) <= 262144:
            choices = _Choices.model_validate_json(value)
            return AuditFilterChoices(choices.actions, choices.entities)
    except (ValidationError, UnicodeDecodeError):
        pass
    except (RedisError, TimeoutError):
        logger.warning("Audit dropdown cache read unavailable.")
    result = await repository.filter_choices(categories)
    choices = _Choices(actions=result.actions, entities=result.entities)
    try:
        async with asyncio.timeout(0.1):
            await redis.set(key, choices.model_dump_json(), ex=30)
    except (RedisError, TimeoutError):
        logger.warning("Audit dropdown cache write unavailable.")
    return result

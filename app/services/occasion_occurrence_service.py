"""Owned virtual calendar occurrences with bounded Gregorian ranges."""

import base64
import binascii
from datetime import date
from uuid import UUID

from app.core.exceptions import BadRequestError, ValidationDomainError
from app.repositories.occasion_occurrence_repository import OccasionOccurrenceRepository
from app.schemas.occasions import OccasionOccurrencePage


class OccasionOccurrenceService:
    """Preserve saved-list semantics while exposing actual calendar dates."""

    def __init__(self, repository: OccasionOccurrenceRepository) -> None:
        """Bind owned calendar persistence."""
        self._repository = repository

    async def list(
        self, owner: UUID, *, start: date, end: date, limit: int, cursor: str | None
    ) -> OccasionOccurrencePage:
        """Validate bounds and opaque anchors before SQL expansion."""
        if start > end or (end - start).days >= 366:
            raise ValidationDomainError("Occurrence range must be between 1 and 366 days.")
        after = None
        if cursor is not None:
            try:
                decoded = base64.b64decode(
                    cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
                )
                day, identifier = decoded.decode("ascii").split("|")
                after = (date.fromisoformat(day), UUID(identifier))
            except (ValueError, UnicodeError, binascii.Error) as exc:
                raise BadRequestError("Invalid occurrence cursor.") from exc
        rows = await self._repository.page(
            owner, start=start, end=end, limit=limit + 1, after=after
        )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = (
                base64.urlsafe_b64encode(f"{last.occurrence_date}|{last.occasion_id}".encode())
                .decode()
                .rstrip("=")
            )
        return OccasionOccurrencePage(items=rows[:limit], next_cursor=next_cursor)

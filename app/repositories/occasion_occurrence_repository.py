"""Expand only an owned, bounded date range in PostgreSQL."""

from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.schemas.occasions import OccasionOccurrence

_SCOPE = """
WITH owned AS (
 SELECT id,title,occasion_date,recurrence,reminder_enabled,reminder_days_before
 FROM occasions WHERE user_id=CAST(:owner AS uuid)
), dates AS (
 SELECT o.*,o.occasion_date AS occurrence_date FROM owned o WHERE recurrence='NONE'
 UNION ALL
 SELECT o.*,CASE WHEN extract(month FROM occasion_date)=2 AND extract(day FROM occasion_date)=29
 AND NOT (y%4=0 AND (y%100<>0 OR y%400=0)) THEN NULL
 ELSE make_date(y,extract(month FROM occasion_date)::integer,
 extract(day FROM occasion_date)::integer)
 END AS occurrence_date
 FROM owned o CROSS JOIN
 generate_series(CAST(:start_year AS integer),CAST(:end_year AS integer)) AS y
 WHERE recurrence='ANNUAL'
), occurrences AS (
 SELECT * FROM dates WHERE occurrence_date>=occasion_date
 AND occurrence_date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
)
"""


class OccasionOccurrenceRepository:
    """Keep calendar expansion, ownership and pagination before the SQL limit."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the owned transaction."""
        self._session = session

    async def page(
        self,
        owner: UUID,
        *,
        start: date,
        end: date,
        limit: int,
        after: tuple[date, UUID] | None,
    ) -> list[OccasionOccurrence]:
        """At most two years are expanded; invalid/foreign anchors stay protected."""
        params: dict[str, object] = {
            "owner": owner,
            "start": start,
            "end": end,
            "start_year": start.year,
            "end_year": end.year,
            "limit": limit,
            "after_date": after[0] if after else None,
            "after_id": after[1] if after else None,
        }
        if after:
            anchor = await self._session.scalar(
                text(
                    _SCOPE
                    + """
SELECT id FROM occurrences WHERE occurrence_date=CAST(:after_date AS date)
AND id=CAST(:after_id AS uuid) LIMIT 1
"""
                ),
                params,
            )
            if anchor is None:
                raise NotFoundError("Occurrence cursor not found in this range.")
        rows = await self._session.execute(
            text(
                _SCOPE
                + """
SELECT id AS occasion_id,title,occurrence_date,recurrence,reminder_enabled,reminder_days_before
FROM occurrences WHERE CAST(:after_date AS date) IS NULL
OR (occurrence_date,id)>(CAST(:after_date AS date),CAST(:after_id AS uuid))
ORDER BY occurrence_date,id LIMIT :limit
"""
            ),
            params,
        )
        return [OccasionOccurrence.model_validate(row) for row in rows.mappings()]

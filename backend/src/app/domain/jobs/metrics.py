"""Pure aggregation helpers for the dashboard: ranges, buckets and percentiles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.domain.errors import FieldError, ValidationFailed

BUCKETS = ("day", "week", "month")
DEFAULT_WINDOW = timedelta(days=90)
MAX_WINDOW = timedelta(days=3 * 366)


@dataclass(frozen=True)
class DateRange:
    """`[start, end)` in UTC dates, plus the bucket for series."""

    start: date
    end: date
    bucket: str

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=UTC)

    @property
    def end_at(self) -> datetime:
        return datetime.combine(self.end, time.min, tzinfo=UTC)

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.start_at <= moment < self.end_at


def resolve_range(start: date | None, end: date | None, bucket: str, today: date) -> DateRange:
    resolved_end = end or today + timedelta(days=1)
    resolved_start = start or resolved_end - DEFAULT_WINDOW
    errors: list[FieldError] = []
    if bucket not in BUCKETS:
        errors.append(FieldError("/bucket", "Use day, week or month."))
    if resolved_start >= resolved_end:
        errors.append(FieldError("/from", "Must be before `to`."))
    elif resolved_end - resolved_start > MAX_WINDOW:
        errors.append(FieldError("/from", "The range can be at most three years."))
    if errors:
        raise ValidationFailed("Check the date range.", errors=errors)
    return DateRange(resolved_start, resolved_end, bucket)


def bucket_start(moment: datetime, bucket: str) -> date:
    day = moment.astimezone(UTC).date()
    if bucket == "week":
        return day - timedelta(days=day.weekday())
    if bucket == "month":
        return day.replace(day=1)
    return day


def percentile(samples: Sequence[float], fraction: float) -> float | None:
    """Linear interpolation, matching Postgres `percentile_cont`."""
    ordered = sorted(samples)
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def rounded(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)

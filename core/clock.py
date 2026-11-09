"""Time helpers. All business-day arithmetic is in calendar days, America/New_York."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

NY = ZoneInfo("America/New_York")


def now() -> datetime:
    return timezone.now()


def today() -> date:
    return now().astimezone(NY).date()


def local_date(dt: datetime | None) -> date | None:
    if dt is None:
        return None
    return dt.astimezone(NY).date()


def days_between(earlier: date | datetime | None, later: date | datetime | None) -> int | None:
    """Calendar days from `earlier` to `later` (positive when later is after earlier)."""
    if earlier is None or later is None:
        return None
    if isinstance(earlier, datetime):
        earlier = local_date(earlier)
    if isinstance(later, datetime):
        later = local_date(later)
    return (later - earlier).days


def days_since(dt: date | datetime | None) -> int | None:
    return days_between(dt, today())


def days_until(d: date | None) -> int | None:
    return days_between(today(), d)


def end_of_local_day(d: date) -> datetime:
    """Last instant of a NY calendar day, as an aware datetime (for as-of date pickers)."""
    return datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=NY) - timedelta(
        microseconds=1
    )

from datetime import date, datetime, timedelta
from datetime import time as dtime


def parse_iso_date(value: str) -> date | None:
    """Convert YYYY-MM-DD to date; ignore invalid."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def period_bounds(period: str, today: date | None = None):
    """Return (start_date, end_date) for preset periods."""
    today = today or datetime.utcnow().date()
    if period == "day":
        return today, today
    if period == "week":
        return today - timedelta(days=6), today
    if period == "month":
        return today - timedelta(days=29), today
    return None, None


def date_range_bounds(date_from: str = "", date_to: str = "", period: str = ""):
    """Return (start, end) half-open interval for filtering."""
    start_date = parse_iso_date(date_from)
    end_date = parse_iso_date(date_to)
    if start_date is None and end_date is None and period in {"day", "week", "month"}:
        start_date, end_date = period_bounds(period)
    start = datetime.combine(start_date, dtime.min) if start_date else None
    end = datetime.combine(end_date + timedelta(days=1), dtime.min) if end_date else None
    if start and end and start > end - timedelta(days=1):
        return None, None
    return start, end

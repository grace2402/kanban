"""Centralized timezone constants and helpers.

All time-zone logic lives here so callers never duplicate TZ definitions.
"""

from datetime import datetime, timedelta, timezone


# ── Constants ──
TAIPEI_TZ = timezone(timedelta(hours=8))
TZ_NAME = 'Asia/Taipei'


def to_gcal_datetime(dt_val):
    """Convert various timestamp formats to naive ISO string in Asia/Taipei.

    Google Calendar requires dateTime WITHOUT offset when timeZone param is set.
    Must convert UTC→Taipei first, then strip — otherwise 01:00UTC becomes
    the wrong Taipei time.

    Args:
        dt_val: datetime object, or string like "2026-06-01T09:00" / "2026-06-01 09:00+08:00" / ...

    Returns:
        Naive ISO string (e.g. "2026-06-01T17:00:00") in Asia/Taipei local time, or None.
    """
    if dt_val is None:
        return None

    s = str(dt_val)
    s = s.replace(' ', 'T')  # Ensure T separator for fromisoformat
    s_clean = s.replace('Z', '+00:00')

    try:
        dt = datetime.fromisoformat(s_clean)
        if dt.tzinfo is not None:
            # Convert to Taipei timezone, then strip tzinfo (naive = local time)
            taipei_dt = dt.astimezone(TAIPEI_TZ).replace(tzinfo=None)
        else:
            # Already naive — assume it's already in the right local time
            taipei_dt = dt
        return taipei_dt.isoformat()
    except (ValueError, TypeError):
        # Fallback: strip anything after '+' to get naive string
        if '+' in s_clean:
            base = s_clean[:s_clean.index('+')]
        else:
            base = s_clean

        return base.strip()[:19]  # YYYY-MM-DDTHH:MM:SS


def effective_end(start_time, end_time):
    """Return effective end datetime string for Google Calendar exclusive-end semantics.

    For multi-day tasks the calendar API expects end to be one day after the last day.
    """
    if not end_time or not start_time:
        return None

    try:
        start_naive = datetime.fromisoformat(to_gcal_datetime(start_time))
        end_naive = datetime.fromisoformat(to_gcal_datetime(end_time))

        # If end date is on a later day, add 1 day for exclusive-end semantics
        if end_naive.date() > start_naive.date():
            return (end_naive + timedelta(days=1)).isoformat()

        return to_gcal_datetime(end_time)
    except Exception:
        return to_gcal_datetime(end_time)


def ensure_taipei_offset(ts_str):
    """Convert naive timestamp from frontend to explicit +08:00 (Taipei).

    Frontend sends naive timestamps like "2026-06-01T09:00" (no timezone).
    PostgreSQL with session TZ=UTC would interpret these as UTC, causing 8-hour offset.
    This function adds explicit +08:00 so DB stores the correct local time.

    Already-offset timestamps pass through unchanged.
    """
    if not ts_str:
        return ts_str

    s = str(ts_str)
    # Already has timezone info - pass through
    if 'T' in s and (s.endswith('Z') or '+' in s.split('T')[1] or '-' in s.split('T')[1].split('+')[0][-5:]):
        return ts_str

    return s + '+08:00'

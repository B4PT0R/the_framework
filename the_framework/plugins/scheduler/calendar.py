"""Small wall-clock schedules shared by wakes and recurring tasks."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def calendar_schedule(value):
    if not isinstance(value, dict) or set(value) - {"time", "timezone", "weekdays"}:
        raise ValueError("calendar requires time, timezone and optional weekdays (Monday=0)")
    try:
        clock = time.fromisoformat(value["time"])
        ZoneInfo(value["timezone"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("calendar requires an HH:MM time and an IANA timezone") from error
    if clock.tzinfo is not None or value["time"] != clock.isoformat(timespec="minutes"):
        raise ValueError("calendar time must be HH:MM without a UTC offset")
    days = value.get("weekdays", list(range(7)))
    if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
        raise ValueError("weekdays must be a nonempty list of integers within 0..6")
    return {"time": clock.isoformat(timespec="minutes"), "timezone": value["timezone"], "weekdays": sorted(set(days))}


def next_calendar(schedule, after):
    """Next instant strictly after `after`; gaps move forward, folds fire once."""
    schedule = calendar_schedule(schedule)
    zone = ZoneInfo(schedule["timezone"])
    after = after.astimezone(UTC)
    day = after.astimezone(zone).date()
    clock = time.fromisoformat(schedule["time"])
    for offset in range(9):
        date = day + timedelta(days=offset)
        if date.weekday() not in schedule["weekdays"]:
            continue
        # fold=0 chooses the first occurrence in autumn. A UTC round trip moves
        # nonexistent spring times forward by the size of the clock gap.
        candidate = datetime.combine(date, clock, zone).replace(fold=0).astimezone(UTC)
        if candidate > after:
            return candidate
    raise ValueError("calendar has no next occurrence")

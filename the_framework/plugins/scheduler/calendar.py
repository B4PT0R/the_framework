"""Wall-clock schedules shared by wakes and recurring tasks."""
from calendar import monthrange
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def _integers(value, field, allowed):
    if not isinstance(value, list) or not value or any(type(n) is not int or n not in allowed for n in value):
        raise ValueError(f"{field} must be a nonempty list of integers within {sorted(allowed)}")
    return sorted(set(value))


def calendar_schedule(value):
    if not isinstance(value, dict) or set(value) - {
            "time", "timezone", "weekdays", "monthdays", "weekday_ordinals"}:
        raise ValueError("calendar requires time, timezone and optional weekdays, monthdays or weekday_ordinals")
    try:
        clock = time.fromisoformat(value["time"])
        ZoneInfo(value["timezone"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("calendar requires an HH:MM time and an IANA timezone") from error
    if clock.tzinfo is not None or value["time"] != clock.isoformat(timespec="minutes"):
        raise ValueError("calendar time must be HH:MM without a UTC offset")
    result = {"time": clock.isoformat(timespec="minutes"), "timezone": value["timezone"]}
    if "monthdays" in value:
        if "weekdays" in value or "weekday_ordinals" in value:
            raise ValueError("monthdays cannot be combined with weekdays or weekday_ordinals")
        result["monthdays"] = _integers(value["monthdays"], "monthdays", {-1, *range(1, 32)})
    else:
        result["weekdays"] = _integers(value.get("weekdays", list(range(7))), "weekdays", set(range(7)))
        if "weekday_ordinals" in value:
            if "weekdays" not in value:
                raise ValueError("weekday_ordinals requires explicit weekdays")
            result["weekday_ordinals"] = _integers(value["weekday_ordinals"], "weekday_ordinals", {-1, 1, 2, 3, 4, 5})
    return result


def _matches(schedule, day):
    last = monthrange(day.year, day.month)[1]
    if "monthdays" in schedule:
        return day.day in schedule["monthdays"] or (-1 in schedule["monthdays"] and day.day == last)
    if day.weekday() not in schedule["weekdays"]:
        return False
    ordinals = schedule.get("weekday_ordinals")
    return ordinals is None or (day.day - 1) // 7 + 1 in ordinals or (-1 in ordinals and day.day + 7 > last)


def next_calendar(schedule, after):
    """Next instant strictly after `after`; gaps move forward, folds fire once.

    Absent monthdays/fifth weekdays are skipped, never clamped to another day.
    Every supported rule has an occurrence within a year (no annual filters).
    """
    schedule = calendar_schedule(schedule)
    zone = ZoneInfo(schedule["timezone"])
    after = after.astimezone(UTC)
    day = after.astimezone(zone).date()
    clock = time.fromisoformat(schedule["time"])
    for offset in range(367):
        date = day + timedelta(days=offset)
        if not _matches(schedule, date):
            continue
        # fold=0 chooses the first occurrence in autumn. A UTC round trip moves
        # nonexistent spring times forward by the size of the clock gap.
        candidate = datetime.combine(date, clock, zone).replace(fold=0).astimezone(UTC)
        if candidate > after:
            return candidate
    raise ValueError("calendar has no next occurrence")

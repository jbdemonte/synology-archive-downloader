"""Weekly policy in NAS local time. Overnight slots belong to their starting day."""

from datetime import datetime


def policy(settings, now=None):
    limit = settings["speed_limit_kib"]
    if not settings.get("schedule_enabled", False):
        return {"allowed": True, "limit_kib": limit, "outside": False}
    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    start = settings["schedule_start"]
    end = settings["schedule_end"]
    day = now.weekday()
    if start == end:
        inside = day in settings["schedule_days"]
    elif start < end:
        inside = day in settings["schedule_days"] and start <= minute < end
    else:
        inside = (day in settings["schedule_days"] and minute >= start) or (
            (day - 1) % 7 in settings["schedule_days"] and minute < end
        )
    return {
        "allowed": inside or settings["schedule_outside"] == "limited",
        "limit_kib": limit if inside else settings["schedule_limit_kib"],
        "outside": not inside,
    }


def validate(settings):
    if type(settings["schedule_enabled"]) is not bool:
        raise ValueError("Invalid schedule switch")
    days = settings["schedule_days"]
    if (
        not isinstance(days, list)
        or len(days) > 7
        or any(type(d) is not int or not 0 <= d <= 6 for d in days)
    ):
        raise ValueError("Invalid schedule days")
    if settings["schedule_enabled"] and not days:
        raise ValueError("Select at least one schedule day")
    for key in ["schedule_start", "schedule_end"]:
        if type(settings[key]) is not int or not 0 <= settings[key] < 1440:
            raise ValueError("Invalid schedule time")
    if settings["schedule_outside"] not in ["pause", "limited"]:
        raise ValueError("Invalid schedule mode")

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger(__name__)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WAKE_MINUTES = (15, 30, 60, 120, 240)


def default_schedule() -> dict[str, object]:
    return {"days": {
        day: {"enabled": False, "start": "22:00", "end": "07:00"} for day in DAYS
    }}


def validate_schedule(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"days"}:
        raise ValueError("Sleep schedule must contain days")
    days = value["days"]
    if not isinstance(days, dict) or set(days) != set(DAYS):
        raise ValueError("Sleep schedule must contain all seven days")
    result = {}
    for day in DAYS:
        window = days[day]
        if not isinstance(window, dict) or set(window) != {"enabled", "start", "end"}:
            raise ValueError(f"Invalid sleep window for {day}")
        if not isinstance(window["enabled"], bool):
            raise ValueError("Sleep day enabled must be true or false")
        for field in ("start", "end"):
            if not isinstance(window[field], str) or not re.fullmatch(
                r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", window[field]
            ):
                raise ValueError("Sleep times must use HH:MM (00:00–23:59)")
        if window["enabled"] and window["start"] == window["end"]:
            raise ValueError(f"Sleep start and end must differ for enabled {day}")
        result[day] = dict(window)
    return {"days": result}


def validate_wake_until(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid sleep wake deadline")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Invalid sleep wake deadline")
    return float(value)


def sleep_status(schedule: dict[str, object], wake_until: float | None = None,
                 *, now: datetime | None = None) -> dict[str, object]:
    # Convert an epoch on every evaluation: do not cache the device's current UTC offset.
    now = datetime.fromtimestamp(time.time()).astimezone() if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Sleep evaluation requires an aware device-local datetime")
    days = schedule["days"]
    today = days[DAYS[now.weekday()]]
    yesterday = days[DAYS[(now.weekday() - 1) % 7]]
    clock = now.strftime("%H:%M")
    scheduled = (
        today["enabled"] and (
            today["start"] <= clock < today["end"] if today["start"] < today["end"]
            else clock >= today["start"]
        )
    ) or (
        yesterday["enabled"] and yesterday["end"] < yesterday["start"]
        and clock < yesterday["end"]
    )
    active_wake = wake_until if wake_until is not None and now.timestamp() < wake_until else None
    return {
        "sleeping": bool(scheduled and active_wake is None),
        "scheduled_sleep": bool(scheduled),
        "wake_until": active_wake,
        "device_time": now.isoformat(timespec="seconds"),
        "timezone": now.tzname() or "",
    }


class SleepRuntime:
    """Read only the credential-free runtime; corrupt/unreadable files fail closed."""

    def __init__(self, path: Path):
        self.path = path
        self.failed = False

    def sleeping(self) -> bool:
        try:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                value = {**default_schedule(), "wake_until": None}
            if not isinstance(value, dict) or set(value) != {"days", "wake_until"}:
                raise ValueError("Invalid sleep runtime")
            schedule = validate_schedule({"days": value["days"]})
            deadline = validate_wake_until(value["wake_until"])
            sleeping = sleep_status(schedule, deadline)["sleeping"]
        except (OSError, ValueError):
            if not self.failed:
                LOG.error("Could not read sleep runtime; stopping music and skipping videos")
            self.failed = True
            return True
        self.failed = False
        return bool(sleeping)

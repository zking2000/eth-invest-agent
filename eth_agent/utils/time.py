from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_today() -> str:
    return datetime.now().date().isoformat()


def local_hour() -> int:
    return datetime.now().hour


def local_minute_of_day() -> int:
    now = datetime.now()
    return now.hour * 60 + now.minute


def parse_hhmm(value: str) -> int:
    hour_str, minute_str = value.split(":", 1)
    return int(hour_str) * 60 + int(minute_str)

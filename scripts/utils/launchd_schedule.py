"""Compatibility helpers for ET-to-local launchd schedule generation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from utils.launchd_calendar import expand_intraday_slots, render_calendar_interval_xml


ET = ZoneInfo("America/New_York")


def convert_et_calendar_entries(
    entries: list[tuple[int, int, int]],
    *,
    local_tz: str | ZoneInfo | None = None,
    reference_date: datetime | date | None = None,
) -> list[dict[str, int]]:
    """Convert ``(weekday, hour, minute)`` ET tuples to local launchd entries."""

    target_tz = _coerce_timezone(local_tz)
    ref_day = _coerce_reference_date(reference_date)
    monday = ref_day - timedelta(days=ref_day.weekday())

    converted: list[dict[str, int]] = []
    seen: set[tuple[int, int, int]] = set()

    for weekday, hour, minute in entries:
        et_dt = datetime(
            monday.year,
            monday.month,
            monday.day,
            hour,
            minute,
            tzinfo=ET,
        ) + timedelta(days=weekday - 1)
        local_dt = et_dt.astimezone(target_tz)
        launchd_weekday = local_dt.weekday() + 1
        key = (launchd_weekday, local_dt.hour, local_dt.minute)
        if key in seen:
            continue
        seen.add(key)
        converted.append(
            {
                "Weekday": launchd_weekday,
                "Hour": local_dt.hour,
                "Minute": local_dt.minute,
            }
        )

    converted.sort(key=lambda item: (item["Weekday"], item["Hour"], item["Minute"]))
    return converted


def _coerce_timezone(local_tz: str | ZoneInfo | None) -> ZoneInfo:
    if isinstance(local_tz, ZoneInfo):
        return local_tz
    if isinstance(local_tz, str):
        return ZoneInfo(local_tz)
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def _coerce_reference_date(reference_date: datetime | date | None) -> date:
    if reference_date is None:
        return datetime.now(ET).date()
    if isinstance(reference_date, datetime):
        if reference_date.tzinfo is None:
            return reference_date.date()
        return reference_date.astimezone(ET).date()
    return reference_date

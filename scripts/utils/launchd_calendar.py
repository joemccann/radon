"""Helpers for generating local launchd calendar entries from ET slots."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def expand_intraday_slots(
    start: tuple[int, int],
    end: tuple[int, int],
    interval_minutes: int,
) -> list[tuple[int, int]]:
    """Expand an ET intraday window into ``[(hour, minute), ...]`` slots."""

    hour, minute = start
    slots: list[tuple[int, int]] = []
    while hour < end[0] or (hour == end[0] and minute <= end[1]):
        slots.append((hour, minute))
        minute += interval_minutes
        if minute >= 60:
            minute -= 60
            hour += 1
    return slots


def build_local_calendar_entries(
    slots: list[tuple[int, int]],
    *,
    weekdays: list[int],
    local_tz_name: str | None = None,
    reference_monday: date | None = None,
) -> list[dict[str, int]]:
    """Convert ET slots plus weekdays into local launchd entries."""

    target_tz = ZoneInfo(local_tz_name) if local_tz_name else _local_tz()
    monday = reference_monday or _current_et_monday()

    converted: list[dict[str, int]] = []
    seen: set[tuple[int, int, int]] = set()

    for weekday in weekdays:
        for hour, minute in slots:
            et_dt = datetime(
                monday.year,
                monday.month,
                monday.day,
                hour,
                minute,
                tzinfo=ET,
            ) + timedelta(days=weekday - 1)
            local_dt = et_dt.astimezone(target_tz)
            key = (local_dt.weekday() + 1, local_dt.hour, local_dt.minute)
            if key in seen:
                continue
            seen.add(key)
            converted.append(
                {
                    "Weekday": local_dt.weekday() + 1,
                    "Hour": local_dt.hour,
                    "Minute": local_dt.minute,
                }
            )

    converted.sort(key=lambda item: (item["Weekday"], item["Hour"], item["Minute"]))
    return converted


def render_calendar_interval_xml(entries: list[dict[str, int]]) -> str:
    lines: list[str] = []
    for entry in entries:
        lines.extend(
            [
                "        <dict>",
                "            <key>Hour</key>",
                f"            <integer>{entry['Hour']}</integer>",
                "            <key>Minute</key>",
                f"            <integer>{entry['Minute']}</integer>",
                "            <key>Weekday</key>",
                f"            <integer>{entry['Weekday']}</integer>",
                "        </dict>",
            ]
        )
    return "\n".join(lines) + ("\n" if lines else "")


def _current_et_monday() -> date:
    today = datetime.now(ET).date()
    return today - timedelta(days=today.weekday())


def _local_tz():
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")

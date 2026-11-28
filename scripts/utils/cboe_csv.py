"""Cboe daily-prices CSV.

Dates arrive as MM/DD/YYYY. Header and malformed rows are skipped.
The value column is chosen by the caller (CLOSE, SPX, VVIX, SKEW).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any


def parse_price_row(raw: dict[str, Any], value_column: str) -> tuple[str, float] | None:
    try:
        parsed = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y")
        value = float(raw[value_column])
    except (KeyError, TypeError, ValueError):
        return None
    return parsed.date().isoformat(), value


def parse_daily_prices(text: str, value_column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        parsed = parse_price_row(raw, value_column)
        if parsed is None:
            continue
        day, value = parsed
        rows.append({"date": day, "value": value})
    rows.sort(key=lambda row: row["date"])
    return rows

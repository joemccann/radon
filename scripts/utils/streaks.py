"""Consecutive daily gains (STREAKS) — pure computation, no network.

A streak is the count of consecutive sessions with close > previous close,
ending at the given session; a down or flat close resets it to 0. A run is
a maximal block of sessions with streak > 0 (the trailing in-progress run
counts). Payload contract: docs/indicators/streaks.md.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping


def closes_to_rows(closes: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """{date: close} -> ascending [{date, close}] rows.

    Date keys are truncated to YYYY-MM-DD (datetime strings collapse onto
    their day; the later key wins). Non-finite and non-positive closes drop.
    """
    cleaned: dict[str, float] = {}
    for raw_date, raw_close in (closes or {}).items():
        date = str(raw_date or "")[:10]
        if len(date) != 10:
            continue
        try:
            value = float(raw_close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        cleaned[date] = value
    return [{"date": date, "close": cleaned[date]} for date in sorted(cleaned)]


def compute_streaks(closes: list[float]) -> list[int]:
    """streak[i] = consecutive gains ending at i; down/flat resets to 0."""
    streaks: list[int] = []
    for i, value in enumerate(closes):
        if i > 0 and value > closes[i - 1]:
            streaks.append(streaks[-1] + 1)
        else:
            streaks.append(0)
    return streaks


def run_lengths(streaks: list[int]) -> list[int]:
    """Lengths of maximal streak>0 blocks, chronological, incl. the trailing
    in-progress run."""
    runs: list[int] = []
    for i, streak in enumerate(streaks):
        if streak > 0 and (i + 1 == len(streaks) or streaks[i + 1] == 0):
            runs.append(streak)
    return runs


def parse_yahoo_chart(payload: Any) -> dict[str, float]:
    """Yahoo v8 chart payload -> {YYYY-MM-DD: close}. Malformed -> {}."""
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    if not isinstance(result, dict):
        return {}
    timestamps = result.get("timestamp") or []
    quote_groups = (result.get("indicators") or {}).get("quote") or [{}]
    raw_closes = (quote_groups[0] or {}).get("close") or []
    closes: dict[str, float] = {}
    for ts, close in zip(timestamps, raw_closes):
        if close is None:
            continue
        try:
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            value = float(close)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        if math.isfinite(value) and value > 0:
            closes[day] = value
    return closes


def _missing_payload(symbol: str, source: Any, scan_time: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "scan_time": scan_time,
        "source": source,
        "missing": True,
        "count": 0,
        "first_date": None,
        "last_date": None,
        "current": None,
        "stats": None,
        "series": [],
    }


def build_streaks_payload(
    symbol: str,
    closes: Mapping[str, Any] | None,
    *,
    source: Any,
    scan_time: Any,
) -> dict[str, Any]:
    """Full STREAKS payload; fewer than 2 usable closes is the missing shell."""
    sym = (symbol or "").strip().upper()
    rows = closes_to_rows(closes)
    if len(rows) < 2:
        return _missing_payload(sym, source, scan_time)

    values = [row["close"] for row in rows]
    streaks = compute_streaks(values)
    runs = run_lengths(streaks)
    current_streak = streaks[-1]
    max_streak = max(streaks)
    max_streak_end = next(
        rows[i]["date"]
        for i in range(len(rows) - 1, -1, -1)
        if streaks[i] == max_streak
    )
    comparisons = len(values) - 1
    up_days = sum(1 for i in range(1, len(values)) if values[i] > values[i - 1])

    return {
        "symbol": sym,
        "scan_time": scan_time,
        "source": source,
        "missing": False,
        "count": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "current": {
            "date": rows[-1]["date"],
            "close": round(values[-1], 4),
            "streak": current_streak,
            "day_change_pct": round((values[-1] / values[-2] - 1) * 100, 2),
        },
        "stats": {
            "max_streak": max_streak,
            "max_streak_end": max_streak_end,
            "runs_total": len(runs),
            "runs_ge_current": (
                sum(1 for length in runs if length >= current_streak)
                if current_streak > 0
                else None
            ),
            "avg_run": round(sum(runs) / len(runs), 2) if runs else None,
            "up_day_pct": round(up_days / comparisons * 100, 1),
        },
        "series": [
            {"date": row["date"], "close": round(row["close"], 4), "streak": streak}
            for row, streak in zip(rows, streaks)
        ],
    }

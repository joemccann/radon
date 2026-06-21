"""Per-ticker daily flow accrual + reader for Chronos-2 forecasting.

``record_daily_flow`` is the write side: it maps a scanner / discover result
dict onto a ``ticker_flow_history`` row (migration 0014) and is strictly
best-effort — a forecasting-history write must never break a scan.

``flow_series_for`` is the read side: it returns a parallel
(dates, values) pair for a single metric, suitable for handing straight to
``forecasting.chronos_engine.forecast_quantiles``.
"""

from __future__ import annotations

import sys
from typing import Any


def flow_series_for(
    ticker: str,
    *,
    metric: str = "flow_strength",
    lookback_days: int = 120,
) -> tuple[list[str], list[float]]:
    """Return (dates, values) for ``metric`` ascending by date.

    Rows whose value for ``metric`` is ``None`` are dropped so the series is
    a clean numeric vector. Dates and values stay index-aligned.
    """
    from db.writer import get_ticker_flow_history

    rows = get_ticker_flow_history(ticker, lookback_days=lookback_days)

    dates: list[str] = []
    values: list[float] = []
    for row in rows:
        value = row.get(metric)
        if value is None:
            continue
        dates.append(row.get("date"))
        values.append(float(value))
    return dates, values


def record_daily_flow(ticker: str, date: str, scan_row: dict[str, Any]) -> None:
    """Persist one (ticker, date) flow row from a scan/discover result.

    Best-effort: any failure is logged to stderr and swallowed so the caller's
    scan is never interrupted by a forecasting-history write.
    """
    try:
        from db.writer import upsert_ticker_flow_history

        flow_strength = scan_row.get("flow_strength", scan_row.get("strength"))
        dp_direction = scan_row.get("direction", scan_row.get("dp_direction"))

        upsert_ticker_flow_history(
            ticker,
            date,
            flow_strength=flow_strength,
            dp_direction=dp_direction,
            buy_ratio=scan_row.get("buy_ratio"),
            num_prints=scan_row.get("num_prints"),
            total_premium=scan_row.get("total_premium"),
            total_volume=scan_row.get("total_volume"),
        )
    except Exception as exc:  # best-effort: never break a scan
        print(
            f"record_daily_flow: non-fatal failure for {ticker} {date}: {exc}",
            file=sys.stderr,
        )

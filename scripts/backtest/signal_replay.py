"""Load historical signal rows from Turso snapshot tables into a time-keyed
signal series the engine can replay.

DB access goes through ``scripts/db/writer.py`` / ``scripts/db/client.py`` —
the same lean libsql path the forecasting package uses. NOTHING here runs on
the FastAPI event loop (that side uses the HTTP pipeline); this module is for
the operator-invoked / scheduled backtest subprocess.

Each loader returns a list of ``SignalPoint`` ascending by date, where
``SignalPoint.signal`` is the parsed per-day payload for that strategy. Loaders
also expose an underlying price series so the engine can build forward returns
without the strategy peeking at the future.

Only the CRI loader is fully wired (its snapshot history carries a clean daily
series with SPY closes). The other strategies are registered but raise
``NotImplementedError`` with an honest message — see ``strategies.py``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .engine import SignalPoint


def _latest_cri_history() -> list[dict[str, Any]]:
    """Most recent CRI snapshot's daily ``history`` rows, ascending by date.

    The CRI snapshot payload embeds a trailing daily history (date, spy, cri
    components, realized_vol, cor1m, spx_vs_ma_pct). We read the freshest
    snapshot row and return its history list.
    """
    from db.client import get_db

    db = get_db()
    cursor = db.execute(
        "SELECT payload FROM cri_snapshots ORDER BY date DESC, taken_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        return []
    payload = json.loads(row[0])
    history = payload.get("history") or []
    return sorted(history, key=lambda r: r.get("date", ""))


def load_cri_series(
    history: Optional[list[dict[str, Any]]] = None,
) -> tuple[list[SignalPoint], list[tuple[str, float]]]:
    """CRI daily signal series + (date, spy_close) underlying series.

    Pass ``history`` to replay an explicit list (used by tests); omit it to
    read the latest CRI snapshot from Turso.

    Returns ``(points, underlying)`` where ``points[i].signal`` carries the
    crash-regime inputs for that date and ``underlying`` is the aligned SPY
    close series the engine turns into forward returns.
    """
    rows = history if history is not None else _latest_cri_history()

    points: list[SignalPoint] = []
    underlying: list[tuple[str, float]] = []
    for row in rows:
        date = row.get("date")
        spy = row.get("spy")
        if date is None or spy is None:
            continue
        points.append(
            SignalPoint(
                date=date,
                signal={
                    "cri": row.get("cri"),
                    "realized_vol": row.get("realized_vol"),
                    "cor1m": row.get("cor1m"),
                    "spx_vs_ma_pct": row.get("spx_vs_ma_pct"),
                    "spy": float(spy),
                },
            )
        )
        underlying.append((date, float(spy)))
    return points, underlying


def forward_returns_from_underlying(
    underlying: list[tuple[str, float]],
    *,
    horizon: int = 1,
) -> dict[str, float]:
    """Build a {origin_date -> forward simple return} map from a price series.

    The forward return for an entry at ``underlying[i]`` is the return of the
    underlying from close i to close ``i + horizon``. The last ``horizon``
    origins have no future close and are intentionally absent from the map so
    the engine skips them (no look-ahead, no synthetic fill).
    """
    forward: dict[str, float] = {}
    for i in range(len(underlying) - horizon):
        date_i, price_i = underlying[i]
        _, price_future = underlying[i + horizon]
        if price_i == 0:
            continue
        forward[date_i] = price_future / price_i - 1.0
    return forward

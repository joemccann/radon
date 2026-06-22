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


# --------------------------------------------------------------------------- #
# VCG-R (Strategy 5) — vol/credit divergence                                   #
# --------------------------------------------------------------------------- #
def _latest_vcg_history() -> list[dict[str, Any]]:
    """Backfilled VCG daily history from every persisted ``vcg_snapshots`` row.

    Each VCG snapshot embeds a trailing daily ``history`` (date, vcg, vix,
    vvix, credit, beta1, beta2, ro). Backfilling across ALL snapshots gives the
    engine real data depth instead of the ~10 days a single snapshot carries.
    """
    from db.client import get_db

    db = get_db()
    cursor = db.execute("SELECT payload FROM vcg_snapshots ORDER BY scan_time ASC")
    payloads = [json.loads(row[0]) for row in cursor.fetchall()]
    return backfill_vcg_history(payloads)


def backfill_vcg_history(
    payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge the embedded ``history`` of many VCG snapshots into one series.

    Snapshots overlap (each carries a trailing window), so the same date can
    appear in several payloads. We keep the LAST occurrence per date — payloads
    arrive oldest-first, so a fresher snapshot's read of a day wins — and return
    the merged rows ascending by date. This is the aggregation that gives the
    hit-rate metrics real depth from sparse rolling snapshots.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for row in payload.get("history") or []:
            date = row.get("date")
            if date is None:
                continue
            by_date[date] = row
    return [by_date[date] for date in sorted(by_date)]


def load_vcg_series(
    history: Optional[list[dict[str, Any]]] = None,
) -> tuple[list[SignalPoint], list[tuple[str, float]]]:
    """VCG daily signal series + (date, credit_close) underlying series.

    The credit proxy (HYG by default) close is the underlying the RO hedge
    shorts. Pass ``history`` to replay an explicit list (tests); omit it to
    backfill from every persisted ``vcg_snapshots`` row.
    """
    rows = history if history is not None else _latest_vcg_history()

    points: list[SignalPoint] = []
    underlying: list[tuple[str, float]] = []
    for row in rows:
        date = row.get("date")
        credit = row.get("credit")
        if date is None or credit is None:
            continue
        points.append(
            SignalPoint(
                date=date,
                signal={
                    "vcg": row.get("vcg"),
                    "vix": row.get("vix"),
                    "vvix": row.get("vvix"),
                    "beta1": row.get("beta1"),
                    "beta2": row.get("beta2"),
                    "credit": float(credit),
                },
            )
        )
        underlying.append((date, float(credit)))
    return points, underlying


# --------------------------------------------------------------------------- #
# Dark Pool Flow (Strategy 1) — sustained directional institutional flow       #
# --------------------------------------------------------------------------- #
def _latest_dark_pool_series(ticker: str) -> list[dict[str, Any]]:
    """Daily ``ticker_flow_history`` rows for ``ticker``, ascending by date.

    NOTE: ``ticker_flow_history`` stores the flow signal but NOT an underlying
    close, so it cannot mark a directional trade to market on its own. The
    backtest reports ``insufficient_data`` until a price-bearing series is fed
    in (the loader accepts ``close`` per row for synthetic / future use).
    """
    from db.writer import get_ticker_flow_history

    return get_ticker_flow_history(ticker)


def load_dark_pool_series(
    history: Optional[list[dict[str, Any]]] = None,
    *,
    ticker: str = "SPY",
) -> tuple[list[SignalPoint], list[tuple[str, float]]]:
    """Dark-pool flow signal series + aligned underlying close series.

    Each row carries ``flow_strength`` and ``dp_direction`` (the documented
    Strategy 1 inputs). A row only contributes to the underlying series if it
    also carries a ``close`` — the flow history table has no price column, so
    the production read yields a flow series with NO underlying, which the
    strategy layer surfaces as ``insufficient_data``.
    """
    rows = history if history is not None else _latest_dark_pool_series(ticker)

    points: list[SignalPoint] = []
    underlying: list[tuple[str, float]] = []
    for row in rows:
        date = row.get("date")
        if date is None:
            continue
        points.append(
            SignalPoint(
                date=date,
                signal={
                    "flow_strength": row.get("flow_strength"),
                    "dp_direction": row.get("dp_direction"),
                    "close": row.get("close"),
                },
            )
        )
        close = row.get("close")
        if close is not None:
            underlying.append((date, float(close)))
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

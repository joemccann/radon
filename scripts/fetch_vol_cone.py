#!/usr/bin/env python3
"""VOL CONE — cheap 10% OTM wing IV scanner.

Per ticker, scan every standard monthly expiry (third Friday only, never
weeklies or dailies) with DTE in [21, 180]. For each session, interpolate UW
greeks to ATM (mean of call and put at K/S = 1.00) and the 10% OTM wings
(1.10 call, 0.90 put). The cone is expiry-local: NVDA Sep is not NVDA Oct.

Source: Unusual Whales /api/stock/{ticker}/greeks (Bearer UW_TOKEN).
Spec: docs/indicators/vol-cone.md.

Output is dual-written to Turso vol_cone_history + data/vol_cone.json.

Usage:
    python3 scripts/fetch_vol_cone.py            # human summary (stderr)
    python3 scripts/fetch_vol_cone.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from utils.market_calendar import (  # noqa: E402 — needs the sys.path insert
    get_last_n_trading_days,
    last_completed_session_date,
)

# ── constants ─────────────────────────────────────────────────────
VOL_CONE_JSON = _PROJECT_DIR / "data" / "vol_cone.json"

DEFAULT_UNIVERSE = [
    "NVDA", "SMH", "AAPL", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "AMD",
    "AVGO", "QQQ", "SPY", "IWM", "NFLX", "MU", "ARM", "TSM", "ASML", "INTC",
    "QCOM", "AMAT", "LRCX", "KLAC", "TLT", "GLD",
]
_MIN_DTE = 21
_MAX_DTE = 180
_MONTHLY_LOOKAHEAD = 8
_MAX_LOOKBACK = 80
_UNIVERSE_CAP = 40
_THROTTLE_S = 0.3
_BUDGET_ENV = "VOL_CONE_BUDGET_S"
_DEFAULT_BUDGET_S = 1200.0
_HIT_REGIMES = frozenset({"CHEAP_WINGS", "CHEAP_ATM"})


# ── chain parsing + moneyness interpolation ───────────────────────

def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_greek_rows(payload: dict) -> list[dict[str, Any]]:
    """UW greeks response → per-strike rows with numeric fields coerced.

    date/expiry stay strings; strike and IVs arrive as strings and become
    floats (None when missing or unparseable).
    """
    rows: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        rows.append(
            {
                "date": raw.get("date"),
                "expiry": raw.get("expiry"),
                "strike": _to_float(raw.get("strike")),
                "call_volatility": _to_float(raw.get("call_volatility")),
                "put_volatility": _to_float(raw.get("put_volatility")),
            }
        )
    return rows


def interpolate_iv_at_moneyness(
    rows: list[dict[str, Any]], spot: float, target: float, side: str
) -> tuple[Optional[float], Optional[float]]:
    """IV linearly interpolated in moneyness (strike / spot) to ``target``.

    ``side`` is ``call`` or ``put``. An exact moneyness hit short-circuits
    and returns the listed strike. Otherwise the bracketing pair is used
    and the returned strike is ``target * spot``. Missing bracket or
    non-positive IV → ``(None, None)``.
    """
    if not spot or spot <= 0:
        return None, None
    iv_key = f"{side}_volatility"
    points: list[tuple[float, float, float]] = []
    for row in rows:
        strike = _to_float(row.get("strike"))
        iv = _to_float(row.get(iv_key))
        if strike is None or iv is None or iv <= 0:
            continue
        points.append((strike / spot, iv, strike))
    points.sort()

    for moneyness, iv, strike in points:
        if moneyness == target:
            return iv, strike
    for (m0, v0, _s0), (m1, v1, _s1) in zip(points, points[1:]):
        if m0 < target < m1:
            iv = v0 + (v1 - v0) * (target - m0) / (m1 - m0)
            return iv, target * spot
    return None, None


def session_ivs(rows: list[dict[str, Any]], spot: float) -> Optional[dict[str, float]]:
    """ATM + 10% OTM wings for one session, or None if any leg is missing."""
    call_atm, _ = interpolate_iv_at_moneyness(rows, spot, 1.00, "call")
    put_atm, _ = interpolate_iv_at_moneyness(rows, spot, 1.00, "put")
    call_10, call_k = interpolate_iv_at_moneyness(rows, spot, 1.10, "call")
    put_10, put_k = interpolate_iv_at_moneyness(rows, spot, 0.90, "put")
    if (
        call_atm is None or put_atm is None
        or call_10 is None or put_10 is None
        or call_atm <= 0 or put_atm <= 0 or call_10 <= 0 or put_10 <= 0
    ):
        return None
    return {
        "atm_iv": (call_atm + put_atm) / 2.0,
        "call_10_iv": call_10,
        "put_10_iv": put_10,
        "call_10_strike": call_k,
        "put_10_strike": put_k,
    }


# ── monthly expiry selection ──────────────────────────────────────

def third_friday(year: int, month: int) -> date:
    """Third Friday of the month (standard monthly option expiration)."""
    first_friday_offset = (4 - date(year, month, 1).weekday()) % 7
    return date(year, month, 1 + first_friday_offset + 14)


def select_target_expiries(as_of: date) -> list[date]:
    """Every third-Friday monthly with DTE in [21, 180], ascending.

    Weeklies and the front monthly inside 21 DTE are excluded. As of
    2026-08-12 that is Sep 18, Oct 16, Nov 20, Dec 18, Jan 15.
    """
    candidates: list[date] = []
    year, month = as_of.year, as_of.month
    for _ in range(_MONTHLY_LOOKAHEAD):
        expiry = third_friday(year, month)
        dte = (expiry - as_of).days
        if _MIN_DTE <= dte <= _MAX_DTE:
            candidates.append(expiry)
        month += 1
        if month > 12:
            month, year = 1, year + 1
    if not candidates:
        raise ValueError(f"no monthly expiry in [{_MIN_DTE}, {_MAX_DTE}] DTE as of {as_of}")
    return candidates


# ── cone stats + regime ───────────────────────────────────────────

def percentile(values: list[float], p: float) -> float:
    """Linear order-statistic percentile on sorted ``values``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = p * (len(ordered) - 1)
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def rank_strictly_below(value: float, values: list[float]) -> float:
    """Share of ``values`` strictly below ``value``."""
    if not values:
        return 0.0
    return sum(1 for item in values if item < value) / len(values)


def classify_regime(atm_p: float, call_p: float, put_p: float) -> str:
    """Inclusive cheap bounds; RICH is the other tail; else NEUTRAL."""
    if atm_p <= 0.15 and call_p <= 0.20 and put_p <= 0.20:
        return "CHEAP_WINGS"
    if atm_p <= 0.15:
        return "CHEAP_ATM"
    if atm_p >= 0.85:
        return "RICH"
    return "NEUTRAL"


def compute_name(ticker: str, expiry: str, series: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach cone stats + regime to one ticker's ascending session series."""
    ordered = sorted(series, key=lambda row: row["date"])
    atms = [row["atm_iv"] for row in ordered]
    call_10s = [row["call_10_iv"] for row in ordered]
    put_10s = [row["put_10_iv"] for row in ordered]
    latest = ordered[-1]
    atm_p = rank_strictly_below(latest["atm_iv"], atms)
    call_p = rank_strictly_below(latest["call_10_iv"], call_10s)
    put_p = rank_strictly_below(latest["put_10_iv"], put_10s)
    expiry_date = date.fromisoformat(expiry)
    last_date = date.fromisoformat(latest["date"])
    return {
        "ticker": ticker,
        "spot": latest["spot"],
        "expiry": expiry,
        "month": expiry_date.strftime("%b").upper(),
        "dte": (expiry_date - last_date).days,
        "atm_iv": latest["atm_iv"],
        "call_10_iv": latest["call_10_iv"],
        "put_10_iv": latest["put_10_iv"],
        "call_10_strike": latest.get("call_10_strike"),
        "put_10_strike": latest.get("put_10_strike"),
        "p10": percentile(atms, 0.10),
        "p90": percentile(atms, 0.90),
        "atm_percentile": atm_p,
        "call_10_percentile": call_p,
        "put_10_percentile": put_p,
        "wing_score": (call_p + put_p) / 2.0,
        "regime": classify_regime(atm_p, call_p, put_p),
        "series": [_series_point(row) for row in ordered],
    }


def merge_universe(
    watchlist: list[str],
    seed: Optional[list[str]] = None,
    cap: int = _UNIVERSE_CAP,
) -> list[str]:
    """Union seed then watchlist, uppercase, first-seen order, capped."""
    merged: list[str] = []
    seen: set[str] = set()
    for raw in list(seed if seed is not None else DEFAULT_UNIVERSE) + list(watchlist):
        ticker = str(raw or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        merged.append(ticker)
        if len(merged) >= cap:
            break
    return merged


def build_output(
    names: list[dict[str, Any]], scan_time: str, source_as_of: str
) -> dict[str, Any]:
    hits = [name for name in names if name.get("regime") in _HIT_REGIMES]
    hits = sorted(hits, key=lambda name: (name["wing_score"], name["atm_percentile"]))
    if hits:
        current = hits[0]
    elif names:
        current = min(names, key=lambda name: (name["wing_score"], name["atm_percentile"]))
    else:
        current = None
    return {
        "scan_time": scan_time,
        "source_as_of": source_as_of,
        "count": len(names),
        "hit_count": len(hits),
        "current": current,
        "names": names,
        "hits": hits,
    }


# ── UW client (reuses the shared scripts/clients/uw_client.py) ────

def _closes_from_payload(payload: Any) -> dict[str, float]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    closes: dict[str, float] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        day = raw.get("date")
        close = _to_float(raw.get("close"))
        if day and close is not None:
            closes[str(day)[:10]] = close
    return closes


def _spot_from_payload(payload: Any) -> Optional[float]:
    if not isinstance(payload, dict):
        return _to_float(payload)
    close = _to_float(payload.get("close"))
    if close is not None:
        return close
    data = payload.get("data")
    if isinstance(data, dict):
        return _to_float(data.get("close"))
    if isinstance(data, list) and data:
        latest = max(
            (row for row in data if isinstance(row, dict)),
            key=lambda row: str(row.get("date") or ""),
            default=None,
        )
        if latest is not None:
            return _to_float(latest.get("close"))
    return None


class _DefaultClient:
    """fetch_greeks / fetch_closes / fetch_spot adapter over UWClient."""

    def __init__(self) -> None:
        from clients.uw_client import UWClient

        # web/.env stores the token double-quoted; dotenv usually strips the
        # quotes but a stale shell export may not — strip defensively.
        token = (os.environ.get("UW_TOKEN") or "").strip().strip('"').strip("'")
        self._uw = UWClient(token=token or None)

    def fetch_greeks(self, ticker: str, expiry: str, as_of: Optional[str] = None) -> dict:
        kwargs: dict[str, Any] = {}
        if as_of:
            kwargs["date"] = as_of
        return self._uw.get_greeks(ticker, expiry=expiry, **kwargs)

    def fetch_closes(self, ticker: str) -> dict[str, float]:
        try:
            closes = _closes_from_payload(self._uw.get_stock_ohlc(ticker, candle_size="1d"))
            if closes:
                return closes
        except Exception as exc:  # noqa: BLE001 — iv-rank is the documented fallback
            print(f"[vol-cone] {ticker} ohlc failed ({exc}); trying iv-rank", file=sys.stderr)
        try:
            return _closes_from_payload(self._uw.get_iv_rank(ticker, timespan="1Y"))
        except Exception as exc:  # noqa: BLE001 — missing closes skip those dates
            print(f"[vol-cone] {ticker} iv-rank closes failed: {exc}", file=sys.stderr)
            return {}

    def fetch_spot(self, ticker: str) -> float:
        spot = _spot_from_payload(self._uw.get_stock_state(ticker))
        if spot is None:
            raise ValueError(f"no spot close for {ticker}")
        return spot


# ── persistence ───────────────────────────────────────────────────

def _series_point(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": row["date"],
        "spot": row["spot"],
        "atm_iv": row["atm_iv"],
        "call_10_iv": row["call_10_iv"],
        "put_10_iv": row["put_10_iv"],
        "call_10_strike": row.get("call_10_strike"),
        "put_10_strike": row.get("put_10_strike"),
    }


def _flatten_history_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in payload.get("names") or []:
        expiry = name["expiry"]
        expiry_date = date.fromisoformat(expiry)
        ticker = name["ticker"]
        for point in name.get("series") or []:
            rows.append(
                {
                    "ticker": ticker,
                    "date": point["date"],
                    "expiry": expiry,
                    "dte": (expiry_date - date.fromisoformat(point["date"])).days,
                    "spot": point["spot"],
                    "atm_iv": point["atm_iv"],
                    "call_10_iv": point["call_10_iv"],
                    "put_10_iv": point["put_10_iv"],
                    "call_10_strike": point.get("call_10_strike"),
                    "put_10_strike": point.get("put_10_strike"),
                }
            )
    return rows


def _write_db_cache(
    payload: dict[str, Any], scan_time: str, *, rows_changed: bool
) -> None:
    """Best-effort Turso mirror; data/vol_cone.json is the disk fallback.

    Durable vol_cone_history rows are skipped when no session was added;
    the snapshot + heartbeat run EVERY cycle so the staleness banner
    notices a silent writer (cf. feedback_service_health_heartbeat).
    """
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_vol_cone_rows(_flatten_history_rows(payload), recorded_at=scan_time)
        writer.upsert_scan_snapshot("vol-cone", scan_time, payload)
        writer.record_service_health("vol-cone", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[vol-cone] db cache non-fatal: {exc}", file=sys.stderr)


def _checkpoint_rows(rows: list[dict[str, Any]], scan_time: str) -> None:
    """Persist one (ticker, expiry) delta the moment it completes.

    A deploy's stop-clean SIGTERMs this oneshot mid-scan (incident
    2026-08-12), so rows held in memory until the terminal write were
    discarded on every interruption and the backfill never converged.
    """
    if not rows:
        return
    try:
        from db import writer

        writer.ensure_no_replica_for_writers()
        writer.upsert_vol_cone_rows(rows, recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — the terminal write retries
        print(f"[vol-cone] checkpoint non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    VOL_CONE_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = VOL_CONE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, VOL_CONE_JSON)


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(VOL_CONE_JSON.read_text())
    except (OSError, ValueError):
        return None


def _read_history_rows() -> list[dict[str, Any]]:
    """Turso vol_cone_history -> session rows (JSON names[].series fallback)."""
    keys = (
        "ticker",
        "date",
        "expiry",
        "dte",
        "spot",
        "atm_iv",
        "call_10_iv",
        "put_10_iv",
        "call_10_strike",
        "put_10_strike",
    )
    try:
        from db.client import get_db

        rows = get_db().execute(
            "SELECT ticker, date, expiry, dte, spot, atm_iv, call_10_iv, put_10_iv, "
            "call_10_strike, put_10_strike FROM vol_cone_history ORDER BY ticker, date"
        ).fetchall()
        return [dict(zip(keys, row)) for row in rows]
    except Exception as exc:  # noqa: BLE001 — the JSON fallback still works
        print(f"[vol-cone] turso rehydrate non-fatal: {exc}", file=sys.stderr)
    cached = _read_json_cache()
    if not cached:
        return []
    return _flatten_history_rows(cached)


def _load_watchlist() -> list[str]:
    try:
        from db.readers import read_watchlist_tickers

        return read_watchlist_tickers()
    except Exception as exc:  # noqa: BLE001 — seed-only universe still works
        print(f"[vol-cone] watchlist read non-fatal: {exc}", file=sys.stderr)
        return []


def _scan_time_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── session fetching ──────────────────────────────────────────────

def _missing_sessions(stored_dates: set[str], last_completed: str) -> list[str]:
    """Trading days in the 80-session lookback not yet stored, ascending."""
    anchor = datetime.strptime(last_completed, "%Y-%m-%d").replace(hour=16, minute=30)
    lookback = get_last_n_trading_days(_MAX_LOOKBACK, from_date=anchor)
    return [day for day in reversed(lookback) if day not in stored_dates]


def _backfill_ticker(
    client: Any,
    ticker: str,
    expiry: date,
    last_completed: str,
    stored_dates: set[str],
) -> list[dict[str, Any]]:
    missing = _missing_sessions(stored_dates, last_completed)
    if not missing:
        return []
    closes: dict[str, float] = {}
    try:
        closes = client.fetch_closes(ticker) or {}
    except Exception as exc:  # noqa: BLE001 — last-session spot can still save the row
        print(f"[vol-cone] {ticker} closes failed: {exc}", file=sys.stderr)
    live_spot: Optional[float] = None
    new_rows: list[dict[str, Any]] = []
    throttle = hasattr(client, "_uw")
    expiry_s = expiry.isoformat()
    for session in missing:
        spot = _to_float(closes.get(session))
        if spot is None and session == last_completed:
            if live_spot is None:
                try:
                    live_spot = _to_float(client.fetch_spot(ticker))
                except Exception as exc:  # noqa: BLE001
                    print(f"[vol-cone] {ticker} spot failed: {exc}", file=sys.stderr)
                    live_spot = None
            spot = live_spot
        if spot is None:
            continue
        print(f"[vol-cone] fetching {ticker} {session}", file=sys.stderr)
        try:
            payload = client.fetch_greeks(ticker, expiry_s, session)
        except Exception as exc:  # noqa: BLE001 — skip; next run self-heals
            print(f"[vol-cone] {ticker} {session}: {exc}", file=sys.stderr)
            continue
        finally:
            if throttle:
                time.sleep(_THROTTLE_S)
        ivs = session_ivs(parse_greek_rows(payload or {}), spot)
        if ivs is None:
            continue
        new_rows.append(
            {
                "ticker": ticker,
                "date": session,
                "expiry": expiry_s,
                "dte": (expiry - date.fromisoformat(session)).days,
                "spot": spot,
                **ivs,
            }
        )
    return new_rows


# ── orchestration ─────────────────────────────────────────────────

def _monotonic() -> float:
    return time.monotonic()


def _budget_seconds() -> float:
    """Wall-clock ceiling for one invocation, seconds (``VOL_CONE_BUDGET_S``).

    Sized to finish well inside the unit's TimeoutStartSec and a deploy
    window, so a cold-start backfill converges across successive daily runs
    instead of attempting every missing session in one multi-hour scan.
    """
    try:
        budget = float(os.environ.get(_BUDGET_ENV) or _DEFAULT_BUDGET_S)
    except ValueError:
        return _DEFAULT_BUDGET_S
    return budget if budget > 0 else _DEFAULT_BUDGET_S


def _scan_targets(universe: list[str], expiries: list[date]):
    """(ticker, expiry) pairs in scan order, flat so one guard bounds both."""
    for ticker in universe:
        for expiry in expiries:
            yield ticker, expiry


def run(
    client: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
    tickers: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Incremental cone: fetch missing sessions for the target monthly, or heartbeat.

    If every ticker already has the last completed session for every
    standard monthly in the window, skip UW greeks, rebuild from stored
    history, and refresh scan_time only (rows_changed=False).

    Each completed (ticker, expiry) delta is checkpointed to Turso before
    the next one starts, and the scan stops once the wall-clock budget is
    spent — a kill mid-scan then costs at most one pair, and the snapshot
    plus service_health heartbeat still land on the truncated path.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if client is None:
        client = _DefaultClient()

    last_completed = last_completed_session_date(now)
    expiries = select_target_expiries(date.fromisoformat(last_completed))
    scan_time = _scan_time_iso(now)

    if tickers is None:
        universe = merge_universe(_load_watchlist())
    else:
        universe = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]

    by_key = {
        (row["ticker"], row["date"], row["expiry"]): row
        for row in _read_history_rows()
    }
    deadline = _monotonic() + _budget_seconds()
    rows_changed = False
    for ticker, expiry in _scan_targets(universe, expiries):
        if _monotonic() >= deadline:
            print(
                "[vol-cone] wall-clock budget spent; deferring the rest to the next run",
                file=sys.stderr,
            )
            break
        expiry_s = expiry.isoformat()
        stored = {
            row["date"]
            for row in by_key.values()
            if row["ticker"] == ticker and row["expiry"] == expiry_s
        }
        if last_completed in stored:
            continue
        fetched = _backfill_ticker(client, ticker, expiry, last_completed, stored)
        if not fetched:
            continue
        for row in fetched:
            by_key[(row["ticker"], row["date"], row["expiry"])] = row
        rows_changed = True
        _checkpoint_rows(fetched, scan_time)

    if not rows_changed:
        print("[vol-cone] no missing sessions; refreshing snapshot only", file=sys.stderr)

    names: list[dict[str, Any]] = []
    for ticker in universe:
        for expiry in expiries:
            expiry_s = expiry.isoformat()
            series = [
                _series_point(row)
                for row in by_key.values()
                if row["ticker"] == ticker and row["expiry"] == expiry_s
            ]
            series.sort(key=lambda row: row["date"])
            if series:
                names.append(compute_name(ticker, expiry_s, series))

    if not any(name.get("series") for name in names):
        raise ValueError("vol-cone produced zero series")

    payload = build_output(names, scan_time, last_completed)
    _write_db_cache(payload, scan_time, rows_changed=rows_changed)
    _write_json_cache(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    print(
        f"\nVOL CONE - {payload['count']} names, {payload['hit_count']} hits, "
        f"as-of {payload.get('source_as_of')}",
        file=sys.stderr,
    )
    if current:
        atm = current.get("atm_iv")
        atm_txt = f"{atm * 100:.1f}" if isinstance(atm, (int, float)) else "n/a"
        print(
            f"  {current.get('ticker')}  {current.get('regime')}  "
            f"ATM {atm_txt}  wing {current.get('wing_score')}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cheap 10% OTM wing IV scanner (Unusual Whales greeks cone)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

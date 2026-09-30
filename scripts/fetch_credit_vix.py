#!/usr/bin/env python3
"""CREDIT/VIX Indicator — SHY minus HYG credit proxy vs VIX.

Daily aligned closes: spread = close(SHY) - close(HYG) (USD, unadjusted)
versus the VIX close. Each leg is range-positioned in its own trailing
252-session window; gap = rank_spread - rank_vix.

Sources:
  SHY + HYG — fetch_iei_hyg.fetch_closes (IB Stock -> RH -> UW r -> Yahoo)
  VIX — dedicated ladder: IB Index('VIX','CBOE') -> Cboe CDN -> Yahoo ^VIX
  VIX is never routed through the equity cascade (Stock('VIX') is wrong).

Usage:
    python3 scripts/fetch_credit_vix.py                 # persist + summary
    python3 scripts/fetch_credit_vix.py --json          # JSON to stdout
    python3 scripts/fetch_credit_vix.py --no-db --json  # no Turso I/O
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from db import writer
from fetch_credit_spread import (
    _bar_date,
    _connect_ib_with_retry,
    combine_source,
    fetch_yahoo_chart,
    parse_yahoo_chart,
)
from fetch_iei_hyg import fetch_closes as fetch_iei_hyg_closes
from fetch_iei_hyg import pct_rank as iei_pct_rank
from fetch_vixts import parse_index_csv
from utils.ib_preflight import (
    IB_HISTORICAL_TIMEOUT_S,
    IB_REQUEST_TIMEOUT_S,
    ib_auth_state as _ib_auth_state,
)

# ── constants ─────────────────────────────────────────────────────
SERVICE = "credit-vix"
CREDIT_VIX_JSON = _PROJECT_DIR / "data" / "credit_vix.json"
NO_SOURCE = "none"
STATUS_STALE_SOURCE = "stale_source"

WINDOW_SESSIONS = 252
GAP_THRESHOLD = 0.5
STATE_CREDIT_WIDE = "CREDIT WIDE"
STATE_VIX_WIDE = "VIX WIDE"
STATE_ALIGNED = "ALIGNED"

SHY_SYMBOL = "SHY"
HYG_SYMBOL = "HYG"
VIX_SYMBOL = "VIX"
EQUITY_TICKERS = [SHY_SYMBOL, HYG_SYMBOL]
# Shared with credit-spread / iei-hyg; the unit serializes on the 5669 flock.
CREDIT_VIX_IB_HISTORY_CLIENT_IDS = (56, 69)

Closes = dict[str, float]
FetchCloses = Callable[[list[str]], dict[str, Closes]]
FetchVix = Callable[[], Closes]


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


# ── math ──────────────────────────────────────────────────────────

def pct_rank(value: float, low: float, high: float) -> float:
    """A1: range position. 0.0 when high == low (brief; iei-hyg now returns None)."""
    ranked = iei_pct_rank(value, low, high)
    return 0.0 if ranked is None else ranked


def classify_state(gap: float) -> str:
    if gap >= GAP_THRESHOLD:
        return STATE_CREDIT_WIDE
    if gap <= -GAP_THRESHOLD:
        return STATE_VIX_WIDE
    return STATE_ALIGNED


def z_score(value: float, window: list[float]) -> float:
    """Population z-score. Validation / PR only; not shipped in the payload."""
    n = len(window)
    if n == 0:
        return 0.0
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / n
    if var == 0:
        return 0.0
    return (value - mean) / math.sqrt(var)


def z_gap(spread: float, vix: float, spreads: list[float], vixes: list[float]) -> float:
    return z_score(spread, spreads) - z_score(vix, vixes)


# ── VIX ladder (never the equity cascade) ─────────────────────────

def _ib_gateway_unavailable() -> bool:
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        _log(f"IB skipped: gateway auth_state={auth_state} (falling back to Cboe/Yahoo)")
        return True
    return False


def fetch_ib_vix() -> Closes:
    """IB Index('VIX','CBOE') 1Y daily TRADES. Not Stock('VIX')."""
    if _ib_gateway_unavailable():
        return {}
    try:
        from ib_insync import IB, Index
    except ImportError:
        return {}

    ib = IB()
    if not _connect_ib_with_retry(ib, client_ids=CREDIT_VIX_IB_HISTORY_CLIENT_IDS):
        return {}

    import asyncio

    async def _fetch() -> Closes:
        contract = Index("VIX", "CBOE")
        try:
            qualified = await asyncio.wait_for(
                ib.qualifyContractsAsync(contract), timeout=IB_REQUEST_TIMEOUT_S
            )
        except Exception as exc:
            _log(f"IB: qualify VIX failed: {exc}")
            return {}
        if not qualified and not getattr(contract, "conId", 0):
            _log("IB: VIX not qualified, falling back")
            return {}
        try:
            bars = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr="1 Y",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                ),
                timeout=IB_HISTORICAL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _log("IB: VIX timed out, falling back")
            return {}
        except Exception as exc:
            _log(f"IB: VIX failed: {exc}")
            return {}
        return {_bar_date(b.date): float(b.close) for b in bars or [] if b.close is not None}

    try:
        parsed = ib.run(_fetch())
    finally:
        ib.disconnect()
    if parsed:
        _log(f"IB: VIX {len(parsed)} bars")
    return parsed or {}


def fetch_cboe_vix() -> Closes:
    """Cboe CDN VIX_History.csv via CboeClient + parse_index_csv CLOSE."""
    try:
        from clients.cboe_client import CboeClient
        client = CboeClient()
    except ImportError:
        return {}
    except Exception as exc:
        # Ctor raises on a rejected key before any heartbeat exists.
        _log(f"Cboe: VIX client failed: {exc}")
        _record_error_health(f"{SERVICE}: Cboe VIX client failed: {exc}", "cboe_client")
        return {}
    try:
        with client:
            text, _stamp = client.fetch_history("VIX")
    except Exception as exc:
        _log(f"Cboe: VIX failed: {exc}")
        return {}
    if not text:
        return {}
    parsed = {row["date"]: float(row["value"]) for row in parse_index_csv(text, "CLOSE")}
    if parsed:
        _log(f"Cboe: VIX {len(parsed)} bars")
    return parsed


def fetch_yahoo_vix() -> Closes:
    """Last-resort Yahoo ^VIX. Never the equity ticker VIX."""
    try:
        parsed = parse_yahoo_chart(fetch_yahoo_chart("^VIX"))
    except Exception as exc:
        _log(f"Yahoo: ^VIX failed: {exc}")
        return {}
    if parsed:
        _log(f"Yahoo: ^VIX {len(parsed)} bars")
    return parsed


def fetch_vix_closes(
    *,
    fetch_ib: Optional[FetchVix] = None,
    fetch_cboe: Optional[FetchVix] = None,
    fetch_yahoo: Optional[FetchVix] = None,
) -> tuple[Closes, str]:
    """IB Index -> Cboe CDN -> Yahoo ^VIX. Returns (closes, source label)."""
    for fetch, label in (
        (fetch_ib or fetch_ib_vix, "ib"),
        (fetch_cboe or fetch_cboe_vix, "cboe"),
        (fetch_yahoo or fetch_yahoo_vix, "yahoo"),
    ):
        series = fetch()
        if series:
            return series, label
    return {}, NO_SOURCE


def fetch_equity_closes(
    tickers: Optional[list[str]] = None,
    *,
    fetch_ib: Optional[FetchCloses] = None,
    fetch_uw: Optional[FetchCloses] = None,
    fetch_rh: Optional[FetchCloses] = None,
    fetch_yahoo: Optional[FetchCloses] = None,
) -> tuple[dict[str, Closes], str, dict[str, str]]:
    """S1: import iei-hyg's equity cascade for SHY/HYG. Never pass VIX."""
    wanted = list(tickers or EQUITY_TICKERS)
    if VIX_SYMBOL in wanted:
        raise ValueError("VIX must not go through the equity cascade")
    return fetch_iei_hyg_closes(
        wanted,
        fetch_ib=fetch_ib,
        fetch_uw=fetch_uw,
        fetch_rh=fetch_rh,
        fetch_yahoo=fetch_yahoo,
    )


# ── series assembly ───────────────────────────────────────────────

def _row(date: str, shy_close: float, hyg_close: float, vix_close: float) -> dict[str, Any]:
    return {
        "date": date,
        "shy_close": shy_close,
        "hyg_close": hyg_close,
        "vix_close": vix_close,
        "spread": shy_close - hyg_close,
    }


def align_series(shy: Closes, hyg: Closes, vix: Closes) -> list[dict[str, Any]]:
    """Inner join on SHY ∩ HYG ∩ VIX session dates."""
    return [
        _row(date, shy[date], hyg[date], vix[date])
        for date in sorted(set(shy) & set(hyg) & set(vix))
        if hyg[date]
    ]


def extremes_window(series: list[dict[str, Any]], n: int = WINDOW_SESSIONS) -> list[dict[str, Any]]:
    return series[-n:]


def session_metrics(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-session rank/gap using the trailing window ending at that session."""
    out: list[dict[str, Any]] = []
    for i, row in enumerate(series):
        window = extremes_window(series[: i + 1])
        spreads = [r["spread"] for r in window]
        vixes = [r["vix_close"] for r in window]
        s_lo, s_hi = min(spreads), max(spreads)
        v_lo, v_hi = min(vixes), max(vixes)
        rank_spread = pct_rank(row["spread"], s_lo, s_hi)
        rank_vix = pct_rank(row["vix_close"], v_lo, v_hi)
        gap = rank_spread - rank_vix
        out.append({
            **row,
            "rank_spread": rank_spread,
            "rank_vix": rank_vix,
            "gap": gap,
            "state": classify_state(gap),
            "window_sessions": len(window),
        })
    return out


def widest_since(metrics: list[dict[str, Any]]) -> Optional[str]:
    """Most recent prior session with gap >= latest gap; None if none."""
    if len(metrics) < 2:
        return None
    latest_gap = metrics[-1]["gap"]
    for row in reversed(metrics[:-1]):
        if row["gap"] >= latest_gap:
            return row["date"]
    return None


def _current(series: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = session_metrics(series)
    latest = metrics[-1]
    return {
        "date": latest["date"],
        "shy_close": latest["shy_close"],
        "hyg_close": latest["hyg_close"],
        "vix_close": latest["vix_close"],
        "spread": latest["spread"],
        "rank_spread": latest["rank_spread"],
        "rank_vix": latest["rank_vix"],
        "gap": latest["gap"],
        "state": latest["state"],
        "widest_since": widest_since(metrics),
        "window_sessions": latest["window_sessions"],
    }


def build_output(
    series: list[dict[str, Any]],
    scan_time: Optional[str] = None,
    source: str = "ib",
    source_by_ticker: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    stamp = scan_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "scan_time": stamp,
        "source": source,
        "source_by_ticker": dict(source_by_ticker or {}),
        "count": len(series),
        "current": _current(series) if series else None,
        "series": series,
    }


def merge_series(cached: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date = {row["date"]: row for row in cached}
    by_date.update({row["date"]: row for row in fresh})
    return [by_date[d] for d in sorted(by_date)]


def _db_fields(row: dict[str, Any]) -> tuple:
    return (row["date"], row["shy_close"], row["hyg_close"], row["vix_close"])


def diff_new_rows(cached: list[dict[str, Any]], series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cached_by_date = {row["date"]: row for row in cached}
    return [
        row
        for row in series
        if row["date"] not in cached_by_date
        or _db_fields(cached_by_date[row["date"]]) != _db_fields(row)
    ]


# ── persistence ───────────────────────────────────────────────────

def _write_json_cache(payload: dict[str, Any]) -> None:
    CREDIT_VIX_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = CREDIT_VIX_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, CREDIT_VIX_JSON)


def _record_error_health(message: str, error_class: str) -> None:
    try:
        writer.ensure_no_replica_for_writers()
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            error={"message": message, "class": error_class},
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"error heartbeat non-fatal: {exc}")


def persist_result(
    payload: dict[str, Any],
    changed_rows: list[dict[str, Any]],
    health_error: Optional[dict[str, Any]] = None,
    *,
    degraded: Optional[dict[str, Any]] = None,
) -> None:
    if not payload["series"]:
        _log("refusing to persist empty series")
        _record_error_health(
            f"{SERVICE}: refusing to persist an empty series", "empty_series"
        )
        return
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if changed_rows:
        writer.upsert_credit_vix_rows(changed_rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(
        SERVICE,
        "ok" if health_error is None else "error",
        finished_at=scan_time,
        error=health_error if health_error is not None else degraded,
    )
    _write_json_cache(payload)


# ── orchestration ─────────────────────────────────────────────────

def _turso_series() -> list[dict[str, Any]]:
    from db.client import get_db

    rows = get_db().execute(
        "SELECT date, shy_close, hyg_close, vix_close "
        "FROM credit_vix_history ORDER BY date"
    ).fetchall()
    return [
        _row(row[0], float(row[1]), float(row[2]), float(row[3]))
        for row in rows
        if row[2]
    ]


def _json_series() -> list[dict[str, Any]]:
    try:
        return json.loads(CREDIT_VIX_JSON.read_text())["series"]
    except (OSError, ValueError, KeyError):
        return []


def load_cached_series(*, no_db: bool = False) -> list[dict[str, Any]]:
    if no_db:
        return _json_series()
    try:
        stored = _turso_series()
        if stored:
            return stored
    except Exception as exc:  # noqa: BLE001
        _log(f"turso rehydrate non-fatal: {exc}")
    return _json_series()


def _serve_cached(cached: list[dict[str, Any]], *, no_db: bool = False) -> dict[str, Any]:
    if not cached:
        raise RuntimeError(
            f"{SERVICE}: IB, Cboe, UW, Robinhood and Yahoo all failed with no cached series"
        )
    payload = {**build_output(cached, source=NO_SOURCE), "status": STATUS_STALE_SOURCE}
    through = payload["current"]["date"]
    _log(f"all sources down; re-serving cached series through {through}")
    health_error = {
        "message": (
            f"{SERVICE}: every source failed; serving the cached series through {through}"
        ),
        "class": "source_down",
    }
    if not no_db:
        persist_result(payload, [], health_error)
    return payload


def run(
    *,
    no_db: bool = False,
    fetch_ib: Optional[FetchCloses] = None,
    fetch_uw: Optional[FetchCloses] = None,
    fetch_rh: Optional[FetchCloses] = None,
    fetch_yahoo: Optional[FetchCloses] = None,
    fetch_vix_ib: Optional[FetchVix] = None,
    fetch_vix_cboe: Optional[FetchVix] = None,
    fetch_vix_yahoo: Optional[FetchVix] = None,
) -> dict[str, Any]:
    _log("fetching SHY/HYG (equity cascade) and VIX (IB -> Cboe -> Yahoo)")
    try:
        equity, equity_source, equity_sources = fetch_equity_closes(
            EQUITY_TICKERS,
            fetch_ib=fetch_ib,
            fetch_uw=fetch_uw,
            fetch_rh=fetch_rh,
            fetch_yahoo=fetch_yahoo,
        )
        vix, vix_source = fetch_vix_closes(
            fetch_ib=fetch_vix_ib,
            fetch_cboe=fetch_vix_cboe,
            fetch_yahoo=fetch_vix_yahoo,
        )
        source_by_ticker = dict(equity_sources)
        if vix_source != NO_SOURCE:
            source_by_ticker[VIX_SYMBOL] = vix_source
        source = combine_source(source_by_ticker) or NO_SOURCE
        cached = load_cached_series(no_db=no_db)
        have_legs = (
            SHY_SYMBOL in equity
            and HYG_SYMBOL in equity
            and bool(vix)
        )
        if source == NO_SOURCE or not have_legs:
            return _serve_cached(cached, no_db=no_db)
        fresh = align_series(
            equity.get(SHY_SYMBOL, {}),
            equity.get(HYG_SYMBOL, {}),
            vix,
        )
        series = merge_series(cached, fresh)
        new_rows = diff_new_rows(cached, series)
        if not new_rows:
            _log("source unchanged; refreshing snapshot only")
        payload = build_output(series, source=source, source_by_ticker=source_by_ticker)
        if no_db:
            _log("--no-db: skipping Turso writes and the JSON mirror")
            return payload
        persist_result(payload, new_rows)
    except Exception as exc:
        if not no_db:
            _record_error_health(f"{SERVICE}: {exc}", "cycle_failed")
        raise
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nCREDIT/VIX: {payload['count']} sessions", file=sys.stderr)
    if current:
        print(
            f"  latest {current['date']} spread {current['spread']:.2f} "
            f"vix {current['vix_close']:.2f} gap {current['gap']:.3f}",
            file=sys.stderr,
        )
        print(f"  state {current['state']}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SHY minus HYG credit proxy vs VIX, 252-session range gap"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--no-db", dest="no_db", action="store_true", help="skip all Turso I/O"
    )
    args = parser.parse_args(argv)

    payload = run(no_db=args.no_db)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())

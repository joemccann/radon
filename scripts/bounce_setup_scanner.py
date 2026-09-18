#!/usr/bin/env python3
"""BOUNCE SETUP scanner. Spec: docs/bounce-setup.md.

Stage 1 ranks the universe by how stretched to the downside each name is,
from Turso daily closes only (zero UW calls). Stage 2 spends UW calls on the
top ``STAGE2_TOP_N`` names: one fixed-strike put's implied vol path and the
30-day 25-delta skew path over the same ``WINDOW`` sessions.

Nominates candidates only. None of its inputs is dark pool or OTC flow, so
each row carries the flow scanner's latest read for that ticker (Gate 2).
Writes ``data/bounce_setup.json`` and mirrors ``scan_snapshots`` service
``bounce-setup``.
"""
from __future__ import annotations

import os

os.environ.setdefault("RADON_UW_CALLER", "bounce-setup")

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from clients.uw_client import UWClient, UWRateLimitError
from strength_confirmation_scanner import resolve_tickers
from utils.scan_health import (
    SCAN_STATUS_BUDGET_BLOCKED,
    SCAN_STATUS_COVERAGE_FAILED,
    next_quota_reset_iso,
    record_scan_degraded,
)
from utils.uw_budget import should_block_universe_scan
from vol_skew_mr_scanner import (
    _as_rows,
    _as_vol_points,
    _parse_option_symbol,
    _to_float,
    _vol_pct,
    bollinger_pct_b,
    fetch_skew_snapshot,
    rsi,
)

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover - DB layer optional in tests/dev
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_CACHE_PATH = _PROJECT_DIR / "data" / "bounce_setup.json"
_FLOW_CACHE_PATH = _PROJECT_DIR / "data" / "scanner.json"

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

SERVICE_NAME = "bounce-setup"
FLOW_SERVICE_NAME = "scanner"  # scripts/scanner.py -> scanner_snapshots
DEFAULT_PRESET = "largecaps"

WINDOW = 20
STAGE2_TOP_N = 30
STRETCH_PCTL_MAX = 10.0
IV_RUNUP_MIN = 2.0
IV_OFF_PEAK_MIN = 0.25
SKEW_EASE_MIN = 0.3
SLOPE_SESSIONS = 3
EXPIRY_DTE_MIN = 30
EXPIRY_DTE_MAX = 45
EXPIRY_DTE_FALLBACK_MIN = 21
RET_Z_LOOKBACK = 252
MIN_CLOSES = RET_Z_LOOKBACK + WINDOW  # 272
_EPS = 1e-9

VERDICTS = ("BOUNCE_SETUP", "WATCH", "STRETCHED")
_VERDICT_RANK = {verdict: idx for idx, verdict in enumerate(VERDICTS)}


# ── pure functions ──────────────────────────────────────────────────


def occ_symbol(ticker: str, expiry: str, right: str, strike: float) -> str:
    exp = date.fromisoformat(expiry)
    return f"{ticker.upper()}{exp:%y%m%d}{right.upper()}{int(round(strike * 1000)):08d}"


def _is_monthly(day: date) -> bool:
    return day.weekday() == 4 and 15 <= day.day <= 21


def pick_expiry(expiries: Sequence[str], as_of: date) -> Optional[str]:
    """Monthly (third-Friday) expiry with 30-45 DTE, else nearest monthly >= 21 DTE."""
    monthlies: List[tuple[int, str]] = []
    for raw in expiries:
        try:
            day = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if _is_monthly(day):
            monthlies.append(((day - as_of).days, day.isoformat()))
    in_window = [row for row in monthlies if EXPIRY_DTE_MIN <= row[0] <= EXPIRY_DTE_MAX]
    if in_window:
        return min(in_window)[1]
    fallback = [row for row in monthlies if row[0] >= EXPIRY_DTE_FALLBACK_MIN]
    return min(fallback)[1] if fallback else None


def select_fixed_strike(close: float, strikes: Sequence[float]) -> Optional[float]:
    """Listed strike nearest ``close``; a tie goes to the lower strike."""
    if not strikes:
        return None
    return min(strikes, key=lambda strike: (abs(strike - close), strike))


def _slope(values: Sequence[float]) -> float:
    prior = values[-SLOPE_SESSIONS - 1:-1]
    return values[-1] - sum(prior) / len(prior)


def vol_plateau(fs_iv: Sequence[float]) -> Dict[str, Any]:
    """Fixed-strike vol has run up, given back part of it, and stopped rising."""
    values = [float(v) for v in fs_iv]
    if len(values) < SLOPE_SESSIONS + 1:
        return {"runup": None, "off_peak": None, "slope": None, "pass": None}
    peak = max(values)
    runup = peak - values[0]
    off_peak = (peak - values[-1]) / runup if runup > 0 else 0.0
    slope = _slope(values)
    passed = (
        runup >= IV_RUNUP_MIN - _EPS
        and off_peak >= IV_OFF_PEAK_MIN - _EPS
        and slope <= _EPS
    )
    return {"runup": runup, "off_peak": off_peak, "slope": slope, "pass": passed}


def skew_easing(skew: Sequence[float]) -> Dict[str, Any]:
    """Skew sits below its window max and is still falling (strict)."""
    values = [float(v) for v in skew]
    if len(values) < SLOPE_SESSIONS + 1:
        return {"ease": None, "slope": None, "pass": None}
    ease = max(values) - values[-1]
    slope = _slope(values)
    passed = ease >= SKEW_EASE_MIN - _EPS and slope < -_EPS
    return {"ease": ease, "slope": slope, "pass": passed}


def _percentile_ranks(values: Mapping[str, float]) -> Dict[str, float]:
    ordered = sorted(values, key=lambda ticker: (values[ticker], ticker))
    n = len(ordered)
    return {ticker: (idx / (n - 1) * 100 if n > 1 else 0.0) for idx, ticker in enumerate(ordered)}


def rank_stretch(scores: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Rank 1 = most stretched to the downside. Names missing a metric drop out."""
    complete = {
        ticker: row for ticker, row in scores.items()
        if all(_to_float(row.get(key)) is not None for key in ("rsi", "pct_b", "ret_z"))
    }
    if not complete:
        return []
    per_metric = [
        _percentile_ranks({ticker: float(row[key]) for ticker, row in complete.items()})
        for key in ("rsi", "pct_b", "ret_z")
    ]
    stretch_score = {
        ticker: sum(ranks[ticker] for ranks in per_metric) / len(per_metric) for ticker in complete
    }
    pctl = _percentile_ranks(stretch_score)
    ordered = sorted(complete, key=lambda ticker: (stretch_score[ticker], ticker))
    return [
        {
            **dict(complete[ticker]),
            "ticker": ticker,
            "stretch_rank": idx,
            "stretch_score": round(stretch_score[ticker], 2),
            "stretch_pctl": round(pctl[ticker], 1),
        }
        for idx, ticker in enumerate(ordered, start=1)
    ]


def stage2_tickers(ranked: Sequence[Mapping[str, Any]], top_n: int = STAGE2_TOP_N) -> List[str]:
    return [str(row["ticker"]) for row in list(ranked)[:top_n]]


def classify(stretch_pctl: float, vol: Mapping[str, Any], skew: Mapping[str, Any]) -> Optional[str]:
    if stretch_pctl > STRETCH_PCTL_MAX:
        return None
    passes = sum(1 for leg in (vol, skew) if leg.get("pass") is True)
    return VERDICTS[2 - passes]


def ret_z(closes: Sequence[float]) -> Optional[float]:
    """z-score of the latest WINDOW-session return vs the prior 252 rolling returns."""
    if len(closes) < MIN_CLOSES:
        return None
    tail = list(closes)[-MIN_CLOSES:]
    returns = [tail[i] / tail[i - WINDOW] - 1 for i in range(WINDOW, len(tail))]
    latest, prior = returns[-1], returns[:-1]
    mean = sum(prior) / len(prior)
    std = math.sqrt(sum((r - mean) ** 2 for r in prior) / len(prior))
    if std == 0:
        return None
    return (latest - mean) / std


def stretch_metrics(closes: Sequence[float]) -> Optional[Dict[str, Any]]:
    """Stage-1 metrics for one name, or None when history is too short."""
    z = ret_z(closes)
    if z is None:
        return None
    return {
        "rsi": rsi(closes),
        "pct_b": bollinger_pct_b(closes),
        "ret_z": z,
        "ret_20d": (closes[-1] / closes[-WINDOW - 1] - 1) * 100,
    }


def flow_for(ticker: str, flow_payload: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The flow scanner's read for ``ticker``: its dark-pool direction and score."""
    if not isinstance(flow_payload, Mapping):
        return None
    rows = flow_payload.get("top_signals") or flow_payload.get("results") or []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("ticker") or "").upper() == ticker.upper():
            direction = str(row.get("direction") or "UNKNOWN").upper()
            return {"signal": direction, "score": _to_float(row.get("score"))}
    return None


# ── IO seams ────────────────────────────────────────────────────────


def completed_cutoff(now: Optional[datetime] = None) -> str:
    """Latest session date allowed in stage 1: today only after the 16:00 ET close."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # pragma: no cover
        et = now - timedelta(hours=4)
    today = et.date()
    return today.isoformat() if et.hour >= 16 else (today - timedelta(days=1)).isoformat()


def load_closes(tickers: Sequence[str], cutoff: str) -> Dict[str, List[tuple[str, float]]]:
    from db.readers import read_price_history_closes

    since = (date.fromisoformat(cutoff) - timedelta(days=int(MIN_CLOSES * 1.6))).isoformat()
    raw = read_price_history_closes(list(tickers), since=since)
    return {
        symbol: sorted((d, c) for d, c in by_date.items() if d <= cutoff)
        for symbol, by_date in raw.items()
    }


def load_flow_payload() -> Optional[Dict[str, Any]]:
    """Latest flow-scanner snapshot: Turso scanner_snapshots, then data/scanner.json."""
    try:
        from db.readers import _db, _cell
        row = _db(None).execute(
            "SELECT payload FROM scanner_snapshots ORDER BY scan_time DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            payload = json.loads(_cell(row, 0, "payload"))
            if isinstance(payload, dict):
                return payload
    except Exception as exc:  # noqa: BLE001 - flow join is best-effort
        print(f"  flow snapshot unavailable from Turso ({exc})", file=sys.stderr)
    return _read_cache_file(_FLOW_CACHE_PATH)


def _put_strikes(payload: Any, expiry: str) -> List[float]:
    strikes = set()
    for row in _as_rows(payload):
        symbol = str(row.get("option_symbol") or row.get("symbol") or "")
        parsed_expiry, parsed_strike, parsed_right = _parse_option_symbol(symbol)
        right = str(row.get("option_type") or parsed_right or "").upper()[:1]
        row_expiry = str(row.get("expiry") or parsed_expiry or "")[:10]
        if len(row_expiry) == 8:
            row_expiry = f"{row_expiry[:4]}-{row_expiry[4:6]}-{row_expiry[6:]}"
        strike = _to_float(row.get("strike")) or parsed_strike
        if right == "P" and strike and row_expiry == expiry:
            strikes.add(float(strike))
    return sorted(strikes)


def _historic_iv(payload: Any) -> Dict[str, float]:
    rows = payload.get("chains", []) if isinstance(payload, dict) else []
    out: Dict[str, float] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        iv = _vol_pct(row.get("implied_volatility"))
        if day and iv is not None:
            out[day] = iv
    return out


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def stage2_row(
    client: Any,
    ranked_row: Mapping[str, Any],
    closes: Sequence[tuple[str, float]],
    as_of: date,
    flow_payload: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Vol and skew legs for one stretched name. Rate limits propagate."""
    ticker = str(ranked_row["ticker"])
    errors: List[str] = []

    def fetch(label: str, func, default: Any) -> Any:
        try:
            return func()
        except UWRateLimitError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade the leg, not the row
            errors.append(f"{label}:{exc}")
            return default

    window = list(closes)[-WINDOW:]
    dates = [d for d, _ in window]
    start_close = window[0][1]

    contract = None
    fs_by_date: Dict[str, float] = {}
    breakdown = fetch("expiry", lambda: client.get_expiry_breakdown(ticker), {"data": []})
    expiry = pick_expiry(
        [str(row.get("expiry") or row.get("expires") or "") for row in _as_rows(breakdown)], as_of,
    )
    if expiry is None:
        errors.append("expiry:no_monthly")
    else:
        strikes = _put_strikes(
            fetch("contracts", lambda: client.get_option_contracts(ticker, expiry=expiry, option_type="put"), {"data": []}),
            expiry,
        )
        strike = select_fixed_strike(start_close, strikes)
        if strike is None:
            errors.append("contracts:no_put_strikes")
        else:
            symbol = occ_symbol(ticker, expiry, "P", strike)
            contract = {"symbol": symbol, "expiry": expiry, "strike": strike}
            fs_by_date = _historic_iv(fetch("historic", lambda: client.get_option_contract_historic(symbol), {}))

    fs_series = [fs_by_date[d] for d in dates if d in fs_by_date]
    vol = vol_plateau(fs_series)

    skew_snap = fetch_skew_snapshot(client, ticker, as_of, sessions_count=WINDOW)
    errors.extend(skew_snap["errors"])
    skew_dates = [row["date"] for row in skew_snap["sessions"]]
    skew_values = _as_vol_points([row["value"] for row in skew_snap["sessions"]])
    skew_by_date = dict(zip(skew_dates, skew_values))
    skew = skew_easing(skew_values)

    verdict = classify(float(ranked_row["stretch_pctl"]), vol, skew)
    if verdict is None:
        return None
    fs_base = fs_series[0] if fs_series else None
    series = [
        {
            "date": d,
            "spot_cum_pct": round((c / start_close - 1) * 100, 2),
            "fs_iv_change": _r(fs_by_date[d] - fs_base) if d in fs_by_date and fs_base is not None else None,
            "skew30": _r(skew_by_date.get(d), 4),
        }
        for d, c in window
    ]
    return {
        "ticker": ticker,
        "verdict": verdict,
        "stretch_rank": ranked_row["stretch_rank"],
        "stretch_pctl": ranked_row["stretch_pctl"],
        "rsi": _r(ranked_row.get("rsi")),
        "pct_b": _r(ranked_row.get("pct_b"), 4),
        "ret_z": _r(ranked_row.get("ret_z")),
        "ret_20d": _r(ranked_row.get("ret_20d")),
        "contract": contract,
        "vol": {"runup": _r(vol["runup"]), "off_peak": _r(vol["off_peak"], 4), "slope": _r(vol["slope"]), "pass": vol["pass"]},
        "skew": {"ease": _r(skew["ease"], 4), "slope": _r(skew["slope"], 4), "pass": skew["pass"]},
        "series": series,
        "flow": flow_for(ticker, flow_payload),
        "errors": errors,
    }


def build_output(
    results: Sequence[Mapping[str, Any]],
    *,
    as_of: Optional[str],
    universe: str,
    coverage: Mapping[str, int],
    scan_time: Optional[str] = None,
) -> Dict[str, Any]:
    ordered = sorted(
        results,
        key=lambda row: (_VERDICT_RANK.get(row.get("verdict"), 9), row.get("stretch_pctl") or 0.0, row.get("ticker")),
    )
    return {
        "scan_time": scan_time or datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "window": WINDOW,
        "universe": universe,
        "coverage": {
            "tickers": int(coverage.get("tickers", 0)),
            "ranked": int(coverage.get("ranked", 0)),
            "excluded_short_history": int(coverage.get("excluded_short_history", 0)),
            "stage2": int(coverage.get("stage2", 0)),
        },
        "bounce_count": sum(1 for row in ordered if row.get("verdict") == "BOUNCE_SETUP"),
        "results": [dict(row) for row in ordered],
    }


def _universe_label(source: str) -> str:
    return source.split(":", 1)[1] if source.startswith("preset:") else source


def scan_universe(
    tickers: Sequence[str],
    preset: Optional[str] = DEFAULT_PRESET,
    limit: Optional[int] = None,
    client: Any = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    resolved, source = resolve_tickers(tickers, preset)
    if limit is not None and limit > 0:
        resolved = resolved[:limit]
    universe = _universe_label(source)
    cutoff = completed_cutoff(now)

    closes = load_closes(resolved, cutoff)
    scores: Dict[str, Dict[str, Any]] = {}
    for ticker in resolved:
        series = closes.get(ticker) or []
        metrics = stretch_metrics([c for _, c in series])
        if metrics is not None:
            scores[ticker] = metrics
    ranked = rank_stretch(scores)
    excluded = len(resolved) - len(ranked)
    as_of_str = max((closes[row["ticker"]][-1][0] for row in ranked), default=None)
    coverage = {"tickers": len(resolved), "ranked": len(ranked), "excluded_short_history": excluded, "stage2": 0}

    picked = [row for row in ranked[:STAGE2_TOP_N] if row["stretch_pctl"] <= STRETCH_PCTL_MAX]
    if picked and not tickers and should_block_universe_scan():
        print(f"UW daily budget block; skipping stage 2 ({source})", file=sys.stderr)
        record_scan_degraded(
            SERVICE_NAME,
            SCAN_STATUS_BUDGET_BLOCKED,
            f"UW daily budget block; bounce-setup stage 2 skipped ({source})",
            next_attempt_at=next_quota_reset_iso(),
        )
        prior = _read_cache_file(_CACHE_PATH)
        if prior and prior.get("results"):
            return {**prior, "scan_status": SCAN_STATUS_BUDGET_BLOCKED}
        blocked = build_output([], as_of=as_of_str, universe=universe, coverage=coverage)
        blocked["scan_status"] = SCAN_STATUS_BUDGET_BLOCKED
        return blocked

    results: List[Dict[str, Any]] = []
    failed = 0
    if picked:
        as_of = date.fromisoformat(as_of_str) if as_of_str else (now or datetime.now(timezone.utc)).date()
        flow_payload = load_flow_payload()
        own_client = client is None
        client = client or UWClient()
        try:
            for row in picked:
                try:
                    out = stage2_row(client, row, closes[row["ticker"]], as_of, flow_payload)
                except UWRateLimitError:
                    failed += 1
                    print(f"  {row['ticker']} - SKIP (rate limited)", file=sys.stderr)
                    continue
                except Exception as exc:  # noqa: BLE001 - per-ticker isolation
                    failed += 1
                    print(f"  {row['ticker']} - ERROR ({exc})", file=sys.stderr)
                    continue
                if out is not None:
                    print(f"  {row['ticker']} - {out['verdict']}", file=sys.stderr)
                    results.append(out)
        finally:
            if own_client:
                client.close()
    coverage["stage2"] = len(picked)

    payload = build_output(results, as_of=as_of_str, universe=universe, coverage=coverage)
    if not tickers and (not ranked or (picked and failed == len(picked))):
        payload["scan_status"] = SCAN_STATUS_COVERAGE_FAILED
        record_scan_degraded(
            SERVICE_NAME,
            SCAN_STATUS_COVERAGE_FAILED,
            f"bounce-setup coverage failed; ranked {len(ranked)}/{len(resolved)}, "
            f"stage 2 failed {failed}/{len(picked)} ({source})",
        )
    return payload


def _read_cache_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_cache(payload: Dict[str, Any], path: Path = _CACHE_PATH) -> bool:
    """Persist unless the run degraded (budget block / coverage failure)."""
    if payload.get("scan_status"):
        print("Degraded bounce-setup scan; preserving last good cache", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    mirror_scan_snapshot(SERVICE_NAME, payload)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank downside-stretched names whose put vol and skew are fading.")
    parser.add_argument("tickers", nargs="*", help="Tickers to scan. Overrides --preset.")
    parser.add_argument("--preset", default=DEFAULT_PRESET, help=f"Preset name (default: {DEFAULT_PRESET}).")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for smoke runs.")
    parser.add_argument("--json", action="store_true", help="Print JSON payload to stdout.")
    parser.add_argument("--output", default=str(_CACHE_PATH), help="Cache output path.")
    args = parser.parse_args()

    payload = scan_universe(args.tickers, preset=args.preset, limit=args.limit)
    if not args.tickers:
        save_cache(payload, Path(args.output))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Bounce setup scan complete: {payload['bounce_count']} setups / {len(payload['results'])} rows")


if __name__ == "__main__":
    main()

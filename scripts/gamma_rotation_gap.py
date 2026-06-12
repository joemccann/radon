#!/usr/bin/env python3
"""SPY/TLT Gamma Rotation Gap scanner.

Builds a VCG-style cross-asset gamma divergence signal from Unusual Whales
SPY and TLT Greek-exposure history, then stores the snapshot in Turso.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

HISTORY_DAYS = 90
Z_WINDOW = 63
MIN_OBSERVATIONS = 70
PRIMARY_TICKERS = ("SPY", "TLT")
SERVICE_NAME = "gamma-rotation-scan"
CACHE_PATH = _PROJECT_DIR / "data" / "gamma_rotation_gap.json"


def _load_local_env() -> None:
    for path in (_PROJECT_DIR / "web" / ".env", _PROJECT_DIR / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _zscore_series(values: Iterable[float], window: int = Z_WINDOW) -> np.ndarray:
    arr = np.array(list(values), dtype=float)
    out = np.full(len(arr), np.nan)
    for idx in range(len(arr)):
        start = max(0, idx - window + 1)
        chunk = arr[start : idx + 1]
        valid = chunk[np.isfinite(chunk)]
        if len(valid) < 10:
            continue
        sigma = float(np.std(valid, ddof=1))
        if sigma < 1e-12:
            continue
        out[idx] = (arr[idx] - float(np.mean(valid))) / sigma
    return out


def _slope(values: List[float], length: int = 3) -> Optional[float]:
    valid = [v for v in values if math.isfinite(v)]
    if len(valid) < length + 1:
        return None
    return valid[-1] - valid[-1 - length]


def is_market_open() -> bool:
    import zoneinfo

    try:
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(et)
    except Exception:
        now = datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def _asset_state(net_gamma: float) -> str:
    if net_gamma > 0:
        return "CUSHION"
    if net_gamma < 0:
        return "WHIP"
    return "NEUTRAL"


def _pair_state(spy_gamma: float, tlt_gamma: float) -> str:
    if spy_gamma > 0 and tlt_gamma < 0:
        return "RISK_ON_DIVERGENCE"
    if spy_gamma < 0 and tlt_gamma > 0:
        return "RISK_OFF_DIVERGENCE"
    if spy_gamma > 0 and tlt_gamma > 0:
        return "DUAL_CUSHION"
    if spy_gamma < 0 and tlt_gamma < 0:
        return "DUAL_WHIP"
    return "NEUTRAL"


def _state_label(state: str) -> str:
    return {
        "RISK_ON_DIVERGENCE": "Risk-on divergence",
        "RISK_OFF_DIVERGENCE": "Risk-off divergence",
        "DUAL_CUSHION": "Dual cushion",
        "DUAL_WHIP": "Dual whip",
        "NEUTRAL": "Neutral",
    }.get(state, state)


def _classify_signal(
    grg_z: Optional[float],
    spy_gamma: float,
    tlt_gamma: float,
    spy_slope_3d: Optional[float],
    spy_flip_gap_pct: Optional[float],
) -> Dict[str, Any]:
    state = _pair_state(spy_gamma, tlt_gamma)
    z = grg_z if grg_z is not None and math.isfinite(grg_z) else 0.0

    top_gates = [
        z >= 2.0,
        spy_gamma > 0,
        spy_slope_3d is not None and spy_slope_3d < 0,
        state == "RISK_ON_DIVERGENCE",
        spy_flip_gap_pct is not None and spy_flip_gap_pct > 0,
    ]
    bottom_gates = [
        z <= -2.0,
        spy_gamma < 0,
        spy_slope_3d is not None and spy_slope_3d > 0,
        state == "RISK_OFF_DIVERGENCE",
        spy_flip_gap_pct is not None and spy_flip_gap_pct > 0,
    ]
    top_score = sum(1 for gate in top_gates if gate)
    bottom_score = sum(1 for gate in bottom_gates if gate)

    if state == "DUAL_WHIP":
        interpretation = "DUAL_WHIP"
        tier = 2 if abs(z) >= 2 else 3
    elif state == "RISK_ON_DIVERGENCE" and z >= 2.5:
        interpretation = "TOP_WATCH"
        tier = 1 if top_score >= 4 else 2
    elif state == "RISK_ON_DIVERGENCE":
        interpretation = "RISK_ON"
        tier = 3
    elif state == "RISK_OFF_DIVERGENCE" and z <= -2.5:
        interpretation = "BOTTOM_WATCH"
        tier = 1 if bottom_score >= 4 else 2
    elif state == "RISK_OFF_DIVERGENCE":
        interpretation = "RISK_OFF"
        tier = 3
    elif state == "DUAL_CUSHION":
        interpretation = "CUSHION"
        tier = None
    else:
        interpretation = "NORMAL"
        tier = None

    return {
        "state": state,
        "state_label": _state_label(state),
        "interpretation": interpretation,
        "tier": tier,
        "top_watch": interpretation == "TOP_WATCH" or top_score >= 4,
        "bottom_watch": interpretation == "BOTTOM_WATCH" or bottom_score >= 4,
        "top_score": top_score,
        "bottom_score": bottom_score,
    }


def _gate_rows(
    z: Optional[float],
    spy_gamma: float,
    tlt_gamma: float,
    spy_slope_3d: Optional[float],
    spy_flip_gap_pct: Optional[float],
) -> List[Dict[str, str]]:
    z_val = z if z is not None and math.isfinite(z) else 0.0
    return [
        {
            "id": "polarity",
            "label": "Polarity",
            "status": "PASS" if spy_gamma > 0 and tlt_gamma < 0 else "WATCH",
            "copy": "SPY positive and TLT negative identifies the clean risk-on divergence.",
        },
        {
            "id": "magnitude",
            "label": "Magnitude",
            "status": "PASS" if abs(z_val) >= 2 else "WATCH",
            "copy": "Absolute GRG above 2σ means the cross-asset gamma spread is statistically stretched.",
        },
        {
            "id": "spy_cushion",
            "label": "SPY cushion",
            "status": "PASS" if spy_gamma > 0 else "FAIL",
            "copy": "Positive SPY gamma means dealer hedging is mechanically dampening equity moves.",
        },
        {
            "id": "duration_whip",
            "label": "TLT whip",
            "status": "PASS" if tlt_gamma < 0 else "WATCH",
            "copy": "Negative TLT gamma means duration moves are mechanically amplified.",
        },
        {
            "id": "decay",
            "label": "Decay",
            "status": "PASS" if spy_slope_3d is not None and spy_slope_3d < 0 else "WATCH",
            "copy": "A negative 3-session SPY gamma slope marks possible equity cushion decay.",
        },
        {
            "id": "flip",
            "label": "Flip",
            "status": "PASS" if spy_flip_gap_pct is not None and spy_flip_gap_pct > 0 else "WATCH",
            "copy": "Spot above the SPY gamma flip keeps the equity cushion valid.",
        },
    ]


def _fetch_spot(client: Any, ticker: str) -> Optional[float]:
    try:
        data = client.get_stock_info(ticker)
        info = data.get("data", [{}])
        if isinstance(info, list) and info:
            info = info[0]
        for key in ("last", "price", "close"):
            if info.get(key) is not None:
                return _f(info.get(key))
    except Exception:
        pass
    try:
        rows = client.get_iv_rank(ticker).get("data", [])
        if rows:
            latest = max(rows, key=lambda row: row.get("date", ""))
            if latest.get("close") is not None:
                return _f(latest.get("close"))
    except Exception:
        pass
    return None


def _build_levels(ticker: str, strike_rows: List[Dict[str, Any]], spot: Optional[float]) -> Dict[str, Any]:
    if spot is None:
        return {"gex_flip": None, "max_magnet": None, "max_accelerator": None, "put_wall": None, "call_wall": None}
    try:
        from gex_scan import _bucket_size_for, bucket_profile, compute_gex_flip, find_key_levels

        parsed = []
        for row in strike_rows:
            call_gex = _f(row.get("call_gex"))
            put_gex = _f(row.get("put_gex"))
            parsed.append(
                {
                    "strike": _f(row.get("strike")),
                    "call_gex": call_gex,
                    "put_gex": put_gex,
                    "net_gex": call_gex + put_gex,
                    "call_delta": _f(row.get("call_delta")),
                    "put_delta": _f(row.get("put_delta")),
                    "net_delta": _f(row.get("call_delta")) + _f(row.get("put_delta")),
                }
            )
        profile = bucket_profile(parsed, _bucket_size_for(ticker, spot), spot)
        levels = find_key_levels(profile, spot)
        flip = compute_gex_flip(profile, spot)
        levels["gex_flip"] = (
            {
                "strike": flip,
                "gamma": 0.0,
                "distance": round(flip - spot, 2),
                "distance_pct": round((flip - spot) / spot * 100, 2),
            }
            if flip is not None
            else None
        )
        return levels
    except Exception:
        return {"gex_flip": None, "max_magnet": None, "max_accelerator": None, "put_wall": None, "call_wall": None}


def _history_by_date(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        date = row.get("date")
        if not date:
            continue
        call_gamma = _f(row.get("call_gamma", row.get("call_gex")))
        put_gamma = _f(row.get("put_gamma", row.get("put_gex")))
        call_delta = _f(row.get("call_delta"))
        put_delta = _f(row.get("put_delta"))
        out[str(date)] = {
            "net_gamma": call_gamma + put_gamma,
            "call_gamma": call_gamma,
            "put_gamma": put_gamma,
            "net_delta": call_delta + put_delta,
        }
    return out


def _current_strike_totals(strike_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    call_gex = sum(_f(row.get("call_gex")) for row in strike_rows)
    put_gex = sum(_f(row.get("put_gex")) for row in strike_rows)
    return {
        "date": str(strike_rows[0].get("date")) if strike_rows else None,
        "call_gex": call_gex,
        "put_gex": put_gex,
        "net_gex": call_gex + put_gex,
        "strikes": len(strike_rows),
    }


def compute_gamma_rotation(
    spy_history_rows: List[Dict[str, Any]],
    tlt_history_rows: List[Dict[str, Any]],
    spy_strike_rows: List[Dict[str, Any]],
    tlt_strike_rows: List[Dict[str, Any]],
    spy_spot: Optional[float] = None,
    tlt_spot: Optional[float] = None,
    scan_time: Optional[str] = None,
    market_open: bool = False,
) -> Dict[str, Any]:
    spy_history = _history_by_date(spy_history_rows)
    tlt_history = _history_by_date(tlt_history_rows)
    dates = sorted(set(spy_history) & set(tlt_history))
    if len(dates) < MIN_OBSERVATIONS:
        raise ValueError(f"Only {len(dates)} aligned observations; need {MIN_OBSERVATIONS}")

    spy_values = [spy_history[date]["net_gamma"] for date in dates]
    tlt_values = [tlt_history[date]["net_gamma"] for date in dates]
    spy_z = _zscore_series(spy_values)
    tlt_z = _zscore_series(tlt_values)
    spread = spy_z - tlt_z
    grg_z = _zscore_series(spread)

    history: List[Dict[str, Any]] = []
    for idx, date in enumerate(dates):
        spy_gamma = spy_values[idx]
        tlt_gamma = tlt_values[idx]
        history.append(
            {
                "date": date,
                "spy_net_gamma": _round(spy_gamma, 4),
                "tlt_net_gamma": _round(tlt_gamma, 4),
                "spy_gamma_z": _round(float(spy_z[idx])) if math.isfinite(float(spy_z[idx])) else None,
                "tlt_gamma_z": _round(float(tlt_z[idx])) if math.isfinite(float(tlt_z[idx])) else None,
                "grg_z": _round(float(grg_z[idx])) if math.isfinite(float(grg_z[idx])) else None,
                "raw_spread": _round(float(spread[idx])) if math.isfinite(float(spread[idx])) else None,
                "state": _pair_state(spy_gamma, tlt_gamma),
            }
        )

    latest_idx = len(dates) - 1
    latest_date = dates[-1]
    spy_current_gamma = spy_values[-1]
    tlt_current_gamma = tlt_values[-1]
    latest_grg = float(grg_z[latest_idx]) if math.isfinite(float(grg_z[latest_idx])) else None
    latest_spread = float(spread[latest_idx]) if math.isfinite(float(spread[latest_idx])) else None
    spy_slope_3d = _slope(spy_values, 3)
    tlt_slope_3d = _slope(tlt_values, 3)
    spy_levels = _build_levels("SPY", spy_strike_rows, spy_spot)
    tlt_levels = _build_levels("TLT", tlt_strike_rows, tlt_spot)
    spy_flip = spy_levels.get("gex_flip")
    spy_flip_gap_pct = spy_flip.get("distance_pct") * -1 if isinstance(spy_flip, dict) else None

    classification = _classify_signal(latest_grg, spy_current_gamma, tlt_current_gamma, spy_slope_3d, spy_flip_gap_pct)
    gates = _gate_rows(latest_grg, spy_current_gamma, tlt_current_gamma, spy_slope_3d, spy_flip_gap_pct)
    spy_totals = _current_strike_totals(spy_strike_rows)
    tlt_totals = _current_strike_totals(tlt_strike_rows)

    def _asset(ticker: str, spot: Optional[float], values: List[float], z_values: np.ndarray, slope_3d: Optional[float], totals: Dict[str, Any], levels: Dict[str, Any]) -> Dict[str, Any]:
        latest_gamma = values[-1]
        flip = levels.get("gex_flip")
        flip_gap_pct = flip.get("distance_pct") * -1 if isinstance(flip, dict) else None
        return {
            "ticker": ticker,
            "spot": _round(spot, 4),
            "data_date": latest_date,
            "strike_data_date": totals.get("date"),
            "net_gamma": _round(latest_gamma, 4),
            "net_gex": _round(totals["net_gex"], 4),
            "call_gex": _round(totals["call_gex"], 4),
            "put_gex": _round(totals["put_gex"], 4),
            "net_delta": _round(spy_history[latest_date]["net_delta"] if ticker == "SPY" else tlt_history[latest_date]["net_delta"], 4),
            "gamma_z": _round(float(z_values[-1])) if math.isfinite(float(z_values[-1])) else None,
            "gamma_1d_change": _round(values[-1] - values[-2], 4),
            "gamma_3d_change": _round(slope_3d, 4),
            "state": _asset_state(latest_gamma),
            "levels": levels,
            "spot_vs_flip_pct": _round(flip_gap_pct, 4),
        }

    signal = {
        **classification,
        "grg_z": _round(latest_grg, 4),
        "raw_spread": _round(latest_spread, 4),
        "spy_gamma_z": _round(float(spy_z[-1])) if math.isfinite(float(spy_z[-1])) else None,
        "tlt_gamma_z": _round(float(tlt_z[-1])) if math.isfinite(float(tlt_z[-1])) else None,
        "spy_3d_gamma_change": _round(spy_slope_3d, 4),
        "tlt_3d_gamma_change": _round(tlt_slope_3d, 4),
        "summary": _summary_copy(classification["interpretation"], classification["state"]),
    }

    return {
        "scan_time": scan_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market_open": market_open,
        "data_date": latest_date,
        "source": "Unusual Whales",
        "storage": "turso",
        "lookback_days": len(dates),
        "z_window": Z_WINDOW,
        "signal": signal,
        "assets": {
            "SPY": _asset("SPY", spy_spot, spy_values, spy_z, spy_slope_3d, spy_totals, spy_levels),
            "TLT": _asset("TLT", tlt_spot, tlt_values, tlt_z, tlt_slope_3d, tlt_totals, tlt_levels),
        },
        "gates": gates,
        "history": history[-HISTORY_DAYS:],
        "top_bottom": {
            "top": {
                "active": bool(signal["top_watch"]),
                "copy": "Potential top: stretched positive GRG, positive SPY gamma, equity cushion decay, and duration gamma stress.",
            },
            "bottom": {
                "active": bool(signal["bottom_watch"]),
                "copy": "Potential bottom: stretched negative GRG, SPY gamma repair, and recapture of the SPY gamma flip after stress.",
            },
        },
    }


def _summary_copy(interpretation: str, state: str) -> str:
    if interpretation == "TOP_WATCH":
        return "SPY gamma support is stretched while TLT gamma remains mechanically fragile. Treat upside chase as late-cycle until SPY support refreshes."
    if interpretation == "BOTTOM_WATCH":
        return "SPY gamma stress is stretched and repair conditions are forming. Watch for spot recapturing the gamma flip before calling a bottom."
    if state == "RISK_ON_DIVERGENCE":
        return "SPY gamma is cushioning equities while TLT gamma is amplifying duration moves."
    if state == "RISK_OFF_DIVERGENCE":
        return "SPY gamma is amplifying equity moves while TLT gamma is cushioning duration."
    if state == "DUAL_WHIP":
        return "Both SPY and TLT are short gamma. Cross-asset moves can gap because dealers amplify both sides."
    if state == "DUAL_CUSHION":
        return "Both SPY and TLT are positive gamma. Dealer hedging is dampening both equity and duration moves."
    return "Cross-asset gamma is near neutral."


def fetch_and_build() -> Dict[str, Any]:
    _load_local_env()
    from clients.uw_client import UWClient

    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    client = UWClient()
    try:
        spy_history = client.get_greek_exposure("SPY").get("data", [])
        tlt_history = client.get_greek_exposure("TLT").get("data", [])
        spy_strikes = client.get_greek_exposure_by_strike("SPY").get("data", [])
        tlt_strikes = client.get_greek_exposure_by_strike("TLT").get("data", [])
        spy_spot = _fetch_spot(client, "SPY")
        tlt_spot = _fetch_spot(client, "TLT")
    finally:
        if hasattr(client, "close"):
            client.close()

    return compute_gamma_rotation(
        spy_history_rows=spy_history,
        tlt_history_rows=tlt_history,
        spy_strike_rows=spy_strikes,
        tlt_strike_rows=tlt_strikes,
        spy_spot=spy_spot,
        tlt_spot=tlt_spot,
        scan_time=started,
        market_open=is_market_open(),
    )


def persist_snapshot(payload: Dict[str, Any], *, write_json: bool = True, write_db: bool = True) -> None:
    if write_json:
        from utils.atomic_io import atomic_save

        atomic_save(str(CACHE_PATH), payload)
    if write_db:
        try:
            from db.service_cycle import service_cycle
            from db.writer import upsert_gamma_rotation_snapshot

            with service_cycle(SERVICE_NAME, market_hours_class="on-demand") as cycle:
                cycle.finished_at = payload["scan_time"]
                upsert_gamma_rotation_snapshot(payload["scan_time"], payload)
        except Exception as exc:
            print(f"  DB persist failed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute SPY/TLT Gamma Rotation Gap")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--no-db", action="store_true", help="Do not persist to Turso")
    parser.add_argument("--no-cache", action="store_true", help="Do not write JSON fallback cache")
    parser.add_argument("--force", action="store_true", help="Fetch fresh even when the market is closed (bypass off-hours cache gate)")
    args = parser.parse_args()

    # Off-hours cache gate: when the market is closed and the cached snapshot is
    # already for the most-recent session, serve it and skip the UW fetch.
    try:
        from utils.scan_cache_gate import cached_scan_if_fresh

        cached = cached_scan_if_fresh(CACHE_PATH, force=args.force)
    except Exception:
        cached = None
    if cached is not None:
        if args.json:
            print(json.dumps(cached, indent=2))
        else:
            print(f"GRG (cached, market closed) — {cached.get('scan_time', '')}", file=sys.stderr)
        return 0

    t0 = time.time()
    try:
        payload = fetch_and_build()
        persist_snapshot(payload, write_json=not args.no_cache, write_db=not args.no_db)
    except Exception as exc:
        try:
            from db.service_cycle import record_failed_cycle

            record_failed_cycle(SERVICE_NAME, exc)
        except Exception:
            pass
        print(f"Gamma Rotation Gap scan failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        signal = payload["signal"]
        print(f"GRG {signal['grg_z']}σ — {signal['state_label']} ({signal['interpretation']})")
        print(signal["summary"])
    print(f"  Completed in {time.time() - t0:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

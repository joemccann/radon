#!/usr/bin/env python3
"""Repair CRI cache artifacts when only RVOL history is missing.

This path keeps the existing COR1M signal where possible, but rebuilds the
20-session history rows and trailing SPY closes needed by `/regime` from fresh
market history so the RVOL/COR1M chart does not collapse to a single point.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from cri_scan import (
    MA_WINDOW,
    VOL_WINDOW,
    _fetch_ib,
    _fetch_uw,
    _fetch_yahoo,
    compute_cri,
    compute_realized_vol,
    crash_trigger,
    cta_exposure_model,
    fetch_cor1m_current_quote,
)

ET = ZoneInfo("America/New_York")
REPAIR_TICKERS = ("VIX", "VVIX", "SPY")
MIN_REPAIR_BARS = MA_WINDOW + VOL_WINDOW

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
CACHE_PATH = DATA_DIR / "cri.json"
SCHEDULED_DIR = DATA_DIR / "cri_scheduled"


def as_number(value: Any) -> Optional[float]:
    """Return a finite float, else None."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def read_json_candidate(path: Path) -> Optional[Dict[str, Any]]:
    """Read a CRI payload from a plain JSON file or a stderr-prefixed artifact."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except Exception:
        return None

    json_start = raw.find("{")
    if json_start == -1:
        return None

    try:
        payload = json.loads(raw[json_start:])
    except Exception:
        return None

    return payload if isinstance(payload, dict) else None


def candidate_timestamp(payload: Dict[str, Any], fallback_path: Path) -> float:
    """Use scan_time when available; otherwise fall back to filesystem mtime."""
    scan_time = payload.get("scan_time")
    if isinstance(scan_time, str):
        parsed = datetime.fromisoformat(scan_time)
        return parsed.timestamp()
    return fallback_path.stat().st_mtime if fallback_path.exists() else 0.0


def existing_cache_payloads() -> List[Tuple[Path, Dict[str, Any]]]:
    """Return all valid CRI cache payloads available on disk."""
    payloads: List[Tuple[Path, Dict[str, Any]]] = []
    if SCHEDULED_DIR.exists():
        for path in sorted(SCHEDULED_DIR.glob("cri-*.json")):
            payload = read_json_candidate(path)
            if payload is not None:
                payloads.append((path, payload))

    payload = read_json_candidate(CACHE_PATH)
    if payload is not None:
        payloads.append((CACHE_PATH, payload))

    return payloads


def select_base_cache(payloads: Sequence[Tuple[Path, Dict[str, Any]]]) -> Dict[str, Any]:
    """Choose the freshest valid CRI payload as the repair baseline."""
    if not payloads:
        return {}

    def score(item: Tuple[Path, Dict[str, Any]]) -> Tuple[str, float]:
        path, payload = item
        return str(payload.get("date") or ""), candidate_timestamp(payload, path)

    return max(payloads, key=score)[1]


def build_cor1m_history_lookup(payloads: Iterable[Tuple[Path, Dict[str, Any]]]) -> Dict[str, float]:
    """Merge cached history rows so known COR1M values survive repair."""
    lookup: Dict[str, float] = {}
    for _path, payload in payloads:
        history = payload.get("history")
        if not isinstance(history, list):
            continue
        for entry in history:
            if not isinstance(entry, dict):
                continue
            date = entry.get("date")
            cor1m = as_number(entry.get("cor1m"))
            if isinstance(date, str) and cor1m is not None:
                lookup[date] = cor1m
    return lookup


def fetch_repair_market_data() -> Tuple[Dict[str, np.ndarray], List[str]]:
    """Fetch aligned market history for VIX, VVIX, and SPY only."""
    print("  Repair path: attempting IB history...", file=sys.stderr)
    ib_data = _fetch_ib(list(REPAIR_TICKERS))

    raw: Dict[str, List[Tuple[str, float]]] = {}
    fallback_needed: List[str] = []
    for ticker in REPAIR_TICKERS:
        bars = ib_data.get(ticker, [])
        if len(bars) >= MIN_REPAIR_BARS:
            raw[ticker] = bars
        else:
            if bars:
                print(
                    f"  Repair path: IB {ticker} only {len(bars)} bars (need {MIN_REPAIR_BARS})",
                    file=sys.stderr,
                )
            fallback_needed.append(ticker)

    if fallback_needed:
        print("  Repair path: trying Unusual Whales for fallback tickers...", file=sys.stderr)
        uw_data = _fetch_uw(fallback_needed)
        still_needed: List[str] = []
        for ticker in fallback_needed:
            bars = uw_data.get(ticker, [])
            if len(bars) >= MIN_REPAIR_BARS:
                raw[ticker] = bars
            else:
                still_needed.append(ticker)
        fallback_needed = still_needed

    for ticker in fallback_needed:
        print(f"  Repair path: LAST RESORT Yahoo for {ticker}", file=sys.stderr)
        bars = _fetch_yahoo(ticker)
        if len(bars) >= MIN_REPAIR_BARS:
            raw[ticker] = bars
            print(f"  Repair path: Yahoo {ticker} — {len(bars)} bars", file=sys.stderr)
        else:
            print(
                f"  Repair path: insufficient {ticker} history ({len(bars)} bars)",
                file=sys.stderr,
            )

    missing = [ticker for ticker in REPAIR_TICKERS if ticker not in raw]
    if missing:
        raise RuntimeError(f"Missing repair data for {missing}")

    date_sets = [set(date for date, _close in raw[ticker]) for ticker in REPAIR_TICKERS]
    common_dates = sorted(set.intersection(*date_sets))
    if len(common_dates) < MIN_REPAIR_BARS:
        raise RuntimeError(
            f"Only {len(common_dates)} common repair dates (need {MIN_REPAIR_BARS})"
        )

    aligned: Dict[str, np.ndarray] = {}
    for ticker in REPAIR_TICKERS:
        lookup = {date: close for date, close in raw[ticker]}
        aligned[ticker] = np.array([lookup[date] for date in common_dates], dtype=float)

    return aligned, common_dates


def compute_cor1m_5d_change(
    common_dates: Sequence[str],
    cor1m_by_date: Dict[str, float],
    current_cor1m: Optional[float],
    fallback: Optional[float],
) -> Optional[float]:
    """Compute a 5-session COR1M change when the historical points exist."""
    if current_cor1m is None or len(common_dates) < 6:
        return fallback

    previous = as_number(cor1m_by_date.get(common_dates[-6]))
    if previous is None:
        return fallback

    return round(current_cor1m - previous, 2)


def build_repaired_cri_payload(
    base_cache: Dict[str, Any],
    aligned: Dict[str, np.ndarray],
    common_dates: Sequence[str],
    cor1m_by_date: Dict[str, float],
    scan_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Rebuild the route-visible CRI payload with complete RVOL history."""
    vix = aligned["VIX"]
    vvix = aligned["VVIX"]
    spy = aligned["SPY"]
    last_date = str(common_dates[-1])

    vix_now = float(vix[-1])
    vvix_now = float(vvix[-1])
    spy_now = float(spy[-1])

    if len(vix) >= 6 and vix[-6] > 0:
        vix_5d_roc = (vix[-1] / vix[-6] - 1.0) * 100.0
    else:
        vix_5d_roc = 0.0

    vvix_vix_ratio = vvix_now / vix_now if vix_now > 0 else float("nan")

    if len(spy) >= MA_WINDOW:
        spx_100d_ma = float(np.mean(spy[-MA_WINDOW:]))
        spx_distance_pct = (spy_now / spx_100d_ma - 1.0) * 100.0
        spx_below_ma = spy_now < spx_100d_ma
    else:
        spx_100d_ma = float("nan")
        spx_distance_pct = 0.0
        spx_below_ma = False

    realized_vol = compute_realized_vol(spy, VOL_WINDOW)
    current_cor1m = as_number(base_cache.get("cor1m")) or as_number(cor1m_by_date.get(last_date))
    cor1m_5d_change = compute_cor1m_5d_change(
        common_dates=common_dates,
        cor1m_by_date=cor1m_by_date,
        current_cor1m=current_cor1m,
        fallback=as_number(base_cache.get("cor1m_5d_change")),
    )

    corr_input = current_cor1m if current_cor1m is not None else float("nan")
    corr_5d_input = cor1m_5d_change if cor1m_5d_change is not None else float("nan")
    cri = compute_cri(
        vix=vix_now,
        vix_5d_roc=float(vix_5d_roc),
        vvix=vvix_now,
        vvix_vix_ratio=float(vvix_vix_ratio),
        corr=corr_input,
        corr_5d_change=corr_5d_input,
        spx_distance_pct=float(spx_distance_pct),
    )
    cta = cta_exposure_model(realized_vol)
    trigger = crash_trigger(spx_below_ma, realized_vol, corr_input)

    history: List[Dict[str, Any]] = []
    for index in range(max(0, len(common_dates) - 20), len(common_dates)):
        day_vix = float(vix[index])
        day_vvix = float(vvix[index])
        day_spy = float(spy[index])
        day_date = str(common_dates[index])

        if index >= MA_WINDOW - 1:
            day_ma = float(np.mean(spy[index - MA_WINDOW + 1:index + 1]))
            spx_vs_ma_pct = (day_spy / day_ma - 1.0) * 100.0
        else:
            spx_vs_ma_pct = 0.0

        if index >= 5 and vix[index - 5] > 0:
            day_vix_5d_roc = (vix[index] / vix[index - 5] - 1.0) * 100.0
        else:
            day_vix_5d_roc = 0.0

        day_rvol = compute_realized_vol(spy[:index + 1], VOL_WINDOW)
        day_cor1m = current_cor1m if day_date == last_date and current_cor1m is not None else as_number(
            cor1m_by_date.get(day_date)
        )

        history.append(
            {
                "date": day_date,
                "vix": round(day_vix, 2),
                "vvix": round(day_vvix, 2),
                "spy": round(day_spy, 2),
                "cor1m": round(day_cor1m, 2) if day_cor1m is not None else None,
                "realized_vol": round(day_rvol, 2) if not math.isnan(day_rvol) else None,
                "spx_vs_ma_pct": round(spx_vs_ma_pct, 2),
                "vix_5d_roc": round(day_vix_5d_roc, 1),
            }
        )

    repaired = {
        **base_cache,
        "scan_time": scan_time or datetime.now(ET).replace(tzinfo=None).isoformat(),
        "date": last_date,
        "vix": round(vix_now, 2),
        "vvix": round(vvix_now, 2),
        "spy": round(spy_now, 2),
        "vix_5d_roc": round(vix_5d_roc, 1),
        "vvix_vix_ratio": round(vvix_vix_ratio, 2) if not math.isnan(vvix_vix_ratio) else None,
        "spx_100d_ma": round(spx_100d_ma, 2) if not math.isnan(spx_100d_ma) else None,
        "spx_distance_pct": round(spx_distance_pct, 2),
        "cor1m": round(current_cor1m, 2) if current_cor1m is not None else None,
        "cor1m_5d_change": round(cor1m_5d_change, 2) if cor1m_5d_change is not None else None,
        "realized_vol": round(realized_vol, 2) if not math.isnan(realized_vol) else None,
        "cri": cri,
        "cta": cta,
        "crash_trigger": trigger,
        "history": history,
        "spy_closes": [round(float(close), 4) for close in spy[-(VOL_WINDOW * 2):]],
    }
    return repaired


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically overwrite a JSON file without leaving partial artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        dir=str(path.parent),
        prefix=f".{path.stem}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(serialized)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def write_repaired_cache(payload: Dict[str, Any], now: Optional[datetime] = None) -> Path:
    """Write both the legacy cache and a newest scheduled snapshot."""
    timestamp = (now or datetime.now(ET)).strftime("%Y-%m-%dT%H-%M")
    scheduled_path = SCHEDULED_DIR / f"cri-{timestamp}.json"
    write_json_atomic(CACHE_PATH, payload)
    write_json_atomic(scheduled_path, payload)
    return scheduled_path


def repair_cache(target_date: Optional[str] = None) -> Dict[str, Any]:
    """Build a repaired CRI payload from fresh market history and cached COR1M."""
    payloads = existing_cache_payloads()
    base_cache = select_base_cache(payloads)
    cor1m_lookup = build_cor1m_history_lookup(payloads)

    current_cor1m = fetch_cor1m_current_quote()
    if current_cor1m is not None:
        base_cache = {**base_cache, "cor1m": round(current_cor1m, 2)}

    aligned, common_dates = fetch_repair_market_data()
    repaired = build_repaired_cri_payload(
        base_cache=base_cache,
        aligned=aligned,
        common_dates=common_dates,
        cor1m_by_date=cor1m_lookup,
    )
    if target_date and repaired.get("date") != target_date:
        raise RuntimeError(
            f"Repair produced {repaired.get('date')} but target date is {target_date}"
        )
    return repaired


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair CRI cache RVOL history.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the repaired payload to stdout.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write data/cri.json plus a new data/cri_scheduled snapshot.",
    )
    parser.add_argument(
        "--target-date",
        help="Require the repaired payload to end on this ET date (YYYY-MM-DD).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = repair_cache(target_date=args.target_date)
        if args.write:
            scheduled_path = write_repaired_cache(payload)
            print(
                f"Repair path wrote {CACHE_PATH.relative_to(PROJECT_DIR)} and {scheduled_path.relative_to(PROJECT_DIR)}",
                file=sys.stderr,
            )
        if args.json:
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
        return 0
    except Exception as exc:
        print(f"RVOL cache repair failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

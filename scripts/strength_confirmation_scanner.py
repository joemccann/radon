#!/usr/bin/env python3
"""7-step strength confirmation scanner.

Translates the operator checklist into auditable factor groups:
Q-scores, GEX, call positioning, term structure, vol smile, systematic
positioning, and market breadth. Primary live data source is Unusual Whales.
Where Radon has no direct feed for a checklist item, the factor is marked
``source="APPROX"`` and the note names the approximation.

The script writes ``data/strength_confirmation.json`` for the web UI.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from clients.uw_client import UWClient, UWRateLimitError
from utils.scan_coverage import (
    coverage_block,
    is_coverage_failed,
    payload_has_candidates,
    should_persist_scan,
)
from utils.scan_health import (
    SCAN_STATUS_BUDGET_BLOCKED,
    SCAN_STATUS_COVERAGE_FAILED,
    next_quota_reset_iso,
    record_scan_degraded,
)
from utils.uw_budget import should_block_universe_scan
from utils.uw_surface import fetch_surface, scan_ib_session

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover - DB layer optional in tests/dev
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_CACHE_PATH = _PROJECT_DIR / "data" / "strength_confirmation.json"
_DATA_DIR = _PROJECT_DIR / "data"

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

TRADING_DAYS = 252
DEFAULT_MAX_WORKERS = 24
RATE_LIMIT_ABORT = 8


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        parsed = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


NASDAQ_100_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "AVGO", "META", "TSLA", "GOOGL", "GOOG", "COST",
    "NFLX", "AMD", "PEP", "ADBE", "LIN", "CSCO", "TMUS", "INTU", "QCOM", "AMAT",
    "TXN", "ISRG", "BKNG", "AMGN", "HON", "CMCSA", "PANW", "ADP", "VRTX", "GILD",
    "SBUX", "ADI", "MU", "LRCX", "MELI", "KLAC", "INTC", "MDLZ", "CRWD", "REGN",
    "CTAS", "CEG", "CDNS", "SNPS", "MAR", "ORLY", "PYPL", "CSX", "ABNB", "FTNT",
    "NXPI", "ROP", "MRVL", "PCAR", "MNST", "WDAY", "ADSK", "AEP", "CPRT", "ROST",
    "PAYX", "KDP", "KHC", "ODFL", "FAST", "EXC", "DDOG", "TEAM", "CCEP", "EA",
    "XEL", "BKR", "GEHC", "FANG", "CSGP", "VRSK", "CHTR", "MCHP", "IDXX", "TTWO",
    "ZS", "ON", "CDW", "DXCM", "BIIB", "ANSS", "GFS", "MDB", "ILMN", "WBD",
    "TTD", "ARM", "AZN", "PDD", "ASML", "MRNA", "DASH", "LULU", "APP",
]


@dataclass
class FactorCheck:
    label: str
    passed: bool
    value: Optional[float]
    threshold: str
    note: str
    source: str = "UW"


@dataclass
class FactorAssessment:
    group: str
    passed: bool
    checks_passed: int
    checks_total: int
    source: str
    checks: List[FactorCheck]
    notes: List[str]


@dataclass
class StrengthCandidate:
    ticker: str
    verdict: str
    score: float
    groups_passed: int
    spot: float
    factors: List[FactorAssessment]
    errors: List[str]


@dataclass
class MarketContext:
    vix_front: Optional[float]
    vix_back: Optional[float]
    vix_front_prev: Optional[float]
    vix_back_prev: Optional[float]
    cta_exposure_pct: Optional[float]
    cta_forced_reduction_pct: Optional[float]
    spx_distance_pct: Optional[float]
    sectors_positive_ratio: Optional[float]
    sectors_call_premium_ratio: Optional[float]
    notes: List[str]
    net_breadth_20d: Optional[float] = None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _to_int(value: Any) -> int:
    num = _to_float(value)
    return int(num) if num is not None else 0


def _as_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("data", [])
    else:
        rows = payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _vol_pct(value: Any) -> Optional[float]:
    num = _to_float(value)
    if num is None or num <= 0:
        return None
    return num * 100 if num <= 3 else num


def _valid_ticker(token: str) -> bool:
    return token.isalpha() and 1 <= len(token) <= 6


def dedupe_tickers(tickers: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for ticker in tickers:
        upper = str(ticker).upper().strip()
        if not _valid_ticker(upper) or upper in seen:
            continue
        seen.add(upper)
        out.append(upper)
    return out


def resolve_tickers(tickers: Sequence[str], preset: Optional[str]) -> Tuple[List[str], str]:
    if tickers:
        return dedupe_tickers(tickers), "explicit"
    preset_name = preset or "ndx100"
    try:
        from utils.presets import load_preset
        loaded = load_preset(preset_name)
        resolved = dedupe_tickers(loaded.tickers)
        if resolved:
            return resolved, f"preset:{loaded.name}"
    except Exception as exc:
        print(f"  Preset {preset_name} unavailable ({exc}); using fallback", file=sys.stderr)
    if preset_name.lower() == "ndx100":
        return dedupe_tickers(NASDAQ_100_FALLBACK), "fallback:ndx100"
    return [], f"preset:{preset_name}"


def _prices_from_ohlc(payload: Any) -> List[float]:
    prices: List[float] = []
    for row in _as_rows(payload):
        close = _to_float(row.get("close"))
        if close is not None and close > 0:
            prices.append(close)
    return prices


def _pct_change(prices: Sequence[float], periods: int) -> Optional[float]:
    if len(prices) < periods + 1 or prices[-periods - 1] <= 0:
        return None
    return (prices[-1] / prices[-periods - 1] - 1) * 100


def calculate_hv(prices: Sequence[float], period: int) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    recent = list(prices)[-(period + 1):]
    returns = [
        math.log(recent[i] / recent[i - 1])
        for i in range(1, len(recent))
        if recent[i - 1] > 0 and recent[i] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS) * 100


def _latest_iv(payload: Any) -> Tuple[Optional[float], Optional[float]]:
    rows = _as_rows(payload)
    if not rows:
        return None, None
    latest = max(rows, key=lambda row: str(row.get("date", "")))
    return _vol_pct(latest.get("volatility")), _to_float(latest.get("iv_rank_1y"))


def _premium_value(row: Dict[str, Any]) -> float:
    for key in ("premium", "total_premium", "net_premium", "cost_basis", "ask_side_premium"):
        value = _to_float(row.get(key))
        if value is not None:
            return abs(value)
    price = _to_float(row.get("price") or row.get("fill_price"))
    size = _to_float(row.get("size") or row.get("volume"))
    if price is not None and size is not None:
        return abs(price * size * 100)
    return 0.0


def _is_call(row: Dict[str, Any]) -> bool:
    option_type = str(row.get("option_type") or row.get("right") or row.get("type") or "").lower()
    symbol = str(row.get("option_symbol") or row.get("symbol") or "")
    return option_type.startswith("c") or "C00" in symbol


def _is_put(row: Dict[str, Any]) -> bool:
    option_type = str(row.get("option_type") or row.get("right") or row.get("type") or "").lower()
    symbol = str(row.get("option_symbol") or row.get("symbol") or "")
    return option_type.startswith("p") or "P00" in symbol


def _is_ask_side(row: Dict[str, Any]) -> bool:
    if row.get("ask_side") is True or row.get("is_ask_side") is True:
        return True
    side = str(row.get("side") or row.get("sentiment") or row.get("trade_side") or "").lower()
    return "ask" in side or side in {"buy", "bought"}


def _is_bid_side(row: Dict[str, Any]) -> bool:
    if row.get("bid_side") is True or row.get("is_bid_side") is True:
        return True
    side = str(row.get("side") or row.get("sentiment") or row.get("trade_side") or "").lower()
    return "bid" in side or side in {"sell", "sold"}


def _option_flow_summary(alerts: Any, options_volume: Any) -> Dict[str, float]:
    call_buy = put_buy = call_total = put_total = 0.0
    for row in _as_rows(alerts):
        premium = _premium_value(row)
        if _is_call(row):
            call_total += premium
            if _is_ask_side(row) or not _is_bid_side(row):
                call_buy += premium
        elif _is_put(row):
            put_total += premium
            if _is_ask_side(row):
                put_buy += premium
    for row in _as_rows(options_volume):
        call_total += abs(_to_float(row.get("call_premium") or row.get("call_total_premium")) or 0.0)
        put_total += abs(_to_float(row.get("put_premium") or row.get("put_total_premium")) or 0.0)
    total = call_total + put_total
    call_ratio = call_total / total if total > 0 else 0.5
    call_buy_ratio = call_buy / max(call_total, 1.0)
    return {
        "call_total": call_total,
        "put_total": put_total,
        "call_ratio": call_ratio,
        "call_buy_ratio": call_buy_ratio,
        "put_buy": put_buy,
    }


def _parse_option_symbol(symbol: str) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    text = str(symbol or "").strip().upper()
    for idx in range(1, len(text) - 7):
        if text[idx:idx + 6].isdigit() and text[idx + 6:idx + 7] in {"C", "P"}:
            expiry = f"20{text[idx:idx + 2]}{text[idx + 2:idx + 4]}{text[idx + 4:idx + 6]}"
            right = text[idx + 6]
            strike_raw = text[idx + 7:]
            try:
                return expiry, int(strike_raw) / 1000, right
            except ValueError:
                return expiry, None, right
    return None, None, None


def _option_identity(row: Dict[str, Any]) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    symbol = str(row.get("option_symbol") or row.get("symbol") or row.get("id") or "")
    parsed_expiry, parsed_strike, parsed_right = _parse_option_symbol(symbol)
    expiry = str(row.get("expiry") or row.get("expiration") or parsed_expiry or "")
    if len(expiry) >= 10 and expiry[4] == "-" and expiry[7] == "-":
        expiry = expiry[:10].replace("-", "")
    strike = _to_float(row.get("strike") or row.get("strike_price")) or parsed_strike
    right = str(row.get("right") or row.get("option_type") or parsed_right or "").upper()[:1]
    return (expiry or None), strike, right if right in {"C", "P"} else None


def _canonical_delta(row: Dict[str, Any], right: str) -> Optional[float]:
    delta = _to_float(row.get("delta"))
    if delta is None or abs(delta) > 1:
        return None
    if right == "P":
        return delta - 1 if delta > 0 else delta
    return abs(delta)


def _extract_options_by_expiry(payload: Any) -> List[Dict[str, Any]]:
    options = []
    for row in _as_rows(payload):
        expiry, strike, right = _option_identity(row)
        if not expiry or strike is None or right is None:
            continue
        iv = _vol_pct(row.get("implied_volatility") or row.get("iv") or row.get("volatility"))
        if iv is None:
            continue
        options.append({
            "expiry": expiry,
            "strike": strike,
            "right": right,
            "iv": iv,
            "delta": _canonical_delta(row, right),
        })
    return options


def _term_rows(payload: Any) -> List[Tuple[str, float]]:
    rows: List[Tuple[str, float]] = []
    for row in _as_rows(payload):
        expiry = str(row.get("expiry") or row.get("expiration") or row.get("date") or "")
        if len(expiry) >= 10 and expiry[4] == "-" and expiry[7] == "-":
            expiry = expiry[:10].replace("-", "")
        vol = _vol_pct(row.get("volatility") or row.get("iv") or row.get("atm_iv"))
        if expiry and vol is not None:
            rows.append((expiry, vol))
    return sorted(rows, key=lambda item: item[0])


def _gex_rows_by_strike(payload: Any) -> List[Dict[str, float]]:
    parsed = []
    for row in _as_rows(payload):
        strike = _to_float(row.get("strike"))
        if strike is None:
            continue
        call_gex = _to_float(row.get("call_gex")) or 0.0
        put_gex = _to_float(row.get("put_gex")) or 0.0
        parsed.append({
            "strike": strike,
            "call_gex": call_gex,
            "put_gex": put_gex,
            "net_gex": call_gex + put_gex,
        })
    return parsed


def _gex_history(payload: Any) -> List[Dict[str, float]]:
    parsed = []
    for row in _as_rows(payload):
        call_gex = _to_float(row.get("call_gex")) or 0.0
        put_gex = _to_float(row.get("put_gex")) or 0.0
        parsed.append({
            "date_sort": str(row.get("date") or row.get("timestamp") or ""),
            "call_gex": call_gex,
            "put_gex": put_gex,
            "net_gex": call_gex + put_gex,
        })
    return sorted(parsed, key=lambda row: row["date_sort"])


def _oi_call_rows(payload: Any) -> List[Dict[str, Any]]:
    rows = []
    for row in _as_rows(payload):
        expiry, strike, right = _option_identity(row)
        if right != "C" or strike is None:
            continue
        change = _to_float(
            row.get("oi_change")
            or row.get("open_interest_change")
            or row.get("change")
            or row.get("open_interest_diff")
        )
        if change is None:
            change = _to_float(row.get("open_interest") or row.get("oi"))
        if change is not None and change > 0:
            rows.append({"expiry": expiry or "", "strike": strike, "change": change})
    return rows


def _read_latest_cri() -> Dict[str, Any]:
    candidates = []
    scheduled = _DATA_DIR / "cri_scheduled"
    try:
        candidates.extend(sorted(scheduled.glob("cri-*.json"), reverse=True))
    except Exception:
        pass
    candidates.append(_DATA_DIR / "cri.json")
    for path in candidates:
        try:
            raw = path.read_text()
            start = raw.find("{")
            if start == -1:
                continue
            parsed = json.loads(raw[start:])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


# A/D data older than a holiday-extended week is worse than the momentum
# proxy — a stale "measured" reading would carry false authority.
_BREADTH_FRESHNESS_DAYS = 5


def _read_latest_breadth(now: Optional[datetime] = None) -> Optional[float]:
    """20-session net breadth (sum of daily net A/D) from breadth-scan's
    disk cache, or None when missing/stale/malformed."""
    try:
        parsed = json.loads((_DATA_DIR / "breadth.json").read_text())
    except Exception:
        return None
    latest = parsed.get("latest") if isinstance(parsed, dict) else None
    if not isinstance(latest, dict):
        return None
    value = _to_float(latest.get("cum_ad_change_20d"))
    session_date = latest.get("session_date")
    if value is None or not isinstance(session_date, str):
        return None
    try:
        session = datetime.strptime(session_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    reference = now or datetime.now(timezone.utc)
    if (reference - session).days > _BREADTH_FRESHNESS_DAYS:
        return None
    return value


def _sector_stats(client: Any) -> Tuple[Optional[float], Optional[float], List[str]]:
    try:
        rows = _as_rows(client.get_sector_etfs())
    except Exception as exc:
        return None, None, [f"sector_etfs:{exc}"]
    if not rows:
        return None, None, ["sector_etfs:empty"]
    positive = 0
    call_premium = put_premium = 0.0
    counted = 0
    for row in rows:
        change = (
            _to_float(row.get("change"))
            or _to_float(row.get("day_change_pct"))
            or _to_float(row.get("pct_change"))
            or _to_float(row.get("change_pct"))
        )
        if change is not None:
            counted += 1
            if change > 0:
                positive += 1
        call_premium += abs(_to_float(row.get("call_premium") or row.get("call_total_premium")) or 0.0)
        put_premium += abs(_to_float(row.get("put_premium") or row.get("put_total_premium")) or 0.0)
    positive_ratio = positive / counted if counted else None
    total_premium = call_premium + put_premium
    call_ratio = call_premium / total_premium if total_premium > 0 else None
    return positive_ratio, call_ratio, []


def build_market_context(client: Any) -> MarketContext:
    notes: List[str] = []
    cri = _read_latest_cri()
    cta = cri.get("cta") if isinstance(cri.get("cta"), dict) else {}
    history = cri.get("history") if isinstance(cri.get("history"), list) else []
    vix_front = _to_float(cri.get("vix"))
    vix_back = None
    vix_front_prev = None
    vix_back_prev = None
    if len(history) >= 2:
        vix_front_prev = _to_float(history[-2].get("vix")) if isinstance(history[-2], dict) else None
    if len(history) >= 1 and vix_front_prev is None:
        vix_front_prev = _to_float(history[-1].get("vix")) if isinstance(history[-1], dict) else None
    # No durable VIX futures curve feed is available in this scanner path.
    # Estimate "back" vol from VVIX/VIX when present and mark term checks APPROX.
    vvix_ratio = _to_float(cri.get("vvix_vix_ratio"))
    if vix_front is not None and vvix_ratio is not None:
        vix_back = vix_front * (1.04 if vvix_ratio < 6.2 else 0.98)
        vix_back_prev = vix_front_prev * (1.04 if vix_front_prev is not None and vvix_ratio < 6.2 else 0.98) if vix_front_prev is not None else None
        notes.append("VIX back curve approximated from CRI VVIX/VIX ratio; no listed VIX-futures feed in scanner subprocess")
    elif vix_front is not None:
        notes.append("VIX front available from CRI; back curve unavailable")

    sectors_positive_ratio, sectors_call_premium_ratio, sector_notes = _sector_stats(client)
    notes.extend(sector_notes)
    net_breadth_20d = _read_latest_breadth()
    if net_breadth_20d is None:
        notes.append("NYSE breadth cache missing or stale; advancing-stocks check uses momentum proxy")
    return MarketContext(
        vix_front=vix_front,
        vix_back=vix_back,
        vix_front_prev=vix_front_prev,
        vix_back_prev=vix_back_prev,
        cta_exposure_pct=_to_float(cta.get("exposure_pct")) if isinstance(cta, dict) else None,
        cta_forced_reduction_pct=_to_float(cta.get("forced_reduction_pct")) if isinstance(cta, dict) else None,
        spx_distance_pct=_to_float(cri.get("spx_distance_pct")),
        sectors_positive_ratio=sectors_positive_ratio,
        sectors_call_premium_ratio=sectors_call_premium_ratio,
        notes=notes,
        net_breadth_20d=net_breadth_20d,
    )


def _factor(group: str, checks: List[FactorCheck], source: str, notes: Optional[List[str]] = None) -> FactorAssessment:
    passed_count = sum(1 for check in checks if check.passed)
    return FactorAssessment(
        group=group,
        passed=passed_count == len(checks),
        checks_passed=passed_count,
        checks_total=len(checks),
        source=source,
        checks=checks,
        notes=list(notes or []),
    )


def analyze_q_scores(
    prices: Sequence[float],
    iv: Optional[float],
    iv_rank: Optional[float],
    flow_summary: Dict[str, float],
) -> FactorAssessment:
    trend_20d = _pct_change(prices, 20) or 0.0
    trend_5d = _pct_change(prices, 5) or 0.0
    hv20 = calculate_hv(prices, 20) or 0.0
    option_score = max(0.0, min(5.0, 1.0 + flow_summary["call_ratio"] * 3.2 + flow_summary["call_buy_ratio"] * 0.8))
    momentum_score = max(0.0, min(5.0, 2.5 + trend_20d / 4.0 + trend_5d / 3.0))
    volatility_score = 1.0
    if hv20 > 22:
        volatility_score += 1.0
    if hv20 > 35:
        volatility_score += 1.0
    if iv_rank is not None and iv_rank > 45:
        volatility_score += 1.0
    if iv is not None and iv > 45:
        volatility_score += 1.0
    volatility_score = min(5.0, volatility_score)
    return _factor(
        "Q-SCORES",
        [
            FactorCheck("Option Score > 3", option_score > 3.0, round(option_score, 2), "> 3", "Bullish call-premium and ask-side option flow", "APPROX"),
            FactorCheck("Momentum Score > 3", momentum_score > 3.0, round(momentum_score, 2), "> 3", "20D/5D price momentum proxy", "APPROX"),
            FactorCheck("Volatility Score < 3", volatility_score < 3.0, round(volatility_score, 2), "< 3", "Low-stress HV/IV-rank proxy", "APPROX"),
        ],
        "APPROX",
        ["Q-scores are reconstructed from UW flow, price momentum, HV, and IV rank; no direct Q-score feed found"],
    )


def analyze_net_gex(strike_payload: Any, aggregate_payload: Any, spot: float) -> FactorAssessment:
    strikes = _gex_rows_by_strike(strike_payload)
    history = _gex_history(aggregate_payload)
    last = history[-1] if history else None
    prev = history[-2] if len(history) >= 2 else None
    current_net = last["net_gex"] if last else sum(row["net_gex"] for row in strikes)
    prior_net = prev["net_gex"] if prev else None
    current_put = last["put_gex"] if last else sum(row["put_gex"] for row in strikes)
    prior_put = prev["put_gex"] if prev else None
    near = [row for row in strikes if spot > 0 and abs(row["strike"] / spot - 1) <= 0.03]
    spot_net = sum(row["net_gex"] for row in near) if near else current_net
    negative_clusters = sum(1 for row in strikes if row["net_gex"] < 0)
    negative_gex = sum(row["net_gex"] for row in strikes if row["net_gex"] < 0)
    shrinking = (
        (prior_put is not None and abs(current_put) < abs(prior_put))
        or (prior_net is not None and current_net > prior_net)
        or negative_clusters == 0
    )
    less_negative = spot_net >= 0 or current_net >= 0 or (prior_net is not None and current_net > prior_net)
    pressure_declining = prior_net is not None and current_net > prior_net
    return _factor(
        "NET GEX",
        [
            FactorCheck("Negative GEX clusters shrinking", shrinking, round(negative_gex, 2), "shrinking", f"{negative_clusters} negative strike clusters remain"),
            FactorCheck("Net GEX less negative at spot", less_negative, round(spot_net, 2), ">= prior or >= 0", "Spot-zone gamma is stabilizing"),
            FactorCheck("Hedging pressure declining", pressure_declining, round(current_net, 2), "> prior net GEX", f"Prior net GEX {prior_net if prior_net is not None else 'n/a'}"),
        ],
        "UW",
    )


def analyze_call_positioning(strike_payload: Any, oi_payload: Any, spot: float) -> FactorAssessment:
    strikes = _gex_rows_by_strike(strike_payload)
    positive_above = [row for row in strikes if row["strike"] > spot and row["net_gex"] > 0]
    wall = max(positive_above, key=lambda row: row["net_gex"], default=None)
    oi_calls = _oi_call_rows(oi_payload)
    positive_strikes = {row["strike"] for row in oi_calls if row["change"] > 0}
    positive_expiries = {row["expiry"] for row in oi_calls if row["change"] > 0 and row["expiry"]}
    total_call_oi_change = sum(row["change"] for row in oi_calls)
    return _factor(
        "CALL POSITIONING",
        [
            FactorCheck("Positive gamma wall above spot", wall is not None, round(wall["strike"], 2) if wall else None, "> spot", "Highest positive net-gamma strike above spot"),
            FactorCheck("Calls building across multiple strikes", len(positive_strikes) >= 2, float(len(positive_strikes)), ">= 2 strikes", f"Positive call OI change {total_call_oi_change:.0f}"),
            FactorCheck("Multi-expiry support visible", len(positive_expiries) >= 2, float(len(positive_expiries)), ">= 2 expiries", "Positive call OI change spans expiries"),
        ],
        "UW",
    )


def analyze_term_structure(term_payload: Any, market_context: MarketContext) -> FactorAssessment:
    rows = _term_rows(term_payload)
    front = market_context.vix_front
    back = market_context.vix_back
    front_prev = market_context.vix_front_prev
    back_prev = market_context.vix_back_prev
    source = "APPROX"
    notes = list(market_context.notes)
    if front is None or back is None:
        if len(rows) >= 2:
            front, back = rows[0][1], rows[-1][1]
            notes.append("Used ticker IV term structure as VIX curve proxy because VIX-futures curve was unavailable")
        elif len(rows) == 1:
            front = back = rows[0][1]
    if front_prev is None and front is not None:
        front_prev = front + 0.01
        notes.append("Front-curve drop inferred from current low-stress term snapshot")
    if back_prev is None and back is not None:
        back_prev = back
    contango = front is not None and back is not None and front < back
    front_dropping = front is not None and front_prev is not None and front <= front_prev
    back_stable = back is not None and back_prev is not None and abs(back - back_prev) <= max(1.5, abs(back_prev) * 0.08)
    return _factor(
        "TERM STRUCTURE",
        [
            FactorCheck("VIX in contango", contango, round((back or 0) - (front or 0), 2) if front is not None and back is not None else None, "near < long", "Near-term volatility below long-term"),
            FactorCheck("Front curve dropping", front_dropping, round(front, 2) if front is not None else None, "<= prior", "Front volatility not rising"),
            FactorCheck("Back curve stable", back_stable, round(back, 2) if back is not None else None, "stable", "Longer volatility anchor is not accelerating"),
        ],
        source,
        notes,
    )


def _latest_risk_reversal_values(payload: Any) -> Tuple[Optional[float], Optional[float]]:
    rows = _as_rows(payload)
    if len(rows) < 2:
        return None, None
    rows = sorted(rows, key=lambda row: str(row.get("date") or row.get("timestamp") or ""))
    prev = _to_float(rows[-2].get("value") or rows[-2].get("skew") or rows[-2].get("risk_reversal"))
    latest = _to_float(rows[-1].get("value") or rows[-1].get("skew") or rows[-1].get("risk_reversal"))
    return prev, latest


def analyze_vol_smile(contracts_payload: Any, risk_reversal_payload: Any) -> FactorAssessment:
    options = _extract_options_by_expiry(contracts_payload)
    expiries = sorted({row["expiry"] for row in options})
    target_expiry = expiries[0] if expiries else ""
    expiry_options = [row for row in options if row["expiry"] == target_expiry] if target_expiry else options
    calls = [row for row in expiry_options if row["right"] == "C"]
    puts = [row for row in expiry_options if row["right"] == "P"]
    call25 = min(calls, key=lambda row: abs((row.get("delta") or 0.25) - 0.25), default=None)
    call10 = min(calls, key=lambda row: abs((row.get("delta") or 0.10) - 0.10), default=None)
    put25 = min(puts, key=lambda row: abs(abs(row.get("delta") or -0.25) - 0.25), default=None)
    prev_rr, latest_rr = _latest_risk_reversal_values(risk_reversal_payload)
    current_skew = (put25["iv"] - call25["iv"]) if put25 and call25 else latest_rr
    downside_reducing = (
        prev_rr is not None and latest_rr is not None and latest_rr < prev_rr
    ) or (current_skew is not None and current_skew <= 2.5)
    smile_flat = current_skew is not None and current_skew <= 4.0
    upside_bid = bool(call10 and call25 and call10["iv"] >= call25["iv"])
    return _factor(
        "VOLATILITY SMILE",
        [
            FactorCheck("Downside skew reducing", downside_reducing, round(latest_rr if latest_rr is not None else current_skew or 0, 4) if current_skew is not None or latest_rr is not None else None, "falling", "Risk-reversal or 25-delta skew is easing"),
            FactorCheck("Smile flattening bullishly", smile_flat, round(current_skew, 2) if current_skew is not None else None, "<= 4 vol pts", "Put wing is not materially richer than call wing"),
            FactorCheck("Upside convexity bid", upside_bid, round((call10["iv"] - call25["iv"]), 2) if call10 and call25 else None, "10D call IV >= 25D call IV", "Upside wing has convexity demand"),
        ],
        "UW",
    )


def analyze_systematic_positioning(market_context: MarketContext) -> FactorAssessment:
    cta_buyers = (
        market_context.cta_exposure_pct is not None
        and market_context.cta_exposure_pct >= 100
        and (market_context.cta_forced_reduction_pct or 0) <= 0
    )
    vol_control = (
        (market_context.vix_front is not None and market_context.vix_front < 22)
        or (
            market_context.vix_front is not None
            and market_context.vix_front_prev is not None
            and market_context.vix_front <= market_context.vix_front_prev
        )
    )
    risk_parity = (
        (market_context.spx_distance_pct is not None and market_context.spx_distance_pct >= 0)
        or (market_context.sectors_positive_ratio is not None and market_context.sectors_positive_ratio >= 0.60)
    )
    return _factor(
        "SYSTEMATIC POSITIONING",
        [
            FactorCheck("CTAs net buyers", cta_buyers, market_context.cta_exposure_pct, ">= 100% exposure, no forced reduction", "CRI CTA exposure proxy", "APPROX"),
            FactorCheck("Vol Control supportive", vol_control, market_context.vix_front, "VIX < 22 or falling", "Vol-control proxy from CRI VIX trend", "APPROX"),
            FactorCheck("Risk Parity bullish", risk_parity, market_context.spx_distance_pct, "SPX above 100D or broad sectors green", "Risk-parity proxy from SPX distance/breadth", "APPROX"),
        ],
        "APPROX",
        ["No direct CTA net-flow, vol-control, or risk-parity allocation feed found; using CRI and breadth proxies"],
    )


def analyze_market_breadth(market_context: MarketContext, prices: Sequence[float]) -> FactorAssessment:
    trend_20d = _pct_change(prices, 20) or 0.0
    sectors_participating = market_context.sectors_positive_ratio is not None and market_context.sectors_positive_ratio >= 0.55
    if market_context.net_breadth_20d is not None:
        advancing_check = FactorCheck(
            "Advancing stocks expanding",
            market_context.net_breadth_20d > 0,
            round(market_context.net_breadth_20d, 2),
            "> 0 (20-session sum of daily net A/D)",
            "NYSE advance/decline from breadth-scan",
            "IB",
        )
    else:
        advancers_expanding = sectors_participating and trend_20d > 0
        advancing_check = FactorCheck("Advancing stocks expanding", advancers_expanding, trend_20d, "> 0 and broad sectors green", "Ticker momentum plus sector participation proxy", "APPROX")
    green_box = (
        sectors_participating
        and (market_context.sectors_call_premium_ratio is None or market_context.sectors_call_premium_ratio >= 0.52)
    )
    source = "UW" if market_context.sectors_positive_ratio is not None else "APPROX"
    return _factor(
        "MARKET BREADTH",
        [
            FactorCheck("Sectors participating", sectors_participating, market_context.sectors_positive_ratio, ">= 55%", "Positive SPDR sector ETF share", source),
            advancing_check,
            FactorCheck("Green box breadth indicator", green_box, market_context.sectors_call_premium_ratio, ">= 52% sector call premium", "Sector options breadth proxy", source),
        ],
        source,
    )


def verdict_for(groups_passed: int) -> str:
    if groups_passed == 7:
        return "REAL_STRENGTH_CONFIRMED"
    if groups_passed >= 5:
        return "WATCHLIST"
    return "WEAK"


def scan_ticker(
    ticker: str,
    client: Optional[Any] = None,
    now: Optional[datetime] = None,  # noqa: ARG001 - injectable for tests and future date-gated metrics
    market_context: Optional[MarketContext] = None,
    *,
    retry_transient: bool = False,
    ib: Any = None,
) -> Optional[StrengthCandidate]:
    own_client = None
    if client is None:
        client_kwargs = {} if retry_transient else {"max_retries": 0, "backoff_factor": 0}
        own_client = UWClient(**client_kwargs)
        client = own_client
    errors: List[str] = []
    ticker = ticker.upper()
    try:
        try:
            surface = fetch_surface(client, ticker, ib=ib)
            prices = _prices_from_ohlc(surface["ohlc"])
        except UWRateLimitError:
            raise
        except Exception as exc:
            errors.append(f"ohlc:{exc}")
            return None
        if len(prices) < 21:
            errors.append("insufficient_price_history")
            return None
        spot = prices[-1]

        def fetch(label: str, func, default: Any) -> Any:
            try:
                return func()
            except UWRateLimitError:
                raise
            except Exception as exc:  # noqa: BLE001 - scanner degrades by factor
                errors.append(f"{label}:{exc}")
                return default

        # Per-ticker UW budget: keep surface + OI change; degrade the rest.
        empty = {"data": []}
        alerts = empty
        options_volume = empty
        iv, iv_rank = _latest_iv(fetch("iv_rank", lambda: surface["iv_rank"], empty))
        strike_gex = fetch("gex_strike", lambda: surface["gex_strike"], empty)
        aggregate_gex = empty
        oi_change = fetch("oi_change", lambda: client.get_stock_oi_change(ticker), empty)
        term_structure = empty
        contracts = fetch("contracts", lambda: surface["contracts"], empty)
        risk_reversal = empty

        context = market_context or build_market_context(client)
        flow_summary = _option_flow_summary(alerts, options_volume)
        factors = [
            analyze_q_scores(prices, iv, iv_rank, flow_summary),
            analyze_net_gex(strike_gex, aggregate_gex, spot),
            analyze_call_positioning(strike_gex, oi_change, spot),
            analyze_term_structure(term_structure, context),
            analyze_vol_smile(contracts, risk_reversal),
            analyze_systematic_positioning(context),
            analyze_market_breadth(context, prices),
        ]
        groups_passed = sum(1 for factor in factors if factor.passed)
        score = round(groups_passed / 7 * 100, 2)
        return StrengthCandidate(
            ticker=ticker,
            verdict=verdict_for(groups_passed),
            score=score,
            groups_passed=groups_passed,
            spot=round(spot, 4),
            factors=factors,
            errors=errors,
        )
    except UWRateLimitError:
        raise
    except Exception as exc:  # noqa: BLE001 - per-ticker scanner isolation
        print(f"  {ticker} - ERROR ({exc})", file=sys.stderr)
        return None
    finally:
        if own_client is not None:
            own_client.close()


def build_output(
    results: List[StrengthCandidate],
    source: str,
    universe_count: int,
    requested_tickers: Optional[Sequence[str]] = None,
    coverage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    sorted_results = sorted(results, key=lambda row: (row.groups_passed, row.score), reverse=True)
    return {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "source": "Unusual Whales + Radon regime caches",
        "universe": source,
        "requested_tickers": list(requested_tickers or []),
        "tickers_scanned": universe_count,
        "coverage": coverage or coverage_block(
            tickers=universe_count, ok=len(sorted_results),
            no_setup=0, rate_limited=0, errors=0,
        ),
        "candidates_found": len(sorted_results),
        "confirmed_strength_count": sum(1 for row in sorted_results if row.verdict == "REAL_STRENGTH_CONFIRMED"),
        "results": [asdict(row) for row in sorted_results],
    }


def scan_universe(
    tickers: Sequence[str],
    preset: Optional[str] = "ndx100",
    limit: Optional[int] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    retry_transient: bool = False,
) -> Dict[str, Any]:
    resolved, source = resolve_tickers(tickers, preset)
    if limit is not None and limit > 0:
        resolved = resolved[:limit]
    if not tickers and should_block_universe_scan():
        print(
            f"UW daily budget block; skipping universe scan ({source})",
            file=sys.stderr,
        )
        record_scan_degraded(
            "strength-confirmation",
            SCAN_STATUS_BUDGET_BLOCKED,
            f"UW daily budget block; universe scan skipped ({source})",
            next_attempt_at=next_quota_reset_iso(),
        )
        prior = _read_cache_file(_CACHE_PATH)
        if payload_has_candidates(prior):
            return {**prior, "scan_status": SCAN_STATUS_BUDGET_BLOCKED}
        blocked = build_output(
            [], source, len(resolved), requested_tickers=resolved,
            coverage=coverage_block(
                tickers=len(resolved), ok=0, no_setup=0, rate_limited=0, errors=0,
            ),
        )
        blocked["scan_status"] = SCAN_STATUS_BUDGET_BLOCKED
        return blocked
    print(f"Scanning {len(resolved)} tickers for 7-step strength confirmation ({source})...", file=sys.stderr)

    client_kwargs = {} if retry_transient else {"max_retries": 0, "backoff_factor": 0}
    context_client = UWClient(**client_kwargs)
    try:
        market_context = build_market_context(context_client)
    finally:
        context_client.close()

    results: List[StrengthCandidate] = []
    ok = 0
    no_setup = 0
    rate_limited = 0
    errors = 0
    consecutive_rate_limits = 0
    with scan_ib_session() as ib:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {
                pool.submit(
                    scan_ticker,
                    ticker,
                    None,
                    None,
                    market_context,
                    retry_transient=retry_transient,
                    ib=ib,
                ): ticker
                for ticker in resolved
            }
            for idx, future in enumerate(as_completed(futures), start=1):
                ticker = futures[future]
                try:
                    row = future.result()
                except CancelledError:
                    continue
                except UWRateLimitError:
                    rate_limited += 1
                    consecutive_rate_limits += 1
                    print(f"  [{idx}/{len(resolved)}] {ticker} - SKIP (rate limited)", file=sys.stderr)
                    if consecutive_rate_limits >= RATE_LIMIT_ABORT:
                        print(
                            f"  aborting remaining tickers after {RATE_LIMIT_ABORT} consecutive UW rate limits",
                            file=sys.stderr,
                        )
                        for pending in futures:
                            pending.cancel()
                    continue
                except Exception as exc:
                    errors += 1
                    consecutive_rate_limits = 0
                    print(f"  [{idx}/{len(resolved)}] {ticker} - ERROR ({exc})", file=sys.stderr)
                    continue
                consecutive_rate_limits = 0
                if row is None:
                    no_setup += 1
                    print(f"  [{idx}/{len(resolved)}] {ticker} - NO DATA", file=sys.stderr)
                    continue
                ok += 1
                print(f"  [{idx}/{len(resolved)}] {ticker} - {row.verdict} ({row.groups_passed}/7)", file=sys.stderr)
                results.append(row)
    payload = build_output(
        results, source, len(resolved), requested_tickers=resolved,
        coverage=coverage_block(
            tickers=len(resolved), ok=ok, no_setup=no_setup,
            rate_limited=rate_limited, errors=errors,
        ),
    )
    if not tickers and is_coverage_failed(payload):
        payload["scan_status"] = SCAN_STATUS_COVERAGE_FAILED
        cov = payload["coverage"]
        record_scan_degraded(
            "strength-confirmation",
            SCAN_STATUS_COVERAGE_FAILED,
            f"UW coverage failed; completed {cov['completed']}/{cov['tickers']} ({source})",
        )
    return payload


def _read_cache_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_cache(payload: Dict[str, Any], path: Path = _CACHE_PATH) -> bool:
    # scan_status is transient run telemetry — persisting it would let a
    # blocked run's marker outlive the block inside the last-good snapshot.
    payload = {key: value for key, value in payload.items() if key != "scan_status"}
    prior = _read_cache_file(path)
    if not should_persist_scan(payload, prior):
        print("Empty strength scan — preserving last good cache", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    mirror_scan_snapshot("strength-confirmation", payload)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan for 7-step strength confirmation candidates.")
    parser.add_argument("tickers", nargs="*", help="Tickers to scan. Overrides --preset.")
    parser.add_argument("--preset", default="ndx100", help="Preset name from data/presets (default: ndx100).")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for smoke/validation runs.")
    parser.add_argument(
        "--workers",
        type=int,
        default=_env_int("RADON_STRENGTH_SCANNER_WORKERS", DEFAULT_MAX_WORKERS),
        help=f"Concurrent workers (default {DEFAULT_MAX_WORKERS}; override RADON_STRENGTH_SCANNER_WORKERS).",
    )
    parser.add_argument(
        "--retry-transient",
        action="store_true",
        help="Retry UW 429/5xx responses instead of skipping transient failures quickly.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON payload to stdout.")
    parser.add_argument("--output", default=str(_CACHE_PATH), help="Cache output path.")
    args = parser.parse_args()

    payload = scan_universe(
        args.tickers,
        preset=args.preset,
        limit=args.limit,
        max_workers=args.workers,
        retry_transient=args.retry_transient,
    )
    save_cache(payload, Path(args.output))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Strength confirmation scan complete: "
            f"{payload['confirmed_strength_count']} confirmed / {payload['candidates_found']} candidates"
        )


if __name__ == "__main__":
    main()

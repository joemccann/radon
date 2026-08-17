#!/usr/bin/env python3
"""Theta Harvester scanner.

Finds short-strangle candidates where the setup is actually theta-led:
near-flat proposed delta, IV premium over realized volatility, positive
dealer support, controlled short gamma, and a range-bound tape.

Primary data source is Unusual Whales via ``clients.uw_client.UWClient``.
The script writes ``data/theta_harvester.json`` for the web UI.
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

from clients.uw_client import UWAPIError, UWClient, UWRateLimitError
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
from utils.uw_surface import fetch_surface

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover - DB layer optional in tests/dev
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_CACHE_PATH = _PROJECT_DIR / "data" / "theta_harvester.json"

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

TRADING_DAYS = 252
MIN_DTE = 7
MAX_DTE = 45
TARGET_DELTA = 0.16
NEAR_ZERO_DELTA = 0.10
RISK_FREE_RATE = 0.045
DEFAULT_MAX_WORKERS = 24
# After this many consecutive UW 429s the rest of the universe will 429 too.
RATE_LIMIT_ABORT = 8


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        parsed = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))

# Fallback is only used when the file preset exists but is structurally
# corrupted (for example numeric year strings instead of tickers). The normal
# path remains data/presets/ndx100.json via utils.presets.
NASDAQ_100_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "AVGO", "META", "TSLA", "GOOGL", "GOOG", "COST",
    "NFLX", "AMD", "PEP", "ADBE", "LIN", "CSCO", "TMUS", "INTU", "QCOM", "AMAT",
    "TXN", "ISRG", "BKNG", "AMGN", "HON", "CMCSA", "AMGN", "PANW", "ADP", "VRTX",
    "GILD", "SBUX", "ADI", "MU", "LRCX", "MELI", "KLAC", "INTC", "MDLZ", "CRWD",
    "REGN", "CTAS", "CEG", "CDNS", "SNPS", "MAR", "ORLY", "PYPL", "CSX", "ABNB",
    "FTNT", "NXPI", "ROP", "MRVL", "PCAR", "MNST", "WDAY", "ADSK", "AEP", "CPRT",
    "ROST", "PAYX", "KDP", "KHC", "ODFL", "FAST", "EXC", "DDOG", "TEAM", "CCEP",
    "EA", "XEL", "BKR", "GEHC", "FANG", "CSGP", "VRSK", "CHTR", "MCHP", "IDXX",
    "TTWO", "ZS", "ON", "CDW", "DXCM", "BIIB", "ANSS", "GFS", "MDB", "ILMN",
    "WBD", "TTD", "ARM", "AZN", "PDD", "ASML", "MRNA", "DASH", "LULU", "APP",
]


@dataclass
class OptionLeg:
    symbol: str
    expiry: str
    strike: float
    right: str
    iv: float
    delta: float
    theta: float
    gamma: float
    vega: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: int = 0
    open_interest: int = 0


@dataclass
class ThetaStructure:
    expiry: str
    dte: int
    short_put: OptionLeg
    short_call: OptionLeg
    net_delta: float
    theta: float
    gamma: float
    vega: float
    credit: Optional[float]


@dataclass
class ThetaCandidate:
    ticker: str
    score: float
    verdict: str
    structure: ThetaStructure
    spot: float
    iv: float
    hv20: float
    hv60: float
    iv_rv_edge: float
    iv_rv_ratio: float
    trend_20d_pct: float
    range_score: float
    dealer_support: str
    net_gex: Optional[float]
    gex_flip: Optional[float]
    setup: str
    gates: Dict[str, bool]
    errors: List[str]
    # Earnings risk surface only (v1): never gates verdict/score.
    # null | {report_date, report_time, days_until, within_dte, source, expected_move_pct}
    earnings: Optional[Dict[str, Any]] = None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


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
    # UW commonly emits decimal vol (0.35). Some cached/script fields already
    # carry percent points (35.0). Accept both domains.
    return num * 100 if num <= 3 else num


def _parse_date_yyyymmdd(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return text
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    return None


def parse_option_symbol(symbol: str) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Parse OCC-like symbols: AAPL260717C00200000."""
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


def _contract_identity(row: Dict[str, Any]) -> Tuple[Optional[str], Optional[float], Optional[str], str]:
    symbol = str(
        row.get("option_symbol")
        or row.get("symbol")
        or row.get("option_chain")
        or row.get("id")
        or ""
    )
    parsed_expiry, parsed_strike, parsed_right = parse_option_symbol(symbol)
    expiry = (
        _parse_date_yyyymmdd(row.get("expiry"))
        or _parse_date_yyyymmdd(row.get("expiration"))
        or _parse_date_yyyymmdd(row.get("expires_at"))
        or parsed_expiry
    )
    strike = _to_float(row.get("strike") or row.get("strike_price")) or parsed_strike
    right = str(row.get("right") or row.get("option_type") or parsed_right or "").upper()[:1]
    if right not in {"C", "P"}:
        return expiry, strike, None, symbol
    return expiry, strike, right, symbol


def _canonical_delta(raw: Any, right: str) -> Optional[float]:
    delta = _to_float(raw)
    if delta is None or abs(delta) > 1:
        return None
    if right == "P":
        return delta - 1 if delta > 0 else delta
    return abs(delta)


def _normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_greeks(spot: float, strike: float, iv_pct: float, dte: int, right: str) -> Dict[str, float]:
    if spot <= 0 or strike <= 0 or iv_pct <= 0 or dte <= 0:
        return {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0}
    sigma = iv_pct / 100
    t = max(dte / 365.0, 1 / 365.0)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    delta = _normal_cdf(d1) if right == "C" else _normal_cdf(d1) - 1
    gamma = _normal_pdf(d1) / (spot * sigma * sqrt_t)
    vega = spot * _normal_pdf(d1) * sqrt_t / 100
    if right == "C":
        theta_annual = (
            -(spot * _normal_pdf(d1) * sigma) / (2 * sqrt_t)
            - RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * t) * _normal_cdf(d2)
        )
    else:
        theta_annual = (
            -(spot * _normal_pdf(d1) * sigma) / (2 * sqrt_t)
            + RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * t) * _normal_cdf(-d2)
        )
    return {
        "delta": delta,
        "theta": theta_annual / 365,
        "gamma": gamma,
        "vega": vega,
    }


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


def _dte(expiry: str, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        exp = datetime.strptime(expiry, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return -1
    return (exp.date() - now.date()).days


def _parse_option(row: Dict[str, Any], spot: float, fallback_iv: float, now: Optional[datetime] = None) -> Optional[OptionLeg]:
    expiry, strike, right, symbol = _contract_identity(row)
    if not expiry or strike is None or right is None:
        return None
    dte = _dte(expiry, now)
    if dte <= 0:
        return None

    iv = (
        _vol_pct(row.get("implied_volatility"))
        or _vol_pct(row.get("iv"))
        or _vol_pct(row.get("volatility"))
        or fallback_iv
    )
    greeks = bs_greeks(spot, strike, iv, dte, right)
    delta = _canonical_delta(row.get("delta"), right)
    if delta is None:
        delta = greeks["delta"]
    theta = _to_float(row.get("theta"))
    gamma = _to_float(row.get("gamma"))
    vega = _to_float(row.get("vega"))
    return OptionLeg(
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        right=right,
        iv=round(iv, 4),
        delta=round(delta, 6),
        theta=round(theta if theta is not None else greeks["theta"], 6),
        gamma=round(gamma if gamma is not None else greeks["gamma"], 8),
        vega=round(vega if vega is not None else greeks["vega"], 6),
        bid=_to_float(row.get("bid") or row.get("nbbo_bid")),
        ask=_to_float(row.get("ask") or row.get("nbbo_ask")),
        volume=_to_int(row.get("volume")),
        open_interest=_to_int(row.get("open_interest") or row.get("oi")),
    )


def select_short_strangle(
    contracts_payload: Any,
    spot: float,
    fallback_iv: float,
    now: Optional[datetime] = None,
    *,
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
    min_credit: float = 0.0,
) -> Optional[ThetaStructure]:
    rows = _as_rows(contracts_payload)
    options = [
        opt for opt in (_parse_option(row, spot, fallback_iv, now) for row in rows)
        if opt is not None and min_dte <= _dte(opt.expiry, now) <= max_dte
    ]
    calls = [o for o in options if o.right == "C" and o.strike > spot and 0.05 <= o.delta <= 0.35]
    puts = [o for o in options if o.right == "P" and o.strike < spot and 0.05 <= abs(o.delta) <= 0.35]
    if not calls or not puts:
        return None

    best: Optional[Tuple[float, ThetaStructure]] = None
    for call in calls:
        for put in puts:
            if call.expiry != put.expiry:
                continue
            dte = _dte(call.expiry, now)
            short_delta = -call.delta - put.delta
            short_theta = -call.theta - put.theta
            short_gamma = -call.gamma - put.gamma
            short_vega = -call.vega - put.vega
            call_mid = (call.bid + call.ask) / 2 if call.bid is not None and call.ask is not None else None
            put_mid = (put.bid + put.ask) / 2 if put.bid is not None and put.ask is not None else None
            credit = round(call_mid + put_mid, 4) if call_mid is not None and put_mid is not None else None
            # Minimum-credit filter (per-share premium collected). min_credit <= 0
            # disables it; a pair with no quotable credit is skipped when a
            # positive floor is set.
            if min_credit > 0 and (credit is None or credit < min_credit):
                continue
            structure = ThetaStructure(
                expiry=call.expiry,
                dte=dte,
                short_put=put,
                short_call=call,
                net_delta=round(short_delta, 6),
                theta=round(short_theta, 6),
                gamma=round(short_gamma, 8),
                vega=round(short_vega, 6),
                credit=credit,
            )
            score = (
                abs(short_delta) * 100
                + abs(abs(call.delta) - TARGET_DELTA) * 20
                + abs(abs(put.delta) - TARGET_DELTA) * 20
                + abs(dte - 30) / 10
                + (0 if short_theta > 0 else 20)
            )
            if best is None or score < best[0]:
                best = (score, structure)
    return best[1] if best else None


def analyze_dealer_support(strike_payload: Any, spot: float) -> Tuple[str, Optional[float], Optional[float]]:
    rows = _as_rows(strike_payload)
    parsed = []
    for row in rows:
        strike = _to_float(row.get("strike"))
        if strike is None:
            continue
        call_gex = _to_float(row.get("call_gex")) or 0
        put_gex = _to_float(row.get("put_gex")) or 0
        parsed.append({"strike": strike, "net_gex": call_gex + put_gex})
    if not parsed:
        return "UNKNOWN", None, None

    net_gex = sum(row["net_gex"] for row in parsed)
    parsed.sort(key=lambda row: row["strike"])
    flip = None
    for prev, curr in zip(parsed, parsed[1:]):
        if prev["net_gex"] < 0 < curr["net_gex"] and curr["strike"] <= spot:
            flip = curr["strike"]
    support = net_gex > 0 and (flip is None or spot >= flip)
    return ("SUPPORT" if support else "NO_SUPPORT"), round(net_gex, 2), flip


def range_metrics(prices: Sequence[float], hv20: float) -> Tuple[float, float]:
    if len(prices) < 21 or prices[-21] <= 0:
        return 0.0, 0.0
    trend = (prices[-1] / prices[-21] - 1) * 100
    expected = max(hv20 / math.sqrt(TRADING_DAYS) * math.sqrt(20), 0.01)
    range_score = max(0.0, min(1.0, 1 - abs(trend) / (expected * 1.25)))
    return round(trend, 4), round(range_score, 4)


def _score(
    structure: ThetaStructure,
    iv_rv_edge: float,
    iv_rv_ratio: float,
    dealer_support: str,
    range_score: float,
) -> Tuple[float, str, Dict[str, bool], str]:
    delta_gate = abs(structure.net_delta) <= NEAR_ZERO_DELTA
    iv_gate = iv_rv_edge >= 5 or iv_rv_ratio >= 1.10
    dealer_gate = dealer_support == "SUPPORT"
    theta_gate = structure.theta > 0
    gamma_gate = structure.gamma < 0 and abs(structure.net_delta) <= 0.20
    range_gate = range_score >= 0.35

    score = (
        max(0, 25 - abs(structure.net_delta) / NEAR_ZERO_DELTA * 25)
        + max(0, min(25, iv_rv_edge * 2.5))
        + (20 if dealer_gate else 0)
        + (15 if theta_gate else 0)
        + max(0, min(10, range_score * 10))
        + (5 if gamma_gate else 0)
    )
    critical = delta_gate and iv_gate and theta_gate and dealer_gate
    if critical and score >= 70:
        verdict = "THETA_HARVEST"
    elif abs(structure.net_delta) > 0.20 or not iv_gate:
        verdict = "DIRECTIONAL_DISGUISE"
    else:
        verdict = "WATCHLIST"

    setup = "TRUE_THETA" if verdict == "THETA_HARVEST" else verdict
    gates = {
        "delta_near_zero": delta_gate,
        "iv_rich_vs_rv": iv_gate,
        "dealer_support": dealer_gate,
        "theta_positive": theta_gate,
        "gamma_controlled": gamma_gate,
        "range_bound": range_gate,
    }
    return round(score, 2), verdict, gates, setup


def _latest_iv(payload: Any) -> Tuple[Optional[float], Optional[float]]:
    rows = _as_rows(payload)
    if not rows:
        return None, None
    latest = max(rows, key=lambda row: str(row.get("date", "")))
    return _vol_pct(latest.get("volatility")), _to_float(latest.get("iv_rank_1y"))


def _prices_from_ohlc(payload: Any) -> List[float]:
    prices = []
    for row in _as_rows(payload):
        close = _to_float(row.get("close"))
        if close is not None and close > 0:
            prices.append(close)
    return prices


def scan_ticker(
    ticker: str,
    client: Optional[Any] = None,
    now: Optional[datetime] = None,
    *,
    retry_transient: bool = False,
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
    min_credit: float = 0.0,
) -> Optional[ThetaCandidate]:
    own_client = None
    if client is None:
        client_kwargs = {} if retry_transient else {"max_retries": 0, "backoff_factor": 0}
        own_client = UWClient(**client_kwargs)
        client = own_client
    errors: List[str] = []
    try:
        surface = fetch_surface(client, ticker)
        prices = _prices_from_ohlc(surface["ohlc"])
        if len(prices) < 21:
            errors.append("insufficient_price_history")
            return None
        spot = prices[-1]
        hv20 = calculate_hv(prices, 20) or 0.0
        hv60 = calculate_hv(prices, 60) or hv20

        try:
            current_iv, _iv_rank = _latest_iv(surface["iv_rank"])
        except UWRateLimitError:
            raise
        except Exception as exc:
            errors.append(f"iv_rank:{exc}")
            current_iv = None
        if current_iv is None:
            current_iv = hv20

        try:
            contracts = surface["contracts"]
        except UWRateLimitError:
            raise
        except Exception as exc:
            errors.append(f"contracts:{exc}")
            return None
        structure = select_short_strangle(
            contracts, spot, current_iv, now,
            min_dte=min_dte, max_dte=max_dte, min_credit=min_credit,
        )
        if structure is None:
            errors.append("no_short_strangle_pair")
            return None

        try:
            dealer_support, net_gex, gex_flip = analyze_dealer_support(
                surface["gex_strike"],
                spot,
            )
        except UWRateLimitError:
            raise
        except Exception as exc:
            errors.append(f"gex:{exc}")
            dealer_support, net_gex, gex_flip = "UNKNOWN", None, None

        iv_rv_edge = current_iv - hv20
        iv_rv_ratio = current_iv / hv20 if hv20 > 0 else 0.0
        trend, range_score = range_metrics(prices, hv20)
        score, verdict, gates, setup = _score(structure, iv_rv_edge, iv_rv_ratio, dealer_support, range_score)
        return ThetaCandidate(
            ticker=ticker.upper(),
            score=score,
            verdict=verdict,
            structure=structure,
            spot=round(spot, 4),
            iv=round(current_iv, 4),
            hv20=round(hv20, 4),
            hv60=round(hv60, 4),
            iv_rv_edge=round(iv_rv_edge, 4),
            iv_rv_ratio=round(iv_rv_ratio, 4),
            trend_20d_pct=trend,
            range_score=range_score,
            dealer_support=dealer_support,
            net_gex=net_gex,
            gex_flip=gex_flip,
            setup=setup,
            gates=gates,
            errors=errors,
        )
    except UWRateLimitError:
        raise
    except Exception as exc:
        print(f"  {ticker} - ERROR ({exc})", file=sys.stderr)
        return None
    finally:
        if own_client is not None:
            own_client.close()


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


def _slim_earnings_field(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map earnings_dates.annotate payload to the Theta candidate earnings field.

    Missing / unknown → None. Do not include ticker/dte/missing bookkeeping keys.
    """
    if not payload or payload.get("missing") or payload.get("report_date") is None:
        return None
    return {
        "report_date": payload.get("report_date"),
        "report_time": payload.get("report_time"),
        "days_until": payload.get("days_until"),
        "within_dte": payload.get("within_dte"),
        "source": payload.get("source"),
        "expected_move_pct": payload.get("expected_move_pct"),
    }


def annotate_candidates_with_earnings(
    candidates: List[ThetaCandidate],
    client: Any,
    now: Optional[datetime] = None,
) -> List[ThetaCandidate]:
    """Attach earnings risk fields via batch_annotate. Never drops a candidate.

    Uses structure.dte for within_dte. Failures → earnings=None (+ optional
    error note). Does not alter score/verdict/gates.
    """
    if not candidates:
        return candidates
    try:
        import earnings_dates as ed
    except Exception as exc:  # pragma: no cover - import path always present in-repo
        for candidate in candidates:
            candidate.earnings = None
            candidate.errors.append(f"earnings:{exc}")
        return candidates

    items = [{"ticker": c.ticker, "dte": c.structure.dte} for c in candidates]
    try:
        payloads = ed.batch_annotate(client, items, now=now, cache=False)
    except Exception as exc:  # noqa: BLE001 — surface risk only; keep candidates
        for candidate in candidates:
            candidate.earnings = None
            candidate.errors.append(f"earnings:{exc}")
        return candidates

    for candidate, payload in zip(candidates, payloads):
        try:
            candidate.earnings = _slim_earnings_field(payload if isinstance(payload, dict) else {})
        except Exception as exc:  # noqa: BLE001
            candidate.earnings = None
            candidate.errors.append(f"earnings:{exc}")
    return candidates


def build_output(
    results: List[ThetaCandidate],
    source: str,
    universe_count: int,
    requested_tickers: Optional[Sequence[str]] = None,
    params: Optional[Dict[str, Any]] = None,
    coverage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    sorted_results = sorted(results, key=lambda row: row.score, reverse=True)
    return {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "source": "Unusual Whales",
        "universe": source,
        "requested_tickers": list(requested_tickers or []),
        "tickers_scanned": universe_count,
        # Search parameters this scan ran with — surfaced in the UI and used by
        # the FastAPI cooldown so a different DTE/credit search never serves a
        # stale cached snapshot.
        "params": params or {"min_dte": MIN_DTE, "max_dte": MAX_DTE, "min_credit": 0.0},
        "coverage": coverage or coverage_block(
            tickers=universe_count, ok=len(sorted_results),
            no_setup=0, rate_limited=0, errors=0,
        ),
        "candidates_found": len(sorted_results),
        "theta_harvest_count": sum(1 for row in sorted_results if row.verdict == "THETA_HARVEST"),
        "results": [asdict(row) for row in sorted_results],
    }


def scan_universe(
    tickers: Sequence[str],
    preset: Optional[str] = "ndx100",
    limit: Optional[int] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    retry_transient: bool = False,
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
    min_credit: float = 0.0,
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
            "theta-harvester",
            SCAN_STATUS_BUDGET_BLOCKED,
            f"UW daily budget block; universe scan skipped ({source})",
            next_attempt_at=next_quota_reset_iso(),
        )
        prior = _read_cache_file(_CACHE_PATH)
        if payload_has_candidates(prior):
            return {**prior, "scan_status": SCAN_STATUS_BUDGET_BLOCKED}
        blocked = build_output(
            [], source, len(resolved), requested_tickers=resolved,
            params={"min_dte": min_dte, "max_dte": max_dte, "min_credit": min_credit},
            coverage=coverage_block(
                tickers=len(resolved), ok=0, no_setup=0, rate_limited=0, errors=0,
            ),
        )
        blocked["scan_status"] = SCAN_STATUS_BUDGET_BLOCKED
        return blocked
    print(f"Scanning {len(resolved)} tickers for theta harvest ({source})...", file=sys.stderr)

    results: List[ThetaCandidate] = []
    ok = 0
    no_setup = 0
    rate_limited = 0
    errors = 0
    consecutive_rate_limits = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(
                scan_ticker, ticker, retry_transient=retry_transient,
                min_dte=min_dte, max_dte=max_dte, min_credit=min_credit,
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
                print(f"  [{idx}/{len(resolved)}] {ticker} - NO SETUP", file=sys.stderr)
                continue
            ok += 1
            print(f"  [{idx}/{len(resolved)}] {ticker} - {row.verdict} ({row.score:.1f})", file=sys.stderr)
            results.append(row)

    # Earnings annotation is a post-process batch (one UW call per ticker) so
    # failures never drop a theta candidate and we avoid N serial calls inline.
    if results:
        client_kwargs = {} if retry_transient else {"max_retries": 0, "backoff_factor": 0}
        try:
            with UWClient(**client_kwargs) as earnings_client:
                annotate_candidates_with_earnings(results, earnings_client)
        except Exception as exc:  # noqa: BLE001 — surface risk only
            print(f"  earnings annotation skipped ({exc})", file=sys.stderr)
            for row in results:
                if row.earnings is None and not any(e.startswith("earnings:") for e in row.errors):
                    row.errors.append(f"earnings:{exc}")

    payload = build_output(
        results, source, len(resolved), requested_tickers=resolved,
        params={"min_dte": min_dte, "max_dte": max_dte, "min_credit": min_credit},
        coverage=coverage_block(
            tickers=len(resolved), ok=ok, no_setup=no_setup,
            rate_limited=rate_limited, errors=errors,
        ),
    )
    if not tickers and is_coverage_failed(payload):
        payload["scan_status"] = SCAN_STATUS_COVERAGE_FAILED
        cov = payload["coverage"]
        record_scan_degraded(
            "theta-harvester",
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
        print("Empty theta scan — preserving last good cache", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)
    mirror_scan_snapshot("theta-harvester", payload)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan for short-strangle theta harvest candidates.")
    parser.add_argument("tickers", nargs="*", help="Tickers to scan. Overrides --preset.")
    parser.add_argument("--preset", default="ndx100", help="Preset name from data/presets (default: ndx100).")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for smoke/validation runs.")
    parser.add_argument("--min-dte", type=int, default=MIN_DTE, help=f"Minimum days-to-expiration (default {MIN_DTE}).")
    parser.add_argument("--max-dte", type=int, default=MAX_DTE, help=f"Maximum days-to-expiration (default {MAX_DTE}).")
    parser.add_argument(
        "--min-credit", type=float, default=0.0,
        help="Minimum per-share credit collected on the strangle (default 0 = no filter).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_env_int("RADON_THETA_SCANNER_WORKERS", DEFAULT_MAX_WORKERS),
        help=f"Concurrent workers (default {DEFAULT_MAX_WORKERS}; override RADON_THETA_SCANNER_WORKERS).",
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
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        min_credit=args.min_credit,
    )
    save_cache(payload, Path(args.output))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Theta harvester scan complete: {payload['candidates_found']} candidates")


if __name__ == "__main__":
    main()

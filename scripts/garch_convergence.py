#!/usr/bin/env python3
"""GARCH Convergence Spread Scanner.

Scans correlated asset pairs for cross-asset volatility repricing lags.
Fetches all IV/HV data in parallel, computes divergence metrics, and
generates an HTML report.

Strategy spec: docs/strategy-garch-convergence.md

Usage:
    # Inline pairs
    python3 scripts/garch_convergence.py NVDA AMD GOOGL META

    # Built-in presets
    python3 scripts/garch_convergence.py --preset semis
    python3 scripts/garch_convergence.py --preset mega-tech
    python3 scripts/garch_convergence.py --preset energy
    python3 scripts/garch_convergence.py --preset all     # Run all built-in

    # File presets (from data/presets/)
    python3 scripts/garch_convergence.py --preset sp500-semiconductors
    python3 scripts/garch_convergence.py --preset ndx100-biotech

    # Options
    python3 scripts/garch_convergence.py --preset semis --json
    python3 scripts/garch_convergence.py --preset semis --no-open
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from clients.uw_client import UWClient, UWAPIError

# ── built-in pair presets ─────────────────────────────────────────
PAIR_PRESETS: Dict[str, Dict[str, Any]] = {
    "semis": {
        "description": "Semiconductors",
        "pairs": [["NVDA", "AMD"], ["TSM", "ASML"], ["AVGO", "QCOM"], ["MU", "AMAT"]],
        "vol_driver": "AI/cloud capex, memory demand, equipment cycle",
    },
    "mega-tech": {
        "description": "Mega-Cap Tech",
        "pairs": [["AAPL", "MSFT"], ["GOOGL", "META"], ["AMZN", "NFLX"]],
        "vol_driver": "Ad spend, cloud growth, consumer tech cycle",
    },
    "energy": {
        "description": "Energy",
        "pairs": [["XOM", "COP"], ["SLB", "HAL"], ["XLE", "OIH"]],
        "vol_driver": "Oil/gas prices, OPEC policy, drilling activity",
    },
    "china-etf": {
        "description": "China / Asia",
        "pairs": [["FXI", "BABA"], ["EWY", "FXI"]],
        "vol_driver": "China policy, trade tariffs, geopolitics",
    },
}

# ── data classes ──────────────────────────────────────────────────

@dataclass
class TickerVol:
    """IV/HV data for a single ticker."""
    ticker: str
    price: float = 0.0
    hv20: float = 0.0
    hv60: float = 0.0
    hv252: float = 0.0
    leap_atm_iv: float = 0.0      # ATM LEAP IV (~50Δ)
    leap_30d_iv: float = 0.0      # 30Δ LEAP IV
    iv_rank: float = 0.0
    current_iv: float = 0.0
    leap_count: int = 0
    has_leaps: bool = False
    error: Optional[str] = None

    @property
    def iv_hv60(self) -> float:
        return self.leap_atm_iv / self.hv60 if self.hv60 > 0 else 0.0

    @property
    def hv20_minus_iv(self) -> float:
        return self.hv20 - self.leap_atm_iv


@dataclass
class PairAnalysis:
    """GARCH divergence analysis for a correlated pair."""
    ticker_a: str
    ticker_b: str
    vol_a: Optional[TickerVol] = None
    vol_b: Optional[TickerVol] = None
    leader: str = ""
    lagger: str = ""
    divergence: float = 0.0           # IV/HV ratio A - IV/HV ratio B
    lagger_hv_iv_gap: float = 0.0     # lagger HV20 - lagger LEAP IV
    lagger_iv_rank: float = 0.0
    shared_vol_driver: str = ""
    # Gate pass/fail
    gate_divergence: bool = False
    gate_hv_gap: bool = False          # HV20 - IV ≥ 10
    gate_vol_driver: bool = True       # Always True for preset pairs
    gate_iv_rank: bool = False         # IV rank < 50%
    gate_liquidity: bool = True        # Assume True (LEAPs exist)
    signal: str = "NONE"              # STRONG / MODERATE / WEAK / NONE
    failing_gates: List[str] = field(default_factory=list)
    # Expected convergence
    expected_iv: float = 0.0
    expected_move: float = 0.0

    @property
    def all_gates_pass(self) -> bool:
        return all([
            self.gate_divergence, self.gate_hv_gap, self.gate_vol_driver,
            self.gate_iv_rank, self.gate_liquidity,
        ])


# ── Yahoo Finance HV fetch ───────────────────────────────────────

def _fetch_yahoo_prices(ticker: str, days: int = 400) -> List[float]:
    """Fetch daily closes from Yahoo. Returns list of floats."""
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1={start}&period2={end}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [c for c in closes if c is not None]
    except Exception:
        return []


def _calc_hv(prices: List[float], period: int) -> float:
    """Annualized historical volatility from log returns."""
    if len(prices) < period + 1:
        return 0.0
    recent = prices[-(period + 1):]
    returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent)) if recent[i - 1] > 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


# ── UW data fetchers ─────────────────────────────────────────────

def _fetch_uw_iv(ticker: str, client: UWClient) -> Tuple[float, float]:
    """Current IV and IV rank from UW. Returns (iv%, rank%)."""
    try:
        data = client.get_iv_rank(ticker)
        if "data" in data and data["data"]:
            latest = data["data"][0]
            iv = float(latest.get("volatility", 0)) * 100
            rank = float(latest.get("iv_rank_1y", 0))
            return iv, rank
    except UWAPIError:
        pass
    return 0.0, 0.0


def _fetch_uw_leaps(ticker: str, client: UWClient, min_year: int = 2027) -> List[Dict]:
    """Fetch LEAP call options from UW. Returns list of dicts with iv, strike, delta_approx."""
    try:
        resp = client.get_option_contracts(ticker)
    except UWAPIError:
        return []

    results = []
    for c in resp.get("data", []):
        sym = c.get("option_symbol", "")
        try:
            # Parse OCC symbol: TICKER + YYMMDD + C/P + STRIKE*1000
            date_start = None
            for i in range(3, len(sym)):
                if sym[i : i + 2].isdigit():
                    date_start = i
                    break
            if date_start is None:
                continue
            year = int("20" + sym[date_start : date_start + 2])
            if year < min_year:
                continue
            right_idx = date_start + 6
            if sym[right_idx] != "C":
                continue
            strike = int(sym[right_idx + 1 :]) / 1000
            iv = float(c.get("implied_volatility", 0)) * 100
            if iv == 0:
                continue
            results.append({"strike": strike, "iv": iv, "oi": int(c.get("open_interest", 0))})
        except (ValueError, IndexError):
            continue
    return results


# ── Parallel ticker data fetcher ─────────────────────────────────

def fetch_ticker_vol(ticker: str, uw_client: UWClient) -> TickerVol:
    """Fetch all vol data for one ticker. Thread-safe (uses shared UWClient session)."""
    tv = TickerVol(ticker=ticker)

    # Yahoo prices (blocking I/O — but runs in thread pool)
    prices = _fetch_yahoo_prices(ticker)
    if len(prices) < 60:
        tv.error = "Insufficient price data"
        return tv

    tv.price = prices[-1]
    tv.hv20 = round(_calc_hv(prices, 20), 2)
    tv.hv60 = round(_calc_hv(prices, 60), 2)
    tv.hv252 = round(_calc_hv(prices, 252), 2)

    # UW: IV rank + current IV
    tv.current_iv, tv.iv_rank = _fetch_uw_iv(ticker, uw_client)

    # UW: LEAP options
    leaps = _fetch_uw_leaps(ticker, uw_client)
    tv.leap_count = len(leaps)
    tv.has_leaps = len(leaps) > 0

    if leaps and tv.price > 0:
        # Bucket by approximate delta using moneyness
        atm, d30 = [], []
        for lp in leaps:
            m = tv.price / lp["strike"]
            if 0.90 <= m <= 1.10:
                atm.append(lp["iv"])
            elif 0.75 <= m < 0.90:
                d30.append(lp["iv"])
        tv.leap_atm_iv = round(sum(atm) / len(atm), 2) if atm else 0.0
        tv.leap_30d_iv = round(sum(d30) / len(d30), 2) if d30 else 0.0
        # Fallback: use overall median if no ATM bucket
        if tv.leap_atm_iv == 0 and leaps:
            all_ivs = sorted(lp["iv"] for lp in leaps)
            tv.leap_atm_iv = round(all_ivs[len(all_ivs) // 2], 2)

    return tv


def fetch_all_tickers(tickers: List[str], max_workers: int = 8) -> Dict[str, TickerVol]:
    """Fetch vol data for ALL tickers in parallel.

    Uses one shared UWClient (connection-pooled session) across threads,
    plus concurrent Yahoo Finance HTTP requests.
    """
    results: Dict[str, TickerVol] = {}
    t0 = time.time()

    with UWClient() as uw:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_ticker_vol, t, uw): t for t in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    results[ticker] = future.result()
                except Exception as exc:
                    results[ticker] = TickerVol(ticker=ticker, error=str(exc))

    elapsed = time.time() - t0
    print(f"  ✓ Fetched {len(results)} tickers in {elapsed:.1f}s", file=sys.stderr)
    return results


# ── Pair analysis ─────────────────────────────────────────────────

def analyze_pair(
    a: str, b: str, vol_data: Dict[str, TickerVol], vol_driver: str = ""
) -> PairAnalysis:
    """Compute GARCH divergence metrics for a pair."""
    pa = PairAnalysis(ticker_a=a, ticker_b=b, shared_vol_driver=vol_driver)
    va = vol_data.get(a)
    vb = vol_data.get(b)
    pa.vol_a = va
    pa.vol_b = vb

    # Need both sides with LEAP data
    if not va or not vb:
        pa.failing_gates.append("MISSING_DATA")
        return pa
    if not va.has_leaps:
        pa.failing_gates.append(f"{a}: no LEAPs")
        return pa
    if not vb.has_leaps:
        pa.failing_gates.append(f"{b}: no LEAPs")
        return pa

    # Determine leader/lagger by IV/HV60 ratio (higher = leader)
    ratio_a = va.iv_hv60
    ratio_b = vb.iv_hv60
    if ratio_a >= ratio_b:
        pa.leader, pa.lagger = a, b
        leader_vol, lagger_vol = va, vb
    else:
        pa.leader, pa.lagger = b, a
        leader_vol, lagger_vol = vb, va

    pa.divergence = round(leader_vol.iv_hv60 - lagger_vol.iv_hv60, 3)
    pa.lagger_hv_iv_gap = round(lagger_vol.hv20 - lagger_vol.leap_atm_iv, 1)
    pa.lagger_iv_rank = lagger_vol.iv_rank

    # Expected convergence
    if leader_vol.iv_hv60 > 0 and lagger_vol.hv60 > 0:
        pa.expected_iv = round(leader_vol.iv_hv60 * lagger_vol.hv60, 1)
        pa.expected_move = round(pa.expected_iv - lagger_vol.leap_atm_iv, 1)

    # ── Gate checks ──
    # 1. IV ratio divergence: leader ≥ 1.0, lagger < 0.85 (strict) OR divergence > 0.15 (relaxed)
    pa.gate_divergence = (leader_vol.iv_hv60 >= 1.0 and pa.divergence >= 0.15)
    if not pa.gate_divergence:
        pa.failing_gates.append(f"Divergence {pa.divergence:.2f} (leader IV/HV {leader_vol.iv_hv60:.2f})")

    # 2. Lagger HV20 > LEAP IV by ≥ 10 points
    pa.gate_hv_gap = pa.lagger_hv_iv_gap >= 10.0
    if not pa.gate_hv_gap:
        pa.failing_gates.append(f"HV20−IV = {pa.lagger_hv_iv_gap:+.1f} pts (need ≥+10)")

    # 3. Shared vol driver (always True for preset pairs, assumed for ad-hoc)
    pa.gate_vol_driver = bool(vol_driver)
    if not pa.gate_vol_driver:
        pa.failing_gates.append("No confirmed shared vol driver")

    # 4. Lagger IV rank < 50%
    pa.gate_iv_rank = lagger_vol.iv_rank < 50.0
    if not pa.gate_iv_rank:
        pa.failing_gates.append(f"Lagger IV rank {lagger_vol.iv_rank:.0f}% (need <50%)")

    # 5. Liquidity: has LEAPs is already checked
    pa.gate_liquidity = lagger_vol.has_leaps

    # Signal tier
    if pa.all_gates_pass:
        if pa.divergence >= 0.30 and pa.lagger_hv_iv_gap >= 20 and pa.lagger_iv_rank < 30:
            pa.signal = "STRONG"
        elif pa.divergence >= 0.20 and pa.lagger_hv_iv_gap >= 15 and pa.lagger_iv_rank < 40:
            pa.signal = "MODERATE"
        else:
            pa.signal = "WEAK"
    else:
        pa.signal = "NONE"

    return pa


# ── Resolve tickers + pairs from CLI args ─────────────────────────

def resolve_inputs(
    tickers: List[str], preset_name: Optional[str]
) -> Tuple[List[str], List[List[str]], str, str]:
    """Return (unique_tickers, pairs, description, vol_driver)."""
    all_tickers: List[str] = []
    all_pairs: List[List[str]] = []
    descriptions: List[str] = []
    vol_drivers: List[str] = []

    if preset_name:
        names = list(PAIR_PRESETS.keys()) if preset_name == "all" else [preset_name]
        for name in names:
            if name in PAIR_PRESETS:
                p = PAIR_PRESETS[name]
                all_pairs.extend(p["pairs"])
                descriptions.append(p["description"])
                vol_drivers.append(p.get("vol_driver", ""))
            else:
                # Try file preset
                try:
                    from utils.presets import load_preset
                    fp = load_preset(name)
                    all_pairs.extend(fp.pairs)
                    descriptions.append(fp.description)
                    vol_drivers.append(fp.vol_driver)
                except (FileNotFoundError, ImportError) as exc:
                    print(f"⚠ Preset '{name}' not found: {exc}", file=sys.stderr)

    if tickers and not all_pairs:
        # Ad-hoc: make consecutive pairs
        for i in range(0, len(tickers) - 1, 2):
            all_pairs.append([tickers[i], tickers[i + 1]])

    # Dedupe tickers preserving order
    seen = set()
    for pair in all_pairs:
        for t in pair:
            if t not in seen:
                all_tickers.append(t)
                seen.add(t)

    desc = " · ".join(descriptions) if descriptions else "Ad-hoc pairs"
    driver = " | ".join(d for d in vol_drivers if d) or ""
    return all_tickers, all_pairs, desc, driver


# ── HTML report generation ────────────────────────────────────────

def _pill(text: str, kind: str = "") -> str:
    cls = f"pill pill-{kind}" if kind else "pill"
    return f'<span class="{cls}">{text}</span>'


def _gate_icon(passed: bool) -> str:
    return '<span class="text-positive">✅</span>' if passed else '<span class="text-negative">❌</span>'


def _signal_pill(sig: str) -> str:
    m = {"STRONG": "positive", "MODERATE": "warning", "WEAK": "warning", "NONE": "negative"}
    return _pill(sig, m.get(sig, ""))


def _iv_status(tv: TickerVol) -> str:
    if not tv.has_leaps:
        return _pill("No LEAPs", "")
    gap = tv.hv20_minus_iv
    if gap >= 10:
        return _pill("Cheap", "positive")
    elif gap >= 0:
        return _pill("Fair", "")
    elif gap >= -5:
        return _pill("Slight Prem", "")
    elif gap >= -10:
        return _pill("Expensive", "negative")
    else:
        return _pill("V. Expensive", "negative")


def generate_html(
    all_vol: Dict[str, TickerVol],
    pairs: List[PairAnalysis],
    description: str,
    elapsed: float,
) -> str:
    """Build full HTML report."""
    template_path = _PROJECT_DIR / ".pi/skills/html-report/template.html"
    template = template_path.read_text()

    total_tickers = len(all_vol)
    with_leaps = sum(1 for v in all_vol.values() if v.has_leaps)
    actionable = sum(1 for p in pairs if p.all_gates_pass)
    nearest = max(pairs, key=lambda p: p.lagger_hv_iv_gap) if pairs else None
    nearest_label = f"{nearest.lagger} (+{nearest.lagger_hv_iv_gap:.0f})" if nearest and nearest.lagger_hv_iv_gap > 0 else "—"

    now = datetime.now().strftime("%Y-%m-%d %I:%M %p PST")

    body_parts: List[str] = []

    # ── header ──
    body_parts.append(f"""
<header class="header">
  <div>
    <h1 class="title">GARCH Convergence Scan</h1>
    <p class="subtitle">{description} — {total_tickers} Tickers, {len(pairs)} Pairs</p>
  </div>
  <div class="header-actions">
    <span class="timestamp">Generated: {now} ({elapsed:.1f}s)</span>
    <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
  </div>
</header>""")

    # ── metrics ──
    body_parts.append(f"""
<div class="metrics">
  <div class="metric">
    <div class="metric-label">Tickers Scanned</div>
    <div class="metric-value">{total_tickers}</div>
  </div>
  <div class="metric">
    <div class="metric-label">With LEAPs</div>
    <div class="metric-value">{with_leaps}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Actionable Pairs</div>
    <div class="metric-value {"text-positive" if actionable else "text-negative"}">{actionable}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Nearest Lagger</div>
    <div class="metric-value text-warning" style="font-size:18px">{nearest_label}</div>
  </div>
</div>""")

    # ── IV/HV table ──
    body_parts.append("""
<div class="section-header">Individual Ticker Volatility</div>
<div class="panel"><table>
<thead><tr>
  <th>Ticker</th><th class="text-right">Price</th>
  <th class="text-right">HV20</th><th class="text-right">HV60</th>
  <th class="text-right">LEAP ATM IV</th><th class="text-right">IV/HV60</th>
  <th class="text-right">HV20−IV</th><th class="text-right">IV Rank</th>
  <th class="text-center">Status</th>
</tr></thead><tbody>""")

    for ticker in sorted(all_vol.keys()):
        tv = all_vol[ticker]
        if tv.error:
            body_parts.append(f'<tr><td><strong>{ticker}</strong></td><td colspan="8" class="text-muted">{tv.error}</td></tr>')
            continue
        ratio = tv.iv_hv60
        gap = tv.hv20_minus_iv
        ratio_cls = "text-positive" if ratio < 1.0 else "text-negative" if ratio > 1.15 else ""
        gap_cls = "text-positive" if gap >= 0 else "text-negative"
        iv_val = f"{tv.leap_atm_iv:.1f}%" if tv.has_leaps else '<span class="text-muted">—</span>'
        ratio_val = f"{ratio:.2f}" if tv.has_leaps else '<span class="text-muted">—</span>'
        gap_val = f"{gap:+.1f}" if tv.has_leaps else '<span class="text-muted">—</span>'
        hl = ' class="highlight"' if gap >= 5 else ""
        body_parts.append(
            f'<tr{hl}><td><strong>{ticker}</strong></td>'
            f'<td class="text-right">${tv.price:,.2f}</td>'
            f'<td class="text-right">{tv.hv20:.1f}%</td>'
            f'<td class="text-right">{tv.hv60:.1f}%</td>'
            f'<td class="text-right">{iv_val}</td>'
            f'<td class="text-right {ratio_cls}">{ratio_val}</td>'
            f'<td class="text-right {gap_cls}">{gap_val}</td>'
            f'<td class="text-right">{tv.iv_rank:.1f}</td>'
            f'<td class="text-center">{_iv_status(tv)}</td></tr>'
        )

    body_parts.append("</tbody></table></div>")

    # ── pair analysis panels ──
    body_parts.append('<hr class="divider"><div class="section-header">Pair Divergence Analysis</div>')

    for pa in pairs:
        signal_pill = _signal_pill(pa.signal)
        body_parts.append(f"""
<div class="panel" style="margin-bottom:16px">
  <div class="panel-header"><span>{pa.ticker_a} ↔ {pa.ticker_b}</span>{signal_pill}</div>
  <div class="panel-body">
    <table>
      <tr><th></th><th class="text-right">IV/HV60</th><th class="text-right">HV20−IV</th><th class="text-right">IV Rank</th></tr>""")

        for role, tk in [("leader", pa.leader), ("lagger", pa.lagger)]:
            tv = pa.vol_a if tk == pa.ticker_a else pa.vol_b
            if tv and tv.has_leaps:
                body_parts.append(
                    f'<tr><td>{tk} <span class="text-muted">({role})</span></td>'
                    f'<td class="text-right">{tv.iv_hv60:.2f}</td>'
                    f'<td class="text-right">{tv.hv20_minus_iv:+.1f}</td>'
                    f'<td class="text-right">{tv.iv_rank:.0f}</td></tr>'
                )
            else:
                body_parts.append(
                    f'<tr><td>{tk} <span class="text-muted">({role})</span></td>'
                    f'<td colspan="3" class="text-muted">No LEAP data</td></tr>'
                )

        body_parts.append(
            f'<tr style="border-top:2px solid var(--border-focus)">'
            f'<td><strong>Divergence</strong></td>'
            f'<td class="text-right"><strong>{pa.divergence:.2f}</strong></td>'
            f'<td class="text-right"><strong>{pa.lagger_hv_iv_gap:+.1f}</strong></td>'
            f'<td></td></tr></table>'
        )

        if pa.expected_move and pa.expected_move > 0:
            body_parts.append(
                f'<div style="margin-top:8px;font-size:11px;color:var(--text-muted)">'
                f'Expected convergence: lagger IV → {pa.expected_iv:.0f}% (+{pa.expected_move:.0f} pts)</div>'
            )

        # Gate result callout
        if pa.all_gates_pass:
            body_parts.append(
                f'<div class="callout positive" style="margin-top:12px">'
                f'<div class="callout-title">All gates pass — {pa.signal} signal</div>'
                f'Lagger {pa.lagger} LEAP IV underpriced by {pa.lagger_hv_iv_gap:.0f} pts vs HV20. '
                f'Expected {pa.expected_move:.0f} pt IV expansion.</div>'
            )
        else:
            reason = pa.failing_gates[0] if pa.failing_gates else "Unknown"
            body_parts.append(
                f'<div class="callout negative" style="margin-top:12px">'
                f'<div class="callout-title">Gate Fail: {reason}</div>'
                + (f'Additional: {"; ".join(pa.failing_gates[1:])}' if len(pa.failing_gates) > 1 else "")
                + "</div>"
            )

        body_parts.append("</div></div>")

    # ── scorecard table ──
    body_parts.append('<hr class="divider"><div class="section-header">Signal Criteria Scorecard</div>')
    body_parts.append("""
<div class="panel"><table>
<thead><tr>
  <th>Pair</th><th class="text-center">Divergence</th>
  <th class="text-center">HV&gt;IV ≥10</th><th class="text-center">Vol Driver</th>
  <th class="text-center">IV Rank &lt;50%</th><th class="text-center">LEAPs</th>
  <th class="text-center">Verdict</th>
</tr></thead><tbody>""")

    for pa in pairs:
        hl = ' class="highlight"' if pa.all_gates_pass else ""
        body_parts.append(
            f'<tr{hl}><td>{pa.ticker_a} ↔ {pa.ticker_b}</td>'
            f'<td class="text-center">{_gate_icon(pa.gate_divergence)} {pa.divergence:.2f}</td>'
            f'<td class="text-center">{_gate_icon(pa.gate_hv_gap)} {pa.lagger_hv_iv_gap:+.1f}</td>'
            f'<td class="text-center">{_gate_icon(pa.gate_vol_driver)}</td>'
            f'<td class="text-center">{_gate_icon(pa.gate_iv_rank)} {pa.lagger_iv_rank:.0f}%</td>'
            f'<td class="text-center">{_gate_icon(pa.gate_liquidity)}</td>'
            f'<td class="text-center">{_signal_pill(pa.signal)}</td></tr>'
        )

    body_parts.append("</tbody></table></div>")

    # ── watchlist ──
    watchable = [pa for pa in pairs if pa.lagger_hv_iv_gap > 0 or pa.divergence > 0.20]
    if watchable:
        body_parts.append('<div class="section-header">Watchlist — Monitor for Changes</div>')
        body_parts.append('<div class="panel"><div class="panel-body"><table>')
        body_parts.append(
            "<thead><tr><th>Lagger</th><th>Why</th>"
            '<th class="text-right">HV20−IV</th><th class="text-right">IV Rank</th></tr></thead><tbody>'
        )
        for pa in sorted(watchable, key=lambda p: p.lagger_hv_iv_gap, reverse=True):
            gap_cls = "text-positive" if pa.lagger_hv_iv_gap > 0 else "text-negative"
            body_parts.append(
                f"<tr><td><strong>{pa.lagger}</strong></td>"
                f"<td>Pair: {pa.leader}↔{pa.lagger}. Divergence {pa.divergence:.2f}.</td>"
                f'<td class="text-right {gap_cls}">{pa.lagger_hv_iv_gap:+.1f}</td>'
                f'<td class="text-right">{pa.lagger_iv_rank:.0f}%</td></tr>'
            )
        body_parts.append("</tbody></table></div></div>")

    # ── footer ──
    body_parts.append(
        f'<div class="footer">'
        f"<strong>GARCH Convergence Spread Scan</strong> · {total_tickers} tickers · {len(pairs)} pairs<br>"
        f"Data: Yahoo Finance (HV) · Unusual Whales (LEAP IV, IV Rank)<br>"
        f"Strategy spec: <code>docs/strategy-garch-convergence.md</code> · {now}<br><br>"
        f"<em>{'No trades recommended.' if actionable == 0 else f'{actionable} actionable pair(s) found.'}</em></div>"
    )

    body = "\n".join(body_parts)
    html = template.replace("{{TITLE}}", f"GARCH Convergence — {description} | {datetime.now().strftime('%Y-%m-%d')}")
    html = html.replace("{{BODY}}", body)
    return html


# ── JSON output ───────────────────────────────────────────────────

def to_json(all_vol: Dict[str, TickerVol], pairs: List[PairAnalysis]) -> Dict:
    return {
        "scan_time": datetime.now().isoformat(),
        "tickers": {
            t: {
                "price": v.price, "hv20": v.hv20, "hv60": v.hv60, "hv252": v.hv252,
                "leap_atm_iv": v.leap_atm_iv, "iv_rank": v.iv_rank,
                "iv_hv60": round(v.iv_hv60, 3), "hv20_minus_iv": round(v.hv20_minus_iv, 1),
                "has_leaps": v.has_leaps, "leap_count": v.leap_count,
            }
            for t, v in all_vol.items()
        },
        "pairs": [
            {
                "pair": [pa.ticker_a, pa.ticker_b],
                "leader": pa.leader, "lagger": pa.lagger,
                "divergence": pa.divergence,
                "lagger_hv_iv_gap": pa.lagger_hv_iv_gap,
                "lagger_iv_rank": pa.lagger_iv_rank,
                "signal": pa.signal,
                "gates_passed": pa.all_gates_pass,
                "failing_gates": pa.failing_gates,
                "expected_iv": pa.expected_iv,
                "expected_move": pa.expected_move,
            }
            for pa in pairs
        ],
    }


# ── CLI entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GARCH Convergence Spread Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Built-in presets:
  semis       (NVDA,AMD), (TSM,ASML), (AVGO,QCOM), (MU,AMAT)
  mega-tech   (AAPL,MSFT), (GOOGL,META), (AMZN,NFLX)
  energy      (XOM,COP), (SLB,HAL), (XLE,OIH)
  china-etf   (FXI,BABA), (EWY,FXI)
  all         Run all built-in presets

Also supports any data/presets/ file preset (e.g. sp500-semiconductors).

Examples:
  python3 scripts/garch_convergence.py --preset all
  python3 scripts/garch_convergence.py --preset semis --json
  python3 scripts/garch_convergence.py NVDA AMD GOOGL META
""",
    )
    parser.add_argument("tickers", nargs="*", help="Tickers (paired consecutively)")
    parser.add_argument("--preset", "-p", help="Pair preset name (or 'all')")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of HTML")
    parser.add_argument("--no-open", action="store_true", help="Don't open report in browser")
    parser.add_argument("--output", "-o", help="Custom output path")
    parser.add_argument("--workers", type=int, default=8, help="Parallel worker threads (default 8)")

    args = parser.parse_args()

    if not args.tickers and not args.preset:
        parser.print_help()
        sys.exit(1)

    tickers_in = [t.upper() for t in args.tickers] if args.tickers else []

    # ── Step 1: Resolve inputs ──
    all_tickers, all_pairs, description, vol_driver = resolve_inputs(tickers_in, args.preset)

    if not all_pairs:
        print("❌ No pairs to analyze.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"GARCH CONVERGENCE SCAN — {description}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Tickers: {len(all_tickers)} | Pairs: {len(all_pairs)} | Workers: {args.workers}", file=sys.stderr)

    t_start = time.time()

    # ── Step 2: Parallel data fetch (ALL tickers at once) ──
    print(f"\n  Fetching IV/HV data for {len(all_tickers)} tickers in parallel...", file=sys.stderr)
    vol_data = fetch_all_tickers(all_tickers, max_workers=args.workers)

    # ── Step 3: Analyze all pairs (CPU-only, instant) ──
    pair_results: List[PairAnalysis] = []
    for a, b in all_pairs:
        # Determine vol driver for this specific pair
        driver = vol_driver
        for preset_name, preset_data in PAIR_PRESETS.items():
            if [a, b] in preset_data["pairs"] or [b, a] in preset_data["pairs"]:
                driver = preset_data.get("vol_driver", driver)
                break
        pair_results.append(analyze_pair(a, b, vol_data, vol_driver=driver))

    elapsed = time.time() - t_start

    # ── Print summary to stderr ──
    actionable = [p for p in pair_results if p.all_gates_pass]
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"SCAN COMPLETE — {elapsed:.1f}s", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Pairs analyzed: {len(pair_results)}", file=sys.stderr)
    print(f"  Actionable:     {len(actionable)}", file=sys.stderr)

    for pa in pair_results:
        icon = "✅" if pa.all_gates_pass else "❌"
        print(
            f"  {icon} {pa.ticker_a}↔{pa.ticker_b}: "
            f"div={pa.divergence:.2f}, gap={pa.lagger_hv_iv_gap:+.1f}, "
            f"signal={pa.signal}",
            file=sys.stderr,
        )

    # ── Step 4: Output ──
    if args.json:
        print(json.dumps(to_json(vol_data, pair_results), indent=2))
    else:
        html = generate_html(vol_data, pair_results, description, elapsed)
        date_str = datetime.now().strftime("%Y-%m-%d")
        preset_slug = (args.preset or "adhoc").replace(" ", "-")
        out_path = Path(args.output) if args.output else _PROJECT_DIR / f"reports/garch-convergence-{preset_slug}-{date_str}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html)
        print(f"\n✓ Report: {out_path}", file=sys.stderr)

        if not args.no_open:
            webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()

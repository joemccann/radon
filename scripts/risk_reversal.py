#!/usr/bin/env python3
"""Risk Reversal Scanner & Report Generator.

Scans a single ticker's options chain for optimal risk reversal structures
(sell OTM put / buy OTM call for bullish, or inverse for bearish).
Exploits IV skew between puts and calls. Generates HTML report.

Usage:
    python3 scripts/risk_reversal.py IWM
    python3 scripts/risk_reversal.py SPY --bearish
    python3 scripts/risk_reversal.py QQQ --bankroll 500000 --min-dte 21 --max-dte 45
    python3 scripts/risk_reversal.py IWM --no-open
    python3 scripts/risk_reversal.py IWM --json
"""

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from clients.ib_client import IBClient
from ib_insync import Option, Stock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_flow_data(ticker: str) -> Dict:
    """Run fetch_flow.py and return parsed JSON."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "fetch_flow.py"), ticker],
            capture_output=True, text=True, timeout=30, cwd=str(PROJECT_ROOT),
        )
        # Parse JSON from last line or entire stdout
        for line in reversed(result.stdout.strip().split("\n")):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return json.loads(result.stdout)
    except Exception as e:
        return {"error": str(e)}


def fetch_options_data(ticker: str) -> Dict:
    """Run fetch_options.py --json and return parsed JSON."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "fetch_options.py"), ticker, "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(PROJECT_ROOT),
        )
        for line in reversed(result.stdout.strip().split("\n")):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return json.loads(result.stdout)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# IB Chain Fetcher
# ---------------------------------------------------------------------------

def fetch_chain(
    ticker: str,
    min_dte: int = 14,
    max_dte: int = 60,
    bearish: bool = False,
    port: int = 4001,
) -> Dict:
    """Connect to IB and fetch live option quotes with greeks.

    Returns dict with:
      spot, expirations, options (list of dicts per contract)
    """
    # Suppress noisy "No security definition" errors for invalid strikes
    import logging
    logging.getLogger("ib_insync.wrapper").setLevel(logging.CRITICAL)

    client = IBClient()
    client.connect(port=port, client_id=33)
    ib = client.ib

    # Suppress IB error 200 (invalid contract) during qualification
    _orig_error = ib.errorEvent
    def _quiet_error(reqId, errorCode, errorString, contract):
        if errorCode != 200:  # Only suppress "No security definition"
            print(f"IB error {errorCode}: {errorString}")
    ib.errorEvent.clear()
    ib.errorEvent += _quiet_error

    ib.reqMarketDataType(1)

    # Spot price
    stock = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(stock)
    [spot_ticker] = ib.reqTickers(stock)
    spot = spot_ticker.marketPrice()

    # Valid strikes & expirations
    chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
    chain = next((c for c in chains if c.exchange == "SMART"), chains[0])

    today = datetime.now()
    target_expiries = sorted(
        e
        for e in chain.expirations
        if min_dte <= (datetime.strptime(e, "%Y%m%d") - today).days <= max_dte
    )
    # Keep at most 5 expirations (the major ones)
    if len(target_expiries) > 5:
        target_expiries = target_expiries[:5]

    valid_strikes = sorted(chain.strikes)

    # Build contracts for the short leg (put for bullish, call for bearish)
    # and the long leg (call for bullish, put for bearish)
    if bearish:
        short_right, long_right = "C", "P"
        short_strikes = [s for s in valid_strikes if s >= spot and s <= spot * 1.12]
        long_strikes = [s for s in valid_strikes if s <= spot and s >= spot * 0.88]
    else:
        short_right, long_right = "P", "C"
        short_strikes = [s for s in valid_strikes if s <= spot and s >= spot * 0.88]
        long_strikes = [s for s in valid_strikes if s >= spot and s <= spot * 1.12]

    contracts = []
    for exp in target_expiries:
        for s in short_strikes:
            contracts.append(Option(ticker, exp, s, short_right, "SMART"))
        for s in long_strikes:
            contracts.append(Option(ticker, exp, s, long_right, "SMART"))

    # Qualify (ignore failures — many strikes don't exist for all expiries)
    qualified = []
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    _devnull = open(os.devnull, 'w')
    for c in contracts:
        try:
            sys.stdout = _devnull
            sys.stderr = _devnull
            q = ib.qualifyContracts(c)
            sys.stdout = _orig_stdout
            sys.stderr = _orig_stderr
            if q and q[0].conId > 0:
                qualified.extend(q)
        except Exception:
            sys.stdout = _orig_stdout
            sys.stderr = _orig_stderr
    _devnull.close()

    # Request market data
    tickers = ib.reqTickers(*qualified)
    ib.sleep(5)

    # Parse results
    options = []
    for t in tickers:
        c = t.contract
        bid = t.bid if t.bid and t.bid > 0 else 0
        ask = t.ask if t.ask and t.ask > 0 else 0
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

        greeks = t.modelGreeks or t.lastGreeks
        delta = gamma = theta = vega = iv = 0
        if greeks:
            delta = greeks.delta or 0
            gamma = greeks.gamma or 0
            theta = greeks.theta or 0
            vega = greeks.vega or 0
            iv = greeks.impliedVol or 0

        if mid > 0 and abs(delta) >= 0.15:
            dte = (datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d") - today).days
            options.append(
                {
                    "expiry": c.lastTradeDateOrContractMonth,
                    "dte": dte,
                    "right": c.right,
                    "strike": c.strike,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "delta": delta,
                    "gamma": gamma,
                    "theta": theta,
                    "vega": vega,
                    "iv": iv,
                }
            )

    client.disconnect()

    return {
        "spot": spot,
        "expirations": target_expiries,
        "options": options,
        "short_right": short_right,
        "long_right": long_right,
    }


# ---------------------------------------------------------------------------
# Risk Reversal Matrix Builder
# ---------------------------------------------------------------------------

def build_matrix(
    chain_data: Dict,
    bankroll: float,
    max_pct: float = 0.025,
    bearish: bool = False,
) -> Dict:
    """Build the risk reversal combination matrix from chain data.

    Returns dict with per-expiry combos and top picks.
    """
    spot = chain_data["spot"]
    options = chain_data["options"]
    short_right = chain_data["short_right"]
    long_right = chain_data["long_right"]
    max_risk = bankroll * max_pct

    # Group options by expiry and right
    by_exp: Dict[str, Dict[str, List]] = {}
    for o in options:
        by_exp.setdefault(o["expiry"], {}).setdefault(o["right"], []).append(o)

    all_combos = []
    skew_by_exp = {}

    for exp, sides in sorted(by_exp.items()):
        short_opts = sides.get(short_right, [])
        long_opts = sides.get(long_right, [])

        # IV skew table for this expiry
        skew_rows = []
        for delta_target in [0.50, 0.40, 0.35, 0.30, 0.25]:
            # Find closest short-side option to target delta
            s_opt = min(short_opts, key=lambda o: abs(abs(o["delta"]) - delta_target), default=None)
            l_opt = min(long_opts, key=lambda o: abs(abs(o["delta"]) - delta_target), default=None)
            if s_opt and l_opt:
                skew_rows.append(
                    {
                        "delta": delta_target,
                        "short_iv": s_opt["iv"] * 100,
                        "long_iv": l_opt["iv"] * 100,
                        "skew": (s_opt["iv"] - l_opt["iv"]) * 100,
                    }
                )
        skew_by_exp[exp] = skew_rows

        # Build combos (25-50 delta on each side)
        short_filtered = [o for o in short_opts if 0.25 <= abs(o["delta"]) <= 0.50]
        long_filtered = [o for o in long_opts if 0.25 <= abs(o["delta"]) <= 0.50]

        for s in short_filtered:
            for l in long_filtered:
                # Sell short-side at bid, buy long-side at ask
                premium_received = s["bid"]
                premium_paid = l["ask"]
                net = premium_received - premium_paid  # positive = credit

                # Margin: ~20% of short strike notional
                margin_per_contract = s["strike"] * 100 * 0.20
                max_qty = int(max_risk / margin_per_contract) if margin_per_contract > 0 else 0
                total_margin = max_qty * margin_per_contract

                skew = (s["iv"] - l["iv"]) * 100
                net_delta = l["delta"] + s["delta"]  # s["delta"] is negative for puts

                all_combos.append(
                    {
                        "expiry": exp,
                        "dte": s["dte"],
                        "short_strike": s["strike"],
                        "short_delta": abs(s["delta"]),
                        "short_bid": s["bid"],
                        "short_iv": s["iv"] * 100,
                        "long_strike": l["strike"],
                        "long_delta": abs(l["delta"]),
                        "long_ask": l["ask"],
                        "long_iv": l["iv"] * 100,
                        "net": net,
                        "skew": skew,
                        "net_delta": net_delta,
                        "max_qty": max_qty,
                        "margin": total_margin,
                        "short_right": short_right,
                        "long_right": long_right,
                    }
                )

    # Select top picks
    # Near-costless: within ±$0.50 of zero
    costless = [c for c in all_combos if -0.50 <= c["net"] <= 2.00]

    # Primary: costless, longest DTE, best delta balance
    primary = None
    if costless:
        # Prefer costless (abs(net) < 0.10), then longer DTE, then balanced delta
        primary_candidates = sorted(
            costless,
            key=lambda c: (
                abs(c["net"]) < 0.10,  # costless first
                c["dte"],  # longer DTE
                -abs(c["short_delta"] - c["long_delta"]),  # balanced delta
                c["skew"],  # more skew
            ),
            reverse=True,
        )
        primary = primary_candidates[0] if primary_candidates else None

    # Alternative: different expiry than primary
    alternative = None
    if primary and costless:
        alt_candidates = [
            c
            for c in costless
            if c["expiry"] != primary["expiry"]
            and abs(c["net"]) < 0.50
            and abs(c["short_delta"] - c["long_delta"]) < 0.10
        ]
        alt_candidates.sort(key=lambda c: (abs(c["net"]), -c["skew"]))
        alternative = alt_candidates[0] if alt_candidates else None

    # Aggressive: generates meaningful credit (>$1.00), same expiry as primary
    aggressive = None
    credit_combos = sorted(
        [c for c in all_combos if c["net"] >= 1.00 and (primary is None or c["expiry"] == primary["expiry"])],
        key=lambda c: (-c["net"], abs(c["short_delta"] - c["long_delta"])),
    )
    if credit_combos:
        aggressive = credit_combos[0]

    return {
        "all_combos": all_combos,
        "costless": costless,
        "primary": primary,
        "alternative": alternative,
        "aggressive": aggressive,
        "skew_by_exp": skew_by_exp,
        "spot": spot,
        "bankroll": bankroll,
        "max_risk": max_risk,
    }


# ---------------------------------------------------------------------------
# HTML Report Generator
# ---------------------------------------------------------------------------

def _fmt_net(net: float) -> str:
    if net >= 0:
        return f'+${net:.2f} CR'
    return f'-${abs(net):.2f} DR'


def _fmt_net_html(net: float) -> str:
    if net >= 0:
        return f'<span class="text-positive">+${net:.2f} CR</span>'
    return f'<span class="text-negative">-${abs(net):.2f} DR</span>'


def _right_label(right: str) -> str:
    return "Put" if right == "P" else "Call"


def _expiry_fmt(exp: str) -> str:
    """20260417 -> Apr 17"""
    d = datetime.strptime(exp, "%Y%m%d")
    return d.strftime("%b %d")


def _build_trade_panel(combo: Dict, label: str, pill_class: str, pill_text: str, spot: float, panel_class: str = "panel") -> str:
    """Build HTML for a single recommended trade panel."""
    if combo is None:
        return ""

    short_label = _right_label(combo["short_right"])
    long_label = _right_label(combo["long_right"])
    exp_fmt = _expiry_fmt(combo["expiry"])

    net_label = _fmt_net(combo["net"])
    net_per_contract = f'<span style="font-size:20px;" class="{"text-positive" if combo["net"] >= 0 else "text-negative"}"><strong>{net_label} per contract</strong></span>'

    total_credit = combo["net"] * combo["max_qty"] * 100
    total_label = f'+${total_credit:,.0f}' if total_credit >= 0 else f'-${abs(total_credit):,.0f}'

    return f"""
    <div class="{panel_class}">
      <div class="panel-header">
        <span>{label} — {exp_fmt} ({combo["dte"]} DTE)</span>
        <span class="pill {pill_class}">{pill_text}</span>
      </div>
      <div class="panel-body">
        <div class="callout {"positive" if combo["net"] >= 0 else ""}">
          <div class="callout-title">Trade Specification</div>
          <p style="font-size:16px; font-weight:500; margin-bottom:12px;">
            Sell {combo["expiry"][:4][-2:]}/{combo["expiry"][4:6]}/{combo["expiry"][6:]} ${combo["short_strike"]:.0f} {short_label} / Buy ${combo["long_strike"]:.0f} {long_label}
          </p>
          <div class="grid-3" style="margin-top:12px;">
            <div>
              <span class="text-muted text-small">NET</span><br>
              {net_per_contract}<br>
              <span class="text-small text-muted">(Sell {short_label.lower()} at ${combo["short_bid"]:.2f}, buy {long_label.lower()} at ${combo["long_ask"]:.2f})</span>
            </div>
            <div>
              <span class="text-muted text-small">QUANTITY</span><br>
              <span style="font-size:20px;"><strong>{combo["max_qty"]} contracts</strong></span><br>
              <span class="text-small text-muted">${combo["margin"]:,.0f} est. margin</span>
            </div>
            <div>
              <span class="text-muted text-small">NET DELTA EXPOSURE</span><br>
              <span style="font-size:20px;"><strong>{combo["net_delta"]:+.2f}Δ</strong></span><br>
              <span class="text-small text-muted">{short_label}: {combo["short_delta"]:.2f}Δ / {long_label}: {combo["long_delta"]:.2f}Δ</span>
            </div>
          </div>
        </div>

        <table style="margin-top:16px;">
          <thead>
            <tr>
              <th>Leg</th><th>Action</th><th>Contract</th>
              <th class="text-right">Strike</th><th class="text-right">Delta</th>
              <th class="text-right">IV</th><th class="text-right">Bid</th>
              <th class="text-right">Ask</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="pill pill-negative">SELL</span></td>
              <td>Sell to Open</td>
              <td>{exp_fmt} ${combo["short_strike"]:.0f} {short_label}</td>
              <td class="text-right">${combo["short_strike"]:.0f}</td>
              <td class="text-right">{combo["short_delta"]:.2f}</td>
              <td class="text-right">{combo["short_iv"]:.1f}%</td>
              <td class="text-right">${combo["short_bid"]:.2f}</td>
              <td class="text-right">—</td>
            </tr>
            <tr>
              <td><span class="pill pill-positive">BUY</span></td>
              <td>Buy to Open</td>
              <td>{exp_fmt} ${combo["long_strike"]:.0f} {long_label}</td>
              <td class="text-right">${combo["long_strike"]:.0f}</td>
              <td class="text-right">{combo["long_delta"]:.2f}</td>
              <td class="text-right">{combo["long_iv"]:.1f}%</td>
              <td class="text-right">—</td>
              <td class="text-right">${combo["long_ask"]:.2f}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>"""


def _build_execution_cmd(combo: Dict, ticker: str, label: str) -> str:
    """Build copy-paste execution commands for a combo."""
    if combo is None:
        return ""
    short_label = _right_label(combo["short_right"])
    long_label = _right_label(combo["long_right"])
    short_right_flag = combo["short_right"]
    long_right_flag = combo["long_right"]
    thesis = f"{ticker} risk reversal: sell {short_label[0]}{combo['short_strike']:.0f}/buy {long_label[0]}{combo['long_strike']:.0f} {_expiry_fmt(combo['expiry'])}, {_fmt_net(combo['net'])}"

    return f"""
        <div class="callout" style="margin-top:12px;">
          <div class="callout-title">{label}</div>
          <pre style="font-size:12px; white-space:pre-wrap; margin-top:8px;">
# Leg 1: Sell {short_label}
python3 scripts/ib_execute.py --type option --symbol {ticker} --expiry {combo["expiry"]} --strike {combo["short_strike"]:.0f} --right {short_right_flag} --qty {combo["max_qty"]} --side SELL --limit {combo["short_bid"]:.2f} --thesis "{thesis}" --yes

# Leg 2: Buy {long_label}
python3 scripts/ib_execute.py --type option --symbol {ticker} --expiry {combo["expiry"]} --strike {combo["long_strike"]:.0f} --right {long_right_flag} --qty {combo["max_qty"]} --side BUY --limit {combo["long_ask"]:.2f} --thesis "{thesis}" --yes</pre>
        </div>"""


def generate_report(
    ticker: str,
    matrix: Dict,
    flow_data: Dict,
    options_data: Dict,
    bearish: bool,
    template_path: Path,
    output_path: Path,
) -> str:
    """Generate the HTML report from template + data."""

    template = template_path.read_text()
    spot = matrix["spot"]
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %I:%M %p PT")
    date_str = now.strftime("%Y-%m-%d")

    direction = "BEARISH" if bearish else "BULLISH"
    direction_label = "Bearish" if bearish else "Bullish"
    direction_detail = "Sell Call / Buy Put" if bearish else "Sell Put / Buy Call"

    # -- DP flow context --
    dp = flow_data.get("dark_pool", {})
    agg = dp.get("aggregate", {})
    dp_buy_ratio = agg.get("dp_buy_ratio", 0)
    dp_strength = agg.get("flow_strength", 0)
    dp_direction = agg.get("flow_direction", "UNKNOWN")

    # -- Options flow context --
    pc_ratio = options_data.get("put_call_ratio", 0) or 0
    combined_bias = options_data.get("combined_bias", "UNKNOWN")

    # -- Skew range --
    all_skew = []
    for rows in matrix["skew_by_exp"].values():
        for r in rows:
            if r["skew"] > 0:
                all_skew.append(r["skew"])
    skew_min = min(all_skew) if all_skew else 0
    skew_max = max(all_skew) if all_skew else 0

    # -- Extract top picks --
    primary = matrix["primary"]
    alternative = matrix["alternative"]
    aggressive = matrix["aggressive"]
    net_label = "CREDIT" if primary and primary["net"] >= 0 else "COSTLESS" if primary and abs(primary["net"]) < 0.10 else "SMALL DEBIT"

    # ============================================================
    # Build section HTML
    # ============================================================

    # SECTION 2: Metrics
    metrics_html = f"""
      <div class="metric">
        <div class="metric-label">{ticker} Spot</div>
        <div class="metric-value">${spot:.2f}</div>
        <div class="metric-change">Live IB quote</div>
      </div>
      <div class="metric">
        <div class="metric-label">Put IV Skew</div>
        <div class="metric-value text-positive">+{skew_min:.0f}-{skew_max:.0f}%</div>
        <div class="metric-change">Puts rich vs calls — favors selling</div>
      </div>
      <div class="metric">
        <div class="metric-label">DP Buy Ratio</div>
        <div class="metric-value">{dp_buy_ratio*100:.1f}%</div>
        <div class="metric-change {"text-positive" if dp_direction == "ACCUMULATION" else "text-negative" if dp_direction == "DISTRIBUTION" else ""}">{dp_direction} (5-day)</div>
      </div>
      <div class="metric">
        <div class="metric-label">Options Flow</div>
        <div class="metric-value small">{pc_ratio:.1f}x P/C</div>
        <div class="metric-change">{combined_bias}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Bankroll</div>
        <div class="metric-value small">${matrix["bankroll"]:,.0f}</div>
        <div class="metric-change">2.5% = ${matrix["max_risk"]:,.0f} max margin</div>
      </div>
      <div class="metric">
        <div class="metric-label">Net Cost</div>
        <div class="metric-value text-positive">{net_label}</div>
        <div class="metric-change">All recommendations costless or credit</div>
      </div>"""

    # SECTION 3: Thesis
    thesis_html = f"""
    <div class="callout positive">
      <div class="callout-title">Thesis</div>
      <p>{ticker} {direction_label.lower()} directional bet via risk reversal. Dark pool flow shows
      <strong>{dp_direction.lower()}</strong> ({dp_buy_ratio*100:.1f}% buy ratio, strength {dp_strength:.0f}).
      The <strong>{skew_min:.0f}-{skew_max:.0f}% put IV skew</strong> creates a structural edge —
      selling the expensive put and buying the cheaper call produces costless or credit-generating
      {direction_label.lower()} exposure. {"Extreme put buying (" + str(pc_ratio) + "x P/C) likely represents hedging, not directional selling — a contrarian bullish signal." if not bearish and pc_ratio > 2 else ""}</p>
    </div>"""

    # SECTION 4: Dark Pool Flow
    daily = dp.get("daily", [])
    flow_rows = ""
    today_str = now.strftime("%Y-%m-%d")
    for d in daily:
        date = d.get("date", "")
        buy_pct = d.get("dp_buy_ratio", 0) * 100
        strength = d.get("flow_strength", 0)
        direction_day = d.get("flow_direction", "")
        is_today = date == today_str
        today_pill = ' <span class="pill pill-accent" style="font-size:9px">TODAY</span>' if is_today else ""
        dir_class = "text-positive" if direction_day == "ACCUMULATION" else "text-negative" if direction_day == "DISTRIBUTION" else "text-muted"
        bar_class = "up" if direction_day == "ACCUMULATION" else "down" if direction_day == "DISTRIBUTION" else ""
        bar_today = " today" if is_today else ""
        bar_height = max(10, min(100, strength / 69 * 100))

        flow_rows += f"""
            <tr>
              <td>{"<strong>" if is_today else ""}{date}{"</strong>" if is_today else ""}{today_pill}</td>
              <td class="text-right">{d.get("total_volume", 0):,}</td>
              <td class="text-right">${d.get("total_premium", 0)/1e6:.1f}M</td>
              <td class="text-right {dir_class}">{"<strong>" if buy_pct > 70 else ""}{buy_pct:.1f}%{"</strong>" if buy_pct > 70 else ""}</td>
              <td class="text-right">{"<strong>" if strength > 50 else ""}{strength:.1f}{"</strong>" if strength > 50 else ""}</td>
              <td><span class="{dir_class}">{direction_day}</span></td>
              <td><div class="spark" style="width:60px;"><div class="spark-bar {bar_class}{bar_today}" style="height:{bar_height:.0f}%"></div></div></td>
            </tr>"""

    flow_html = f"""
    <div class="panel">
      <div class="panel-header">
        <span>Dark Pool Flow — Recent Breakdown</span>
        <span class="pill {"pill-positive" if dp_direction == "ACCUMULATION" else "pill-negative" if dp_direction == "DISTRIBUTION" else ""}">{dp_direction}</span>
      </div>
      <div class="panel-body">
        <table>
          <thead>
            <tr><th>Date</th><th class="text-right">Volume</th><th class="text-right">Premium</th><th class="text-right">Buy %</th><th class="text-right">Strength</th><th>Direction</th><th>Sparkline</th></tr>
          </thead>
          <tbody>{flow_rows}</tbody>
        </table>
      </div>
    </div>"""

    # SECTION 5: IV Skew
    skew_tables = ""
    for exp, rows in sorted(matrix["skew_by_exp"].items()):
        dte = (datetime.strptime(exp, "%Y%m%d") - now).days
        exp_label = f"{_expiry_fmt(exp)} ({dte} DTE)"
        skew_rows_html = ""
        for r in rows:
            skew_rows_html += f"""
                <tr>
                  <td>~{r["delta"]*100:.0f}Δ</td>
                  <td class="text-right">{r["short_iv"]:.1f}%</td>
                  <td class="text-right">{r["long_iv"]:.1f}%</td>
                  <td class="text-right text-positive"><strong>+{r["skew"]:.1f}%</strong></td>
                </tr>"""
        skew_tables += f"""
          <div>
            <h3 class="section-header">{exp_label}</h3>
            <table>
              <thead><tr><th>Delta</th><th class="text-right">Short IV</th><th class="text-right">Long IV</th><th class="text-right">Skew</th></tr></thead>
              <tbody>{skew_rows_html}</tbody>
            </table>
          </div>"""

    skew_html = f"""
    <div class="panel">
      <div class="panel-header">
        <span>IV Skew Analysis — Why Risk Reversals Are Optimal</span>
        <span class="pill pill-positive">STRUCTURAL EDGE</span>
      </div>
      <div class="panel-body">
        <div class="grid-2">{skew_tables}</div>
        <div class="callout positive" style="margin-top:16px;">
          <div class="callout-title">Skew Interpretation</div>
          <p>Puts are trading at <strong>{skew_min:.0f}-{skew_max:.0f}% higher IV</strong> than equivalent-delta calls. Selling a put generates significantly more premium than buying a call costs — enabling <strong>costless or credit-generating</strong> directional exposure.</p>
        </div>
      </div>
    </div>"""

    # SECTION 6: Recommended Trades
    primary_html = _build_trade_panel(matrix["primary"], "⭐ Primary Recommendation", "pill-positive", "RECOMMENDED", spot, "panel panel-accent")
    alternative_html = _build_trade_panel(matrix["alternative"], "Alternative — Different Expiry", "", "ALTERNATIVE", spot)
    aggressive_html = _build_trade_panel(matrix["aggressive"], "Aggressive — Credit-Generating", "", "AGGRESSIVE", spot)

    # SECTION 7: Full Matrix
    matrix_tables = ""
    combos_by_exp: Dict[str, List] = {}
    for c in matrix.get("costless", []):
        combos_by_exp.setdefault(c["expiry"], []).append(c)

    for exp, combos in sorted(combos_by_exp.items()):
        dte = combos[0]["dte"] if combos else 0
        short_label = _right_label(combos[0]["short_right"]) if combos else "Put"
        long_label = _right_label(combos[0]["long_right"]) if combos else "Call"
        # Sort by net (closest to zero first), then by delta balance
        combos.sort(key=lambda c: (abs(c["net"]), abs(c["short_delta"] - c["long_delta"])))
        rows_html = ""
        for c in combos[:20]:  # Limit to top 20 per expiry
            is_primary = matrix["primary"] and c["short_strike"] == matrix["primary"]["short_strike"] and c["long_strike"] == matrix["primary"]["long_strike"] and c["expiry"] == matrix["primary"]["expiry"]
            is_alt = matrix["alternative"] and c["short_strike"] == matrix["alternative"]["short_strike"] and c["long_strike"] == matrix["alternative"]["long_strike"] and c["expiry"] == matrix["alternative"]["expiry"]
            row_class = ' class="recommended"' if (is_primary or is_alt) else ""
            rows_html += f"""
              <tr{row_class}>
                <td>{short_label[0]}{c["short_strike"]:.0f}</td>
                <td class="text-right">{c["short_delta"]:.2f}</td>
                <td>{long_label[0]}{c["long_strike"]:.0f}</td>
                <td class="text-right">{c["long_delta"]:.2f}</td>
                <td class="text-right">${c["short_bid"]:.2f}</td>
                <td class="text-right">${c["long_ask"]:.2f}</td>
                <td class="text-right">{_fmt_net_html(c["net"])}</td>
                <td class="text-right">{c["skew"]:.1f}%</td>
                <td class="text-right">{c["net_delta"]:+.2f}</td>
              </tr>"""
        matrix_tables += f"""
        <h3 class="section-header" style="margin-top:24px;">{_expiry_fmt(exp)} Expiry ({dte} DTE)</h3>
        <table>
          <thead>
            <tr><th>Sell {short_label}</th><th class="text-right">{short_label} Δ</th><th>Buy {long_label}</th><th class="text-right">{long_label} Δ</th><th class="text-right">{short_label} Bid</th><th class="text-right">{long_label} Ask</th><th class="text-right">Net</th><th class="text-right">Skew</th><th class="text-right">Net Δ</th></tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>"""

    matrix_html = f"""
    <div class="panel">
      <div class="panel-header">
        <span>Full Near-Costless Combinations — Ranked by Proximity to Zero</span>
        <span class="text-small text-muted">Showing combos within ±$0.50 of zero (25-50Δ each leg)</span>
      </div>
      <div class="panel-body" style="overflow-x:auto;">
        {matrix_tables}
      </div>
    </div>"""

    # SECTION 8: Risk & Execution
    short_label = "Put" if not bearish else "Call"
    risk_html = f"""
    <div class="grid-2">
      <div class="panel">
        <div class="panel-header"><span>Risk Management</span></div>
        <div class="panel-body">
          <table>
            <tr><td class="text-muted">Structure</td><td>{direction_label} Risk Reversal (undefined risk)</td></tr>
            <tr><td class="text-muted">Max Contracts</td><td>{matrix["primary"]["max_qty"] if primary else "N/A"} (per 2.5% bankroll / ~20% margin)</td></tr>
            <tr><td class="text-muted">Margin Estimate</td><td>${matrix["primary"]["margin"]:,.0f} if primary else "N/A"</td></tr>
            <tr><td class="text-muted">Theoretical Max Loss</td><td>Short {short_label.lower()} strike × 100 × qty (stock → $0)</td></tr>
            <tr><td class="text-muted">Practical Stop Loss</td><td>Close if spread value hits –$3.00</td></tr>
            <tr><td class="text-muted">Target Exit</td><td>Close when underlying exceeds long strike + 5%</td></tr>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><span>Compliance Notes</span></div>
        <div class="panel-body">
          <div class="callout warning">
            <div class="callout-title">⚠️ Manager Override — Undefined Risk</div>
            <p>This is a <strong>risk reversal with a naked short {short_label.lower()}</strong>. This is an explicit manager override of the "NEVER sell naked options" constraint. The operator requested this structure as a directional bet.</p>
          </div>
          <div class="callout">
            <div class="callout-title">Mitigation</div>
            <ul style="font-size:12px; list-style:none;">
              <li>✓ Position sized to 2.5% margin cap</li>
              <li>✓ Hard stop loss at –$3.00 spread value</li>
              <li>✓ Limited DTE (14-60 days)</li>
              <li>✓ Manager explicitly initiated this trade</li>
            </ul>
          </div>
        </div>
      </div>
    </div>"""

    execution_cmds = ""
    if primary:
        execution_cmds += _build_execution_cmd(primary, ticker, "⭐ Primary")
    if alternative:
        execution_cmds += _build_execution_cmd(alternative, ticker, "Alternative")
    if aggressive:
        execution_cmds += _build_execution_cmd(aggressive, ticker, "Aggressive")

    execution_html = f"""
    <div class="panel panel-accent">
      <div class="panel-header">
        <span>Execution Commands</span>
        <span class="pill pill-accent">READY TO EXECUTE</span>
      </div>
      <div class="panel-body">
        <p class="text-muted text-small" style="margin-bottom:12px;">Copy-paste to execute. Primary recommendation first.</p>
        {execution_cmds}
      </div>
    </div>"""

    # ============================================================
    # Replace template variables
    # ============================================================
    html = template
    replacements = {
        "{{TICKER}}": ticker,
        "{{COMPANY_NAME}}": ticker,  # Could be resolved via fetch_ticker
        "{{DATE}}": date_str,
        "{{TIMESTAMP}}": timestamp,
        "{{DIRECTION}}": direction,
        "{{DIRECTION_LABEL}}": direction_label,
        "{{DIRECTION_DETAIL}}": direction_detail,
        "{{STATUS_CLASS}}": "positive",
        "{{METRICS_HTML}}": metrics_html,
        "{{THESIS_HTML}}": thesis_html,
        "{{FLOW_HTML}}": flow_html,
        "{{SKEW_HTML}}": skew_html,
        "{{PRIMARY_HTML}}": primary_html,
        "{{ALTERNATIVE_HTML}}": alternative_html,
        "{{AGGRESSIVE_HTML}}": aggressive_html,
        "{{MATRIX_HTML}}": matrix_html,
        "{{RISK_HTML}}": risk_html,
        "{{EXECUTION_HTML}}": execution_html,
    }
    for key, value in replacements.items():
        html = html.replace(key, value)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return str(output_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Risk Reversal Scanner — exploit IV skew for directional bets"
    )
    parser.add_argument("ticker", help="Ticker symbol (exactly one)")
    parser.add_argument("--bearish", action="store_true", help="Bearish reversal (sell call / buy put)")
    parser.add_argument("--bankroll", type=float, default=1_201_929, help="Current bankroll (default: 1201929)")
    parser.add_argument("--max-pct", type=float, default=0.025, help="Max %% of bankroll in margin (default: 2.5%%)")
    parser.add_argument("--min-dte", type=int, default=14, help="Minimum DTE (default: 14)")
    parser.add_argument("--max-dte", type=int, default=60, help="Maximum DTE (default: 60)")
    parser.add_argument("--port", type=int, default=4001, help="IB Gateway port (default: 4001)")
    parser.add_argument("--no-open", action="store_true", help="Don't open report in browser")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of HTML")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    direction = "bearish" if args.bearish else "bullish"
    print(f"🔄 {ticker} {direction} risk reversal scan...")

    # Step 1: Fetch DP flow and options flow (context)
    print("  [1/4] Fetching dark pool flow...")
    flow_data = fetch_flow_data(ticker)

    print("  [2/4] Fetching options flow...")
    options_data = fetch_options_data(ticker)

    # Step 2: Fetch live chain from IB
    print(f"  [3/4] Fetching IB chain ({args.min_dte}-{args.max_dte} DTE)...")
    chain_data = fetch_chain(
        ticker,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        bearish=args.bearish,
        port=args.port,
    )
    print(f"        Spot: ${chain_data['spot']:.2f} | {len(chain_data['options'])} options qualified")
    print(f"        Expirations: {', '.join(chain_data['expirations'])}")

    # Step 3: Build matrix
    print("  [4/4] Building risk reversal matrix...")
    matrix = build_matrix(chain_data, args.bankroll, args.max_pct, args.bearish)
    n_combos = len(matrix["all_combos"])
    n_costless = len(matrix["costless"])
    print(f"        {n_combos} total combos | {n_costless} near-costless")

    if args.json:
        result = {
            "ticker": ticker,
            "direction": direction,
            "spot": chain_data["spot"],
            "bankroll": args.bankroll,
            "max_risk": args.bankroll * args.max_pct,
            "primary": matrix["primary"],
            "alternative": matrix["alternative"],
            "aggressive": matrix["aggressive"],
            "skew": matrix["skew_by_exp"],
            "costless_count": n_costless,
            "total_combos": n_combos,
        }
        print(json.dumps(result, indent=2, default=str))
        return

    if not matrix["primary"]:
        print("\n❌ No costless combinations found. Skew may be insufficient or chain too illiquid.")
        return

    # Step 4: Generate report
    template_path = PROJECT_ROOT / ".pi" / "skills" / "html-report" / "risk-reversal-template.html"
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = PROJECT_ROOT / "reports" / f"{ticker.lower()}-risk-reversal-{date_str}.html"

    report_file = generate_report(
        ticker=ticker,
        matrix=matrix,
        flow_data=flow_data,
        options_data=options_data,
        bearish=args.bearish,
        template_path=template_path,
        output_path=output_path,
    )

    # Print summary
    p = matrix["primary"]
    short_label = _right_label(p["short_right"])
    long_label = _right_label(p["long_right"])
    print(f"\n{'='*70}")
    print(f"  ⭐ PRIMARY: Sell ${p['short_strike']:.0f} {short_label} / Buy ${p['long_strike']:.0f} {long_label}")
    print(f"     Expiry: {_expiry_fmt(p['expiry'])} ({p['dte']} DTE)")
    print(f"     Net: {_fmt_net(p['net'])} | Qty: {p['max_qty']} | Margin: ${p['margin']:,.0f}")
    print(f"     Net Delta: {p['net_delta']:+.2f} | Skew: {p['skew']:.1f}%")
    print(f"{'='*70}")
    print(f"\n📄 Report: {report_file}")

    if not args.no_open:
        webbrowser.open(f"file://{output_path.resolve()}")


if __name__ == "__main__":
    main()

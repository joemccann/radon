#!/usr/bin/env python3
"""
generate_gex_share — Generate GEX share cards + preview page for X.

Reads from data/gex.json, produces 4 PNG cards and a self-contained HTML preview.

Cards:
  1. GEX Regime     — spot, net GEX, bias, IV 30D, vol P/C
  2. Key Levels     — GEX flip, put wall, magnets, accelerator (UW)
  3. Source Compare — UW vs MenthorQ level deltas (or UW-only if MQ unavailable)
  4. Profile        — top-strikes bar chart ±8% of spot

Usage:
    python3 scripts/generate_gex_share.py
    python3 scripts/generate_gex_share.py --json --no-open
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
CACHE_PATH = DATA_DIR / "gex.json"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

FONTS = (
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800'
    "&family=IBM+Plex+Mono:wght@400;500;600&display=swap\" rel=\"stylesheet\">"
)

BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', sans-serif; background: #0a0f14; color: #e2e8f0; width: 600px; }
.card { width: 600px; background: #0a0f14; border: 1px solid #1e293b; overflow: hidden; }
.card-inner { padding: 28px 32px; }
.footer { display: flex; justify-content: space-between; align-items: center;
          padding-top: 16px; border-top: 1px solid #1e293b; margin-top: 20px; }
.footer-brand { font-size: 12px; font-weight: 600; color: #05AD98;
                font-family: 'IBM Plex Mono', monospace; }
.footer-tag { font-family: 'IBM Plex Mono', monospace; font-size: 9px; color: #475569;
              letter-spacing: 0.08em; text-transform: uppercase; }
.footer-date { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #475569; }
"""


# ── Data loading ──────────────────────────────────────────────────

def load_gex() -> dict:
    """Load GEX data from cache."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    print("No GEX data found. Run: curl -X POST 'http://localhost:8321/gex/scan?ticker=SPY'",
          file=sys.stderr)
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────

def fmt_price(v) -> str:
    if v is None:
        return "---"
    return f"${v:,.2f}"


def fmt_gex(v) -> str:
    if v is None:
        return "---"
    sign = "+" if v >= 0 else ""
    abs_v = abs(v)
    if abs_v >= 1_000_000:
        return f"{sign}${abs_v / 1_000_000:.1f}M"
    if abs_v >= 1_000:
        return f"{sign}${abs_v / 1_000:.1f}K"
    return f"{sign}${v:.0f}"


def fmt_pct(v, decimals: int = 2) -> str:
    if v is None:
        return "---"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def bias_color(direction: str) -> str:
    return {
        "BULL":          "#05AD98",
        "CAUTIOUS_BULL": "#05AD98",
        "BEAR":          "#E85D6C",
        "CAUTIOUS_BEAR": "#E85D6C",
    }.get(direction, "#94a3b8")


def regime_label(direction: str) -> str:
    return direction.replace("_", " ")


def gex_color(net_gex) -> str:
    return "#05AD98" if (net_gex or 0) >= 0 else "#E85D6C"


# ── Card wrapper ─────────────────────────────────────────────────

def card_wrap(title: str, body: str, card_n: int, total: int, ds: str) -> str:
    try:
        d = datetime.strptime(ds, "%Y-%m-%d")
        date_str = d.strftime("%b %-d, %Y")
    except ValueError:
        date_str = ds
    footer = f"""
    <div class="footer">
      <div class="footer-brand">radon.run</div>
      <div class="footer-tag">Analyzed by Radon · {card_n}/{total}</div>
      <div class="footer-date">{date_str}</div>
    </div>"""
    return (
        f'<!DOCTYPE html><html lang="en"><head>'
        f'<meta charset="UTF-8"><meta name="viewport" content="width=600">'
        f"<title>{title}</title>{FONTS}"
        f"<style>{BASE_CSS}</style></head>"
        f"<body><div class=\"card\"><div class=\"card-inner\">"
        f"\n{body}\n{footer}"
        f"\n</div></div></body></html>"
    )


# ── Card 1: GEX Regime ────────────────────────────────────────────

def card1_regime(data: dict, ds: str) -> str:
    ticker   = data.get("ticker", "SPY")
    spot     = data.get("spot", 0)
    net_gex  = data.get("net_gex", 0)
    net_dex  = data.get("net_dex", 0)
    vol_pc   = data.get("vol_pc")
    bias     = data.get("bias", {})
    direction = bias.get("direction", "NEUTRAL")
    col       = bias_color(direction)
    gex_col   = gex_color(net_gex)
    iv_obj    = data.get("iv") or {}
    iv30d     = iv_obj.get("iv30d") or data.get("atm_iv")
    iv_rank   = iv_obj.get("iv_rank")
    hv30      = iv_obj.get("hv30")

    reasons_html = ""
    for r in (bias.get("reasons") or [])[:3]:
        reasons_html += (
            f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:10px;'
            f'color:#64748b;margin-bottom:4px">· {r}</div>'
        )

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
         letter-spacing:.15em;text-transform:uppercase;color:#475569;margin-bottom:12px;
         display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#05AD98"></span>
      GAMMA EXPOSURE · {ticker} · {ds}
    </div>
    <div style="display:flex;align-items:baseline;gap:16px;margin-bottom:20px">
      <div style="font-size:52px;font-weight:800;letter-spacing:-.04em;color:#e2e8f0;line-height:1">
        {fmt_price(spot)}
      </div>
      <div>
        <div style="display:inline-block;background:rgba(94,94,94,.12);color:{col};
             border:1px solid {col}40;border-radius:999px;padding:3px 12px;
             font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;
             letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px">
          {regime_label(direction)}
        </div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#475569">SPOT</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px">
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
             letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:6px">Net GEX</div>
        <div style="font-size:20px;font-weight:700;color:{gex_col}">{fmt_gex(net_gex)}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-top:4px">
          {"NEGATIVE GAMMA" if (net_gex or 0) < 0 else "POSITIVE GAMMA"}
        </div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
             letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:6px">Net DEX</div>
        <div style="font-size:20px;font-weight:700;color:{'#05AD98' if (net_dex or 0) >= 0 else '#E85D6C'}">{fmt_gex(net_dex)}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-top:4px">delta exposure</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
             letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:6px">Vol P/C</div>
        <div style="font-size:20px;font-weight:700;color:{'#E85D6C' if (vol_pc or 0) > 1.2 else '#e2e8f0'}">
          {f"{vol_pc:.2f}" if vol_pc else "---"}
        </div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-top:4px">
          {"BEARISH" if (vol_pc or 0) > 1.5 else "LEAN BEAR" if (vol_pc or 0) > 1.2 else "neutral"}
        </div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
             letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:6px">IV 30D</div>
        <div style="font-size:20px;font-weight:700;color:#e2e8f0">
          {f"{iv30d:.1f}%" if iv30d else "---"}
        </div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-top:4px">
          {f"rank {iv_rank:.0f}%" if iv_rank else ""}{f"  HV {hv30:.1f}%" if hv30 else ""}
        </div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
             letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:6px">Bias Reasons</div>
        {reasons_html if reasons_html else '<div style="font-size:10px;color:#475569">NEUTRAL</div>'}
      </div>
    </div>"""
    return card_wrap("GEX Regime", body, 1, 4, ds)


# ── Card 2: Key Levels (UW) ───────────────────────────────────────

def card2_levels(data: dict, ds: str) -> str:
    ticker  = data.get("ticker", "SPY")
    spot    = data.get("spot", 0)
    levels  = data.get("levels", {})

    def _s(key: str):
        lvl = levels.get(key)
        return lvl.get("strike") if lvl else None

    def _pct(key: str):
        lvl = levels.get(key)
        return lvl.get("distance_pct") if lvl else None

    rows = [
        ("GEX FLIP",      _s("gex_flip"),       _pct("gex_flip"),       "#F5A623"),
        ("MAX MAGNET",    _s("max_magnet"),      _pct("max_magnet"),     "#05AD98"),
        ("2ND MAGNET",    _s("second_magnet"),   _pct("second_magnet"),  "#05AD98"),
        ("PUT WALL",      _s("put_wall"),        _pct("put_wall"),       "#E85D6C"),
        ("MAX ACCEL",     _s("max_accelerator"), _pct("max_accelerator"), "#E85D6C"),
        ("CALL WALL",     _s("call_wall"),       _pct("call_wall"),      "#F5A623"),
    ]

    rows_html = ""
    for label, strike, pct, col in rows:
        if strike is None:
            continue
        dist = f"{'+' if (pct or 0) >= 0 else ''}{pct:.1f}%" if pct is not None else ""
        bar_w = min(100, abs(pct or 0) * 8)
        bar_col = "#05AD98" if (pct or 0) >= 0 else "#E85D6C"
        rows_html += f"""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
               letter-spacing:.08em;color:#475569;min-width:90px;text-transform:uppercase">{label}</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;
               color:{col};min-width:68px">{fmt_price(strike)}</div>
          <div style="flex:1;height:4px;background:#1e293b;border-radius:2px;overflow:hidden">
            <div style="height:4px;width:{bar_w:.0f}%;background:{bar_col};border-radius:2px"></div>
          </div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#475569;
               min-width:52px;text-align:right">{dist}</div>
        </div>"""

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
         letter-spacing:.15em;text-transform:uppercase;color:#475569;margin-bottom:4px;
         display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#05AD98"></span>
      KEY LEVELS · {ticker} · SPOT {fmt_price(spot)}
    </div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#334155;
         margin-bottom:16px">Source: Unusual Whales · CBOE data</div>
    {rows_html}"""
    return card_wrap("Key Levels", body, 2, 4, ds)


# ── Card 3: Source Comparison (UW vs MenthorQ) ────────────────────

def card3_source(data: dict, ds: str) -> str:
    ticker = data.get("ticker", "SPY")
    mq     = data.get("mq") or {}
    delta  = data.get("source_delta") or {}
    levels = data.get("levels", {})

    def _s(key: str):
        lvl = levels.get(key)
        return lvl.get("strike") if lvl else None

    uw_flip = _s("gex_flip")
    uw_put  = _s("put_wall")
    uw_call = _s("call_wall")
    mq_hvl  = mq.get("hvl")
    mq_put  = mq.get("put_support_all")
    mq_call = mq.get("call_resistance_all")
    mq_date = mq.get("source_date", "")
    mq_iv30 = mq.get("iv30d")
    mq_hv30 = mq.get("hv30")
    iv_obj  = data.get("iv") or {}
    uw_iv   = iv_obj.get("iv30d") or data.get("atm_iv")

    def delta_col(d) -> str:
        if d is None:
            return "#94a3b8"
        if abs(d) <= 2:
            return "#05AD98"
        if abs(d) <= 10:
            return "#F5A623"
        return "#E85D6C"

    def row(label: str, uw_val, mq_val, delta_key: str) -> str:
        d_entry = delta.get(delta_key) or {}
        d = d_entry.get("delta")
        d_str = f"{'+' if (d or 0) > 0 else ''}{d:.1f}" if d is not None else "—"
        return f"""
        <tr>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#64748b;
               padding:5px 8px 5px 0;vertical-align:middle">{label}</td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
               color:#05AD98;text-align:right;padding:5px 8px;vertical-align:middle">
            {fmt_price(uw_val) if uw_val else "—"}
          </td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
               color:#85b7eb;text-align:right;padding:5px 8px;vertical-align:middle">
            {fmt_price(mq_val) if mq_val else "—"}
          </td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
               color:{delta_col(d)};text-align:right;padding:5px 0 5px 8px;vertical-align:middle">
            {d_str}
          </td>
        </tr>"""

    if not mq:
        body = f"""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
             letter-spacing:.15em;text-transform:uppercase;color:#475569;margin-bottom:12px;
             display:flex;align-items:center;gap:8px">
          <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#05AD98"></span>
          SOURCE COMPARISON · {ticker}
        </div>
        <div style="font-size:13px;color:#475569;font-family:'IBM Plex Mono',monospace;margin-top:20px">
          MenthorQ data unavailable for this scan.
        </div>"""
    else:
        iv_row = ""
        if uw_iv or mq_iv30:
            uw_iv_s = f"{uw_iv:.1f}%" if uw_iv else "—"
            mq_iv_s = f"{mq_iv30 * 100:.1f}%" if mq_iv30 else "—"
            iv_delta = round(uw_iv - mq_iv30 * 100, 2) if (uw_iv and mq_iv30) else None
            d_s = f"{'+' if (iv_delta or 0) > 0 else ''}{iv_delta:.2f}" if iv_delta is not None else "—"
            iv_row = f"""
            <tr>
              <td style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#64748b;
                   padding:5px 8px 5px 0;vertical-align:middle">IV 30D</td>
              <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
                   color:#05AD98;text-align:right;padding:5px 8px;vertical-align:middle">{uw_iv_s}</td>
              <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
                   color:#85b7eb;text-align:right;padding:5px 8px;vertical-align:middle">{mq_iv_s}</td>
              <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
                   color:{delta_col(iv_delta)};text-align:right;padding:5px 0 5px 8px;vertical-align:middle">{d_s}</td>
            </tr>"""

        body = f"""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
             letter-spacing:.15em;text-transform:uppercase;color:#475569;margin-bottom:12px;
             display:flex;align-items:center;gap:8px">
          <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#05AD98"></span>
          SOURCE COMPARISON · {ticker}
        </div>
        <div style="display:flex;gap:16px;margin-bottom:12px">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;padding:2px 8px;
               border-radius:2px;background:rgba(15,110,86,.18);color:#5dcaa5;
               border:0.5px solid rgba(15,110,86,.4)">UW · Apr {mq_date[-2:] if mq_date else '—'}</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;padding:2px 8px;
               border-radius:2px;background:rgba(56,138,221,.15);color:#85b7eb;
               border:0.5px solid rgba(56,138,221,.35)">MQ · {mq_date or '—'}</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#334155;margin-left:auto">
            Δ = UW − MQ
          </div>
        </div>
        <table style="width:100%;border-collapse:collapse">
          <thead>
            <tr>
              <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
                   letter-spacing:.08em;text-transform:uppercase;color:#334155;
                   text-align:left;padding-bottom:8px;border-bottom:1px solid #1e293b">Level</th>
              <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
                   letter-spacing:.08em;text-transform:uppercase;color:#5dcaa5;
                   text-align:right;padding-bottom:8px;border-bottom:1px solid #1e293b">UW</th>
              <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
                   letter-spacing:.08em;text-transform:uppercase;color:#85b7eb;
                   text-align:right;padding-bottom:8px;border-bottom:1px solid #1e293b">MQ</th>
              <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
                   letter-spacing:.08em;text-transform:uppercase;color:#475569;
                   text-align:right;padding-bottom:8px;border-bottom:1px solid #1e293b">Δ</th>
            </tr>
          </thead>
          <tbody>
            {row("Flip / HVL",          uw_flip, mq_hvl,  "flip_vs_hvl")}
            {row("Put wall / support",   uw_put,  mq_put,  "put_wall_vs_support_all")}
            {row("Call wall / resist.",  uw_call, mq_call, "call_wall_vs_resistance_all")}
            {iv_row}
          </tbody>
        </table>"""

    return card_wrap("Source Comparison", body, 3, 4, ds)


# ── Card 4: GEX Profile (bar chart) ──────────────────────────────

def card4_profile(data: dict, ds: str) -> str:
    ticker  = data.get("ticker", "SPY")
    spot    = data.get("spot", 0)
    profile = data.get("profile", [])

    if not profile:
        body = f"""
        <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
             letter-spacing:.15em;text-transform:uppercase;color:#475569;margin-bottom:20px;
             display:flex;align-items:center;gap:8px">
          <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#05AD98"></span>
          GEX PROFILE · {ticker}
        </div>
        <div style="font-size:12px;color:#475569">No profile data.</div>"""
        return card_wrap("GEX Profile", body, 4, 4, ds)

    max_abs = max(abs(b.get("net_gex", 0)) for b in profile) or 1
    max_bar_px = 180

    bars_html = ""
    for bucket in profile:
        strike  = bucket.get("strike", 0)
        net_gex = bucket.get("net_gex", 0)
        tag     = bucket.get("tag")
        is_spot = tag == "SPOT"
        pct     = bucket.get("pct_from_spot", 0)

        bar_w = max(1, int(abs(net_gex) / max_abs * max_bar_px))
        bar_col = "#05AD98" if net_gex >= 0 else "#a32d2d"
        strike_col = "#05AD98" if is_spot else "#e2e8f0"
        fw = "700" if is_spot else "400"

        tag_html = ""
        if tag and tag != "SPOT":
            tag_col = {
                "GEX FLIP":      "#F5A623",
                "MAX MAGNET":    "#05AD98",
                "2ND MAGNET":    "#05AD98",
                "PUT WALL":      "#E85D6C",
                "MAX ACCELERATOR": "#E85D6C",
                "CALL WALL":     "#F5A623",
            }.get(tag, "#475569")
            tag_html = (
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:8px;'
                f'font-weight:600;color:{tag_col};letter-spacing:.06em;margin-left:4px">'
                f'{tag}</span>'
            )

        bars_html += f"""
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;height:18px">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;
               color:{strike_col};font-weight:{fw};min-width:56px;text-align:right">
            {strike:,.0f}
          </div>
          <div style="flex:0 0 {max_bar_px * 2}px;position:relative;height:10px">
            <div style="position:absolute;left:50%;width:1px;height:10px;background:#1e293b"></div>
            {'<div style="position:absolute;right:50%;height:10px;width:' + str(bar_w) + 'px;background:' + bar_col + ';border-radius:1px"></div>' if net_gex < 0 else ''}
            {'<div style="position:absolute;left:50%;height:10px;width:' + str(bar_w) + 'px;background:' + bar_col + ';border-radius:1px"></div>' if net_gex >= 0 else ''}
          </div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;
               color:{'#475569' if not is_spot else '#05AD98'};min-width:40px">
            {f"{pct:+.1f}%"}
          </div>
          {tag_html}
        </div>"""

    er = data.get("expected_range", {})
    er_low  = er.get("low")
    er_high = er.get("high")
    er_line = ""
    if er_low and er_high:
        er_line = (
            f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:9px;color:#334155;'
            f'margin-top:12px">1-day range: {fmt_price(er_low)} — {fmt_price(er_high)}</div>'
        )

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
         letter-spacing:.15em;text-transform:uppercase;color:#475569;margin-bottom:4px;
         display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#05AD98"></span>
      GEX PROFILE · {ticker} · SPOT {fmt_price(spot)}
    </div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#334155;margin-bottom:12px">
      ◀ negative (destabilizing) &nbsp;|&nbsp; positive (stabilizing) ▶
    </div>
    {bars_html}
    {er_line}"""
    return card_wrap("GEX Profile", body, 4, 4, ds)


# ── Tweet text ────────────────────────────────────────────────────

def build_tweet(data: dict, ds: str) -> str:
    ticker  = data.get("ticker", "SPY")
    spot    = data.get("spot", 0)
    net_gex = data.get("net_gex", 0)
    levels  = data.get("levels", {})
    bias    = data.get("bias", {})
    iv_obj  = data.get("iv") or {}
    iv30d   = iv_obj.get("iv30d") or data.get("atm_iv")
    vol_pc  = data.get("vol_pc")

    flip_lvl = levels.get("gex_flip") or {}
    put_lvl  = levels.get("put_wall") or {}
    direction = bias.get("direction", "NEUTRAL")

    gex_label = "NEG GAMMA" if (net_gex or 0) < 0 else "POS GAMMA"
    lines = [
        f"${ticker} GEX Analysis · {ds}",
        "",
        f"> Spot: {fmt_price(spot)}",
        f"> Net GEX: {fmt_gex(net_gex)}  [{gex_label}]",
    ]
    if flip_lvl.get("strike"):
        dist = flip_lvl.get("distance_pct", 0)
        lines.append(f"> GEX Flip: {fmt_price(flip_lvl['strike'])}  ({'+' if dist >= 0 else ''}{dist:.1f}%)")
    if put_lvl.get("strike"):
        dist2 = put_lvl.get("distance_pct", 0)
        lines.append(f"> Put Wall: {fmt_price(put_lvl['strike'])}  ({'+' if dist2 >= 0 else ''}{dist2:.1f}%)")
    if iv30d:
        lines.append(f"> IV 30D: {iv30d:.1f}%")
    if vol_pc:
        lines.append(f"> Vol P/C: {vol_pc:.2f}")
    lines += [
        f"> Bias: {regime_label(direction)}",
        "",
        "Analyzed by Radon · radon.run",
    ]
    return "\n".join(lines)


# ── Preview HTML ──────────────────────────────────────────────────

def build_preview(cards_b64: list, tweet_text: str, ds: str) -> str:
    card_labels = [
        "GEX Regime",
        "Key Levels",
        "Source Comparison",
        "GEX Profile",
    ]
    imgs_html = ""
    for i, (b64, label) in enumerate(zip(cards_b64, card_labels), 1):
        card_id = f"gex-card-img-{i}"
        img_tag = (
            f'<img id="{card_id}" src="{b64}" style="width:100%;border-radius:3px;display:block" '
            f'alt="GEX card {i}">' if b64 else
            f'<div style="width:100%;height:200px;background:#0f1519;border:1px solid #1e293b;'
            f'border-radius:3px;display:flex;align-items:center;justify-content:center;'
            f'font-family:\'IBM Plex Mono\',monospace;font-size:10px;color:#475569">'
            f'Screenshot failed</div>'
        )
        copy_btn = (
            f'<button onclick="copyImg(\'{card_id}\',this)" style="padding:4px 10px;'
            f'background:#1e293b;color:#05AD98;border:none;border-radius:2px;'
            f'font-family:\'IBM Plex Mono\',monospace;font-size:9px;font-weight:600;'
            f'letter-spacing:.06em;cursor:pointer;margin-top:6px">COPY IMAGE</button>'
        ) if b64 else ""
        imgs_html += f"""
    <div style="margin-bottom:20px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;
           letter-spacing:.1em;text-transform:uppercase;color:#334155;margin-bottom:8px;
           display:flex;justify-content:space-between">
        <span>{i}/4 — {label}</span>
      </div>
      {img_tag}
      {copy_btn}
    </div>"""

    tweet_escaped = tweet_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GEX Report — X Share · {ds}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#07090d;color:#e2e8f0;font-family:'Inter',sans-serif;min-height:100vh;padding:32px 24px}}
.layout{{max-width:1100px;margin:0 auto;display:grid;grid-template-columns:380px 1fr;gap:32px;align-items:start}}
.intro{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#475569;padding:0 0 20px;line-height:1.6;grid-column:1/-1;border-bottom:1px solid #1e293b;margin-bottom:8px}}
.intro strong{{color:#e2e8f0}}
.panel{{background:#0f1519;border:1px solid #1e293b;border-radius:4px;padding:20px;position:sticky;top:24px}}
.panel-hdr{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #1e293b}}
.tweet-body{{font-size:13px;line-height:1.65;color:#e2e8f0;white-space:pre-wrap;margin-bottom:14px;word-break:break-word}}
.copy-btn{{width:100%;padding:10px;background:#05AD98;color:#000;border:none;border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;transition:opacity 150ms}}
.copy-btn:hover{{opacity:.85}}.copy-btn.copied{{background:#1e293b;color:#05AD98}}
.char{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:#475569;margin-top:8px;text-align:right}}
.cards-hdr{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #1e293b}}
</style>
</head><body>
<div class="layout">
  <div class="intro"><strong>GEX Report — X Share</strong><br>
  Tweet text + 4 infographic cards · {ds} · Analyzed by Radon</div>
  <div class="panel">
    <div class="panel-hdr">Tweet Copy</div>
    <div class="tweet-body" id="tweet-text">{tweet_escaped}</div>
    <button class="copy-btn" id="copy-btn" onclick="copyTweet()">Copy Tweet Text</button>
    <div class="char">{len(tweet_text)} chars</div>
  </div>
  <div>
    <div class="cards-hdr">4 Infographic Cards — attach to tweet</div>
    {imgs_html}
  </div>
</div>
<script>
function copyTweet(){{
  const t=document.getElementById('tweet-text').innerText;
  navigator.clipboard.writeText(t).then(()=>{{
    const b=document.getElementById('copy-btn');
    b.textContent='Copied!';b.classList.add('copied');
    setTimeout(()=>{{b.textContent='Copy Tweet Text';b.classList.remove('copied')}},2000);
  }});
}}
function copyImg(id,btn){{
  const img=document.getElementById(id);
  const c=document.createElement('canvas');
  c.width=img.naturalWidth;c.height=img.naturalHeight;
  c.getContext('2d').drawImage(img,0,0);
  c.toBlob(b=>{{
    navigator.clipboard.write([new ClipboardItem({{'image/png':b}})]).then(()=>{{
      const orig=btn.textContent;btn.textContent='Copied!';
      setTimeout(()=>{{btn.textContent=orig}},2000);
    }});
  }});
}}
</script>
</body></html>"""


# ── Screenshot ────────────────────────────────────────────────────

def screenshot_card(html_path: str, png_path: str) -> bool:
    try:
        r1 = subprocess.run(
            ["agent-browser", "open", f"file://{html_path}"],
            capture_output=True, text=True, timeout=15,
        )
        if r1.returncode != 0:
            return False
        r2 = subprocess.run(
            ["agent-browser", "screenshot", ".card", png_path],
            capture_output=True, text=True, timeout=15,
        )
        return r2.returncode == 0 and Path(png_path).exists()
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate GEX X share report")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    data = load_gex()
    ds = date.today().strftime("%Y-%m-%d")

    generators = [card1_regime, card2_levels, card3_source, card4_profile]
    card_html_paths = []
    png_paths = []

    for i, gen in enumerate(generators, 1):
        html_content = gen(data, ds)
        html_path = str(REPORTS_DIR / f"tweet-gex-{ds}-card-{i}.html")
        with open(html_path, "w") as f:
            f.write(html_content)
        card_html_paths.append(html_path)

        png_path = str(REPORTS_DIR / f"tweet-gex-{ds}-card-{i}.png")
        ok = screenshot_card(html_path, png_path)
        if not ok:
            print(f"  Warning: screenshot failed for card {i}", file=sys.stderr)
        png_paths.append(png_path if ok else "")

    cards_b64 = []
    for p in png_paths:
        if p and Path(p).exists():
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            cards_b64.append(f"data:image/png;base64,{b64}")
        else:
            cards_b64.append("")

    tweet_text  = build_tweet(data, ds)
    preview_html = build_preview(cards_b64, tweet_text, ds)
    preview_path = str(REPORTS_DIR / f"tweet-gex-{ds}.html")
    with open(preview_path, "w") as f:
        f.write(preview_html)

    if not args.no_open:
        subprocess.Popen(["open", preview_path])

    result = {
        "preview_path": preview_path,
        "card_paths": card_html_paths,
        "png_paths": [p for p in png_paths if p],
        "date": ds,
        "tweet_length": len(tweet_text),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"GEX share report generated: {preview_path}")
        print(f"  Cards: {len(card_html_paths)} HTML, {len([p for p in png_paths if p])} PNG")
        print(f"  Tweet: {len(tweet_text)} chars")

    return result


if __name__ == "__main__":
    main()

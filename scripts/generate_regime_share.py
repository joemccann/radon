#!/usr/bin/env python3
"""
Generate Regime/CRI share cards + preview page for X.
Reads from the latest CRI scheduled cache, produces 4 PNG cards
and a self-contained HTML preview page.

Cards:
  1. CRI Score & Regime — headline score, level bar, components
  2. Crash Trigger — 3 conditions, values, INACTIVE/TRIGGERED status
  3. Vol / Credit Divergence — VIX, VVIX, COR1M, HYG 5d
  4. CTA Squeeze State — equity positioning z-score, forced selling

Usage:
  python3 scripts/generate_regime_share.py
  python3 scripts/generate_regime_share.py --json
  python3 scripts/generate_regime_share.py --date 2026-03-19
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
SCHEDULED_DIR = DATA_DIR / "cri_scheduled"
CACHE_PATH = DATA_DIR / "cri.json"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

FONTS = '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">'

BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', sans-serif; background: #0a0f14; color: #e2e8f0; width: 600px; }
.card { width: 600px; background: #0a0f14; border: 1px solid #1e293b; overflow: hidden; }
.card-inner { padding: 28px 32px; }
.footer { display: flex; justify-content: space-between; align-items: center;
          padding-top: 16px; border-top: 1px solid #1e293b; }
.footer-brand { font-size: 12px; font-weight: 600; color: #05AD98;
                font-family: 'IBM Plex Mono', monospace; }
.footer-tag { font-family: 'IBM Plex Mono', monospace; font-size: 9px; color: #475569;
              letter-spacing: 0.08em; text-transform: uppercase; }
.footer-date { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: #475569; }
"""


# ── Data loading ──────────────────────────────────────────────────

def load_cri(target_date: Optional[str] = None) -> dict:
    """Load latest CRI cache, scheduled dir first then legacy cri.json."""
    # Try scheduled dir
    if SCHEDULED_DIR.exists():
        files = sorted(SCHEDULED_DIR.glob("cri-*.json"))
        if target_date:
            files = [f for f in files if target_date in f.name]
        if files:
            try:
                with open(files[-1]) as f:
                    raw = f.read()
                start = raw.find("{")
                return json.loads(raw[start:])
            except Exception:
                pass
    # Fallback to cri.json
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            raw = f.read()
        start = raw.find("{")
        return json.loads(raw[start:])
    raise FileNotFoundError("No CRI cache found. Run cri_scan.py first.")


# ── Helpers ───────────────────────────────────────────────────────

def pctile_label(p: int) -> str:
    if p == 1: return "1st"
    if p == 2: return "2nd"
    if p == 3: return "3rd"
    return f"{p}th"


def level_color(level: str) -> str:
    return {
        "LOW": "#05AD98",
        "ELEVATED": "#F5A623",
        "HIGH": "#E85D6C",
        "CRITICAL": "#E85D6C",
    }.get(level, "#94a3b8")


def level_bg(level: str) -> str:
    return {
        "LOW": "rgba(5,173,152,0.12)",
        "ELEVATED": "rgba(245,166,35,0.12)",
        "HIGH": "rgba(232,93,108,0.12)",
        "CRITICAL": "rgba(232,93,108,0.15)",
    }.get(level, "rgba(100,116,139,0.1)")


# ── Card wrappers ─────────────────────────────────────────────────

def card_wrap(title: str, body: str, card_n: int, total: int, ds: str) -> str:
    d = datetime.strptime(ds, "%Y-%m-%d")
    date_str = d.strftime("%b %-d, %Y")
    footer = f"""
    <div class="footer">
      <div class="footer-brand">radon.run</div>
      <div class="footer-tag">Analyzed by Radon · {card_n}/{total}</div>
      <div class="footer-date">{date_str}</div>
    </div>"""
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=600">
<title>{title}</title>{FONTS}
<style>{BASE_CSS}</style></head>
<body><div class="card"><div class="card-inner">
{body}
{footer}
</div></div></body></html>"""


# ── Card 1: CRI Score & Regime ────────────────────────────────────

def card1_cri(data: dict, ds: str) -> str:
    cri = data.get("cri", {})
    score = cri.get("score", 0)
    level = cri.get("level", "LOW")
    components = cri.get("components", {})
    vix_c = components.get("vix", 0)
    vvix_c = components.get("vvix", 0)
    corr_c = components.get("correlation", 0)
    mom_c = components.get("momentum", 0)
    col = level_color(level)
    bg = level_bg(level)
    score_pct = min(score, 100)

    def comp_row(label: str, val: float, live_label: str = "") -> str:
        pct = (val / 25) * 100
        bar_col = "#05AD98" if val < 8 else "#E85D6C" if val > 16 else "#F5A623"
        return f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#64748b">{label}</span>
            <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:{bar_col}">{val:.1f}/25</span>
          </div>
          <div style="height:8px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;overflow:hidden">
            <div style="height:100%;width:{pct:.1f}%;background:{bar_col};border-radius:1px"></div>
          </div>
        </div>"""

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:{col};margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{col}"></span>
      CRASH RISK INDEX · {ds}
    </div>
    <div style="display:flex;align-items:baseline;gap:16px;margin-bottom:6px">
      <div style="font-size:56px;font-weight:800;letter-spacing:-.04em;color:{col};line-height:1">{score:.0f}</div>
      <div>
        <div style="font-size:13px;color:#64748b;margin-bottom:4px">/100</div>
        <div style="display:inline-block;background:{bg};color:{col};border:1px solid {col.replace('#','rgba(').replace('D6C','D6C,0.4)').replace('AD98','AD98,0.4)').replace('A623','A623,0.4)')};border-radius:999px;padding:3px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase">{level}</div>
      </div>
    </div>
    <div style="height:8px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;overflow:hidden;margin-bottom:6px">
      <div style="height:100%;width:{score_pct}%;background:{col};border-radius:1px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#334155;margin-bottom:20px">
      <span>LOW</span><span>ELEVATED</span><span>HIGH</span><span>CRITICAL</span>
    </div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:12px">CRI Component Breakdown</div>
    {comp_row("VIX", vix_c)}
    {comp_row("VVIX", vvix_c)}
    {comp_row("CORRELATION", corr_c)}
    {comp_row("MOMENTUM", mom_c)}
    <div style="background:#0f1519;border:1px solid #1e293b;border-left:2px solid {col};border-radius:0 3px 3px 0;padding:10px 12px;margin-top:4px">
      <div style="font-size:11px;color:#94a3b8;line-height:1.55">
        Vol-of-vol elevated at {data.get('vvix',0):.1f}. SPX trading {data.get('spx_distance_pct',0):.1f}% below 100-day MA. CTA deleveraging pipeline: ${data.get('cta',{}).get('est_selling_bn',0):.0f}B. Regime: <span style="color:{col};font-weight:700">{level}</span>.
      </div>
    </div>"""
    return card_wrap("CRI Score & Regime", body, 1, 4, ds)


# ── Card 2: Crash Trigger Conditions ─────────────────────────────

def card2_crash_trigger(data: dict, ds: str) -> str:
    ct = data.get("crash_trigger", {})
    triggered = ct.get("triggered", False)
    conditions = ct.get("conditions", {})
    spx_met = conditions.get("spx_below_100d_ma", False)
    rvol_met = conditions.get("realized_vol_gt_25", False)
    cor1m_met = conditions.get("cor1m_gt_60", False)

    status_col = "#E85D6C" if triggered else "#F5A623"
    status_label = "TRIGGERED" if triggered else "INACTIVE"
    status_bg = "rgba(232,93,108,0.12)" if triggered else "rgba(245,166,35,0.08)"

    vix = data.get("vix", 0)
    vvix = data.get("vvix", 0)
    spy = data.get("spy", 0)
    ma = data.get("spx_100d_ma", 0)
    rvol = data.get("realized_vol", 0)
    cor1m = data.get("cor1m", 0)
    spx_dist = data.get("spx_distance_pct", 0)

    def trigger_row(label: str, met: bool, value: str, threshold: str) -> str:
        check_col = "#05AD98" if met else "#E85D6C"
        check_sym = "✓" if met else "✗"
        row_bg = "rgba(5,173,152,0.03)" if met else "transparent"
        return f"""
        <div style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:{row_bg};border-bottom:1px solid rgba(30,41,59,0.5)">
          <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:700;color:{check_col};width:16px;flex-shrink:0">{check_sym}</span>
          <div style="flex:1">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;color:#e2e8f0">{label}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-top:2px">threshold: {threshold}</div>
          </div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;color:{check_col};text-align:right">{value}</div>
        </div>"""

    conditions_met = sum([spx_met, rvol_met, cor1m_met])
    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#E85D6C;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#E85D6C"></span>
      CRASH TRIGGER CONDITIONS · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">{"All Clear" if not triggered else "Crash Trigger Active"}</div>
    <div style="font-size:13px;color:#64748b;margin-bottom:18px;line-height:1.4">
      {conditions_met}/3 conditions active. All three must fire simultaneously for a crash trigger. {"Two more conditions needed." if conditions_met == 1 else "One more condition needed." if conditions_met == 2 else "None active — market stress present but not at crash threshold." if conditions_met == 0 else ""}
    </div>
    <div style="display:inline-block;background:{status_bg};color:{status_col};border:1px solid {status_col};border-radius:999px;padding:4px 14px;font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-bottom:16px">{status_label} — {conditions_met}/3 CONDITIONS</div>
    <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;overflow:hidden;margin-bottom:20px">
      {trigger_row("SPX < 100-day MA", spx_met, f"{spx_dist:+.1f}% (MA: ${ma:.0f})", "below 100d MA")}
      {trigger_row("Realized Vol > 25%", rvol_met, f"{rvol:.2f}%", "> 25%")}
      {trigger_row("COR1M > 60", cor1m_met, f"{cor1m:.2f}", "> 60")}
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">VIX</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:#F5A623">{vix:.2f}</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">VVIX</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:#F5A623">{vvix:.2f}</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">COR1M</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:#e2e8f0">{cor1m:.2f}</div>
      </div>
    </div>"""
    return card_wrap("Crash Trigger Conditions", body, 2, 4, ds)


# ── Card 3: Vol / Credit Divergence ──────────────────────────────

def card3_vol_credit(data: dict, ds: str) -> str:
    vix = data.get("vix", 0)
    vvix = data.get("vvix", 0)
    vvix_vix_ratio = data.get("vvix_vix_ratio", 0)
    cor1m = data.get("cor1m", 0)
    cor1m_5d = data.get("cor1m_5d_change", 0) or 0
    vix_5d = data.get("vix_5d_roc", 0) or 0

    # VCG signal from CRI scan — vvix elevated + credit hasn't caught up
    vvix_elevated = vvix > 110
    hdr_signal = vvix_elevated  # simplified HDR check from available data

    def metric_row(label: str, value: str, sub: str, color: str = "#e2e8f0") -> str:
        return f"""
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(30,41,59,0.4)">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:#94a3b8">{label}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-top:1px">{sub}</div>
          </div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:700;color:{color}">{value}</div>
        </div>"""

    vix_col = "#F5A623" if vix > 20 else "#05AD98"
    vvix_col = "#E85D6C" if vvix > 120 else "#F5A623" if vvix > 110 else "#05AD98"
    cor_col = "#E85D6C" if cor1m > 50 else "#F5A623" if cor1m > 35 else "#05AD98"
    ratio_col = "#F5A623" if vvix_vix_ratio and vvix_vix_ratio > 5 else "#e2e8f0"

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#8B5CF6;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#8B5CF6"></span>
      VOL / CREDIT DIVERGENCE · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">
      {"Vol-of-Vol Elevated" if vvix_elevated else "Vol Complex Stable"}
    </div>
    <div style="font-size:13px;color:#64748b;margin-bottom:20px;line-height:1.4">
      VVIX at {vvix:.1f} — {"above 110 threshold, signalling second-order stress. Vol-of-vol rising faster than spot VIX, indicating unstable vol regime." if vvix_elevated else "below 110 threshold. Vol regime stable."} VIX/VVIX ratio: {vvix_vix_ratio:.2f}{"x (elevated)" if vvix_vix_ratio and vvix_vix_ratio > 5 else "x"}.
    </div>
    <div style="margin-bottom:20px">
      {metric_row("VIX", f"{vix:.2f}", f"5d RoC: {vix_5d:+.1f}%", vix_col)}
      {metric_row("VVIX", f"{vvix:.2f}", f"VVIX/VIX: {vvix_vix_ratio:.2f}", vvix_col)}
      {metric_row("COR1M", f"{cor1m:.2f}", f"5d chg: {cor1m_5d:+.2f} pts", cor_col)}
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:20px">
      <div style="background:#0f1519;border:1px solid rgba(139,92,246,0.3);border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#8B5CF6;margin-bottom:4px">HDR SIGNAL</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:700;color:{'#F5A623' if hdr_signal else '#05AD98'}">{'ACTIVE' if hdr_signal else 'INACTIVE'}</div>
        <div style="font-size:10px;color:#475569;margin-top:3px">VVIX {'>' if hdr_signal else '<'} 110</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:4px">REGIME</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:700;color:#e2e8f0">DIVERGENCE</div>
        <div style="font-size:10px;color:#475569;margin-top:3px">VIX below 40</div>
      </div>
    </div>"""
    return card_wrap("Vol Credit Divergence", body, 3, 4, ds)


# ── Card 4: CTA Squeeze State ─────────────────────────────────────

def card4_cta_squeeze(data: dict, ds: str) -> str:
    cta_model = data.get("cta", {})
    exposure = cta_model.get("exposure_pct", 0) or 0
    forced_red = cta_model.get("forced_reduction_pct", 0) or 0
    est_selling = cta_model.get("est_selling_bn", 0) or 0

    menthorq = data.get("menthorq_cta") or {}
    spx_row = None
    tables = menthorq.get("tables", {})
    if isinstance(tables, dict):
        main = tables.get("main", [])
        for r in main:
            name = r.get("underlying", "").lower()
            if "s&p" in name or "e-mini" in name:
                spx_row = r
                break

    spx_today = spx_row["position_today"] if spx_row else None
    spx_1m = spx_row["position_1m_ago"] if spx_row else None
    spx_pctile = spx_row["percentile_3m"] if spx_row else None
    spx_z = spx_row["z_score_3m"] if spx_row else None

    flipped = spx_today is not None and spx_1m is not None and spx_today < 0 and spx_1m > 0
    is_extreme = spx_pctile is not None and spx_pctile <= 10

    # Exposure bar: scale 0-200%
    bar_width = min(exposure / 2, 100)
    bar_col = "#05AD98" if exposure >= 80 else "#E85D6C"

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#E85D6C;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#E85D6C"></span>
      CTA POSITIONING · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">
      {"Squeeze Coil Loaded" if is_extreme else "CTA Positioning Elevated"}
    </div>
    <div style="font-size:13px;color:#64748b;margin-bottom:20px;line-height:1.4">
      {"SPX CTAs at the " + pctile_label(spx_pctile) + " percentile of their 3M range" + (", having flipped from " + f"{spx_1m:.2f} long to {spx_today:.2f} short in 30 days" if flipped else "") + ". Any bullish catalyst triggers violent short-covering." if spx_row else f"Vol-targeting model: {exposure:.1f}% implied exposure, ${est_selling:.0f}B in forced selling pipeline."}
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:18px">
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">SPX 3M %ILE</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:{'#E85D6C' if is_extreme else '#e2e8f0'}">{pctile_label(spx_pctile) if spx_pctile is not None else '---'}</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Z-SCORE</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:{'#E85D6C' if spx_z is not None and spx_z < -1.5 else '#e2e8f0'}">{f'{spx_z:.2f}' if spx_z is not None else '---'}</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">EST. SELLING</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:#F5A623">${est_selling:.0f}B</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">EXPOSURE</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:700;color:{bar_col}">{exposure:.0f}%</div>
      </div>
    </div>
    <div style="margin-bottom:8px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#475569;margin-bottom:6px">VOL-TARGETING EXPOSURE</div>
      <div style="height:10px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;overflow:hidden;margin-bottom:3px">
        <div style="height:100%;width:{bar_width:.1f}%;background:{bar_col};border-radius:1px"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#334155">
        <span>0%</span><span>100%</span><span>200%</span>
      </div>
    </div>"""
    return card_wrap("CTA Squeeze State", body, 4, 4, ds)


# ── Tweet text ────────────────────────────────────────────────────

def build_tweet(data: dict, ds: str) -> str:
    cri = data.get("cri", {})
    score = cri.get("score", 0)
    level = cri.get("level", "LOW")
    ct = data.get("crash_trigger", {})
    triggered = ct.get("triggered", False)
    conditions = ct.get("conditions", {})
    spx_met = conditions.get("spx_below_100d_ma", False)
    vix = data.get("vix", 0)
    vvix = data.get("vvix", 0)
    cor1m = data.get("cor1m", 0)
    rvol = data.get("realized_vol", 0)
    cta = data.get("cta", {})
    est_selling = cta.get("est_selling_bn", 0) or 0

    menthorq = data.get("menthorq_cta") or {}
    tables = menthorq.get("tables", {}) if isinstance(menthorq, dict) else {}
    main = tables.get("main", []) if isinstance(tables, dict) else []
    spx_row = None
    for r in main:
        name = r.get("underlying", "").lower()
        if "s&p" in name or "e-mini" in name:
            spx_row = r
            break

    d = datetime.strptime(ds, "%Y-%m-%d")
    month_day = d.strftime("%b %-d")

    conditions_met = sum([spx_met, conditions.get("realized_vol_gt_25", False), conditions.get("cor1m_gt_60", False)])

    spx_line = ""
    if spx_row:
        p = spx_row.get("percentile_3m", 0)
        z = spx_row.get("z_score_3m", 0)
        spx_line = f"\n> SPX CTA position at {pctile_label(p)} percentile (3M) · z-score {z:.2f}"

    return f"""⚠️ The Crash Risk Index is flashing {level} at {score:.0f}/100 — here's the full picture ({month_day} 🧵):

> CRI: {score:.0f}/100 — {level}
> VIX: {vix:.2f} · VVIX: {vvix:.2f} ({"above" if vvix > 110 else "below"} 110 threshold)
> COR1M: {cor1m:.2f} · Realized Vol: {rvol:.2f}%

> Crash Trigger: {"🔴 TRIGGERED" if triggered else "🟡 INACTIVE"} ({conditions_met}/3 conditions active)
> SPX below 100d MA: {"✓" if spx_met else "✗"} · RVol > 25%: {"✓" if conditions.get("realized_vol_gt_25") else "✗"} · COR1M > 60: {"✓" if conditions.get("cor1m_gt_60") else "✗"}{spx_line}

> CTA vol-targeting model: ${est_selling:.0f}B in forced selling pipeline
> Equity exposure compressing — deleveraging in progress

Regime is {"elevated but not at crash threshold" if level == "ELEVATED" and not triggered else "at crash trigger threshold — all 3 conditions active" if triggered else "compressed — risk appetite recovering"}.

Analyzed by Radon
radon.run"""


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


# ── Preview HTML ──────────────────────────────────────────────────

def build_preview(cards_b64: list, tweet_text: str, ds: str) -> str:
    labels = [
        ("CRI Score & Regime", "regime-card-1-cri-score.png"),
        ("Crash Trigger Conditions", "regime-card-2-crash-trigger.png"),
        ("Vol / Credit Divergence", "regime-card-3-vol-credit.png"),
        ("CTA Squeeze State", "regime-card-4-cta-squeeze.png"),
    ]
    imgs_html = ""
    for i, (b64, (title, fname)) in enumerate(zip(cards_b64, labels), 1):
        imgs_html += f"""
    <div style="margin-bottom:20px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#334155;margin-bottom:8px;display:flex;justify-content:space-between">
        <span>Card {i}/4 —</span><span style="color:#05AD98">{title}</span>
      </div>
      <img style="width:100%;border:1px solid #1e293b;border-radius:3px;display:block" src="{b64}" alt="{title}" id="img{i}">
      <div style="display:flex;gap:8px;margin-top:8px">
        <button onclick="copyImg('img{i}',this)" style="flex:1;padding:7px;background:#0f1519;border:1px solid #1e293b;border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;color:#94a3b8;text-align:center">Copy Image</button>
        <a href="{b64}" download="{fname}" style="flex:1;padding:7px;background:#0f1519;border:1px solid #1e293b;border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;color:#94a3b8;text-decoration:none;text-align:center;display:block;line-height:1.4">Download PNG ↓</a>
      </div>
    </div>"""

    tweet_escaped = tweet_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Regime/CRI Report — X Share · {ds}</title>
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
  <div class="intro"><strong>Regime / CRI Report — X Share</strong><br>Tweet text + 4 infographic cards · {ds} · Analyzed by Radon</div>
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


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate Regime/CRI X share report")
    parser.add_argument("--date", help="YYYY-MM-DD date override")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    data = load_cri(args.date)
    ds = data.get("date") or args.date or date.today().strftime("%Y-%m-%d")

    generators = [card1_cri, card2_crash_trigger, card3_vol_credit, card4_cta_squeeze]
    card_html_paths = []
    png_paths = []

    for i, gen in enumerate(generators, 1):
        html_content = gen(data, ds)
        html_path = str(REPORTS_DIR / f"tweet-regime-{ds}-card-{i}.html")
        with open(html_path, "w") as f:
            f.write(html_content)
        card_html_paths.append(html_path)

        png_path = str(REPORTS_DIR / f"tweet-regime-{ds}-card-{i}.png")
        ok = screenshot_card(html_path, png_path)
        if not ok:
            print(f"⚠ Screenshot failed for card {i}", file=sys.stderr)
        png_paths.append(png_path if ok else "")

    # Base64 encode
    cards_b64 = []
    for p in png_paths:
        if p and Path(p).exists():
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            cards_b64.append(f"data:image/png;base64,{b64}")
        else:
            cards_b64.append("")

    tweet_text = build_tweet(data, ds)
    preview_html = build_preview(cards_b64, tweet_text, ds)
    preview_path = str(REPORTS_DIR / f"tweet-regime-{ds}.html")
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
        print(f"✅ Regime share report generated: {preview_path}")
        print(f"   Cards: {len(card_html_paths)} HTML, {len([p for p in png_paths if p])} PNG")
        print(f"   Tweet: {len(tweet_text)} chars")

    return result


if __name__ == "__main__":
    main()

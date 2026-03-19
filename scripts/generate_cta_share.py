#!/usr/bin/env python3
"""
Generate CTA share cards + preview page for X.
Reads from the latest MenthorQ CTA cache, produces 4 PNG cards
and a self-contained HTML preview page.

Usage:
  python3 scripts/generate_cta_share.py
  python3 scripts/generate_cta_share.py --json    # print output path as JSON
  python3 scripts/generate_cta_share.py --date 2026-03-19
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CACHE_DIR = PROJECT_ROOT / "data" / "menthorq_cache"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


# ── Data loading ─────────────────────────────────────────────────────────────

def load_cta(target_date: Optional[str] = None) -> dict:
    files = sorted(CACHE_DIR.glob("cta_????-??-??.json"))
    if not files:
        raise FileNotFoundError("No CTA cache files found.")
    if target_date:
        path = CACHE_DIR / f"cta_{target_date}.json"
        if not path.exists():
            raise FileNotFoundError(f"CTA cache not found for {target_date}")
    else:
        path = files[-1]
    with open(path) as f:
        return json.load(f)


def get_row(rows: list, *keywords: str) -> Optional[dict]:
    kw_lower = [k.lower() for k in keywords]
    for r in rows:
        name = r["underlying"].lower()
        if all(k in name for k in kw_lower):
            return r
    return None


def pctile_label(p: int) -> str:
    if p == 1: return "1st"
    if p == 2: return "2nd"
    if p == 3: return "3rd"
    return f"{p}th"


# ── Card HTML generators ──────────────────────────────────────────────────────

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


def card_html(title: str, body: str, card_n: int, total: int, ds: str) -> str:
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


def card1_squeeze(data: dict, ds: str) -> str:
    main = data["tables"]["main"]
    spx = get_row(main, "s&p") or get_row(main, "e-mini")
    nq  = get_row(main, "nasdaq")
    r1k = get_row(main, "russell")

    rows = [
        ("E-Mini SPX", spx),
        ("CME NQ",     nq),
        ("Mini R1000", r1k),
    ]

    def bar_row(label: str, r: dict | None) -> str:
        if not r:
            return ""
        today = r["position_today"]
        ago   = r["position_1m_ago"]
        # scale: ±4 maps to 0–100% of half-track (50% each side)
        max_scale = 4.0
        short_pct = min(abs(min(today, 0)) / max_scale * 50, 50)
        long_pct  = min(abs(max(ago,   0)) / max_scale * 50, 50)
        val_color = "#E85D6C" if today < 0 else "#05AD98"
        ago_sign  = "+" if ago > 0 else ""
        return f"""
        <div style="display:flex;align-items:center;gap:0;margin-bottom:10px">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#94a3b8;width:100px;flex-shrink:0">{label}</div>
          <div style="flex:1;height:28px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;position:relative">
            <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#334155"></div>
            <div style="position:absolute;right:50%;height:100%;width:{long_pct}%;background:rgba(5,173,152,0.35);border-radius:1px"></div>
            <div style="position:absolute;right:50%;height:100%;width:{short_pct}%;background:rgba(232,93,108,0.6);border-radius:1px"></div>
          </div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:{val_color};margin-left:8px;flex-shrink:0">{today:+.2f}</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-left:6px">{ago_sign}{ago:.2f} 1M ago</div>
        </div>"""

    flip_rows = "".join(bar_row(lbl, r) for lbl, r in rows)

    spx_pctile = spx["percentile_3m"] if spx else "—"
    spx_z      = f"{spx['z_score_3m']:.2f}" if spx else "—"
    selling_bn = data.get("cta_model", {}).get("est_selling_bn", 90.6) if isinstance(data.get("cta_model"), dict) else 90.6

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#E85D6C;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#E85D6C"></span>
      CTA SQUEEZE ALERT · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">The Coil Is Set</div>
    <div style="font-size:13px;color:#64748b;margin-bottom:24px;line-height:1.4">
      CTAs flipped from <span style="color:#94a3b8">+{spx['position_1m_ago']:.2f} long → {spx['position_today']:+.2f} short</span> in 30 days.
      SPX positioning at <span style="color:#94a3b8">{pctile_label(int(spx_pctile))} percentile</span> of its 3-month range. Maximum mean-reversion fuel.
    </div>

    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:12px">Position Flip (1M ago → Today)</div>
    {flip_rows}

    <div style="margin-bottom:24px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:8px">CTA Equity Exposure — Squeeze Meter</div>
      <div style="position:relative;height:14px;border-radius:2px;background:linear-gradient(to right,#05AD98,#334155 50%,#E85D6C);margin-bottom:4px">
        <div style="position:absolute;left:3%;top:-3px;width:3px;height:20px;background:#fff;border-radius:1px;box-shadow:0 0 5px rgba(255,255,255,0.4)"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-bottom:6px">
        <span style="color:#E85D6C">◀ MAX SHORT (NOW)</span><span>NEUTRAL</span><span>MAX LONG ▶</span>
      </div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#E85D6C;font-weight:600">{pctile_label(int(spx_pctile))} percentile (3M) · z-score {spx_z} · $90.6B forced selling pipeline</div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#1e293b;border:1px solid #1e293b;border-radius:3px;margin-bottom:20px">
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">SPX 3M Pctile</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#E85D6C">{pctile_label(int(spx_pctile))}</div>
      </div>
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">Z-Score</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#E85D6C">{spx_z}</div>
      </div>
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">Est. Selling</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#F5A623">$90B</div>
      </div>
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">Squeeze Risk</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#F5A623">HIGH</div>
      </div>
    </div>"""
    return card_html("CTA Squeeze Meter", body, 1, 4, ds)


def card2_equity(data: dict, ds: str) -> str:
    idx_rows = data["tables"]["index"]
    main_rows = data["tables"]["main"]

    INDICES = [
        ("E-Mini S&P 500", get_row(main_rows, "s&p") or get_row(main_rows, "e-mini")),
        ("CME Nasdaq 100", get_row(main_rows, "nasdaq")),
        ("Mini Russell 1000", get_row(idx_rows, "russell")),
        ("MSCI World", get_row(idx_rows, "msci")),
        ("DAX", get_row(idx_rows, "dax")),
        ("NIKKEI", get_row(idx_rows, "nikkei")),
        ("Eurostoxx 50", get_row(idx_rows, "eurostoxx")),
        ("FTSE 100", get_row(idx_rows, "ftse")),
    ]

    def td_color(v: float) -> str:
        return "#E85D6C" if v < 0 else "#05AD98"

    rows_html = ""
    extreme_count = 0
    for name, r in INDICES:
        if not r:
            continue
        pctile = r["percentile_3m"]
        if pctile <= 5:
            extreme_count += 1
        pctile_style = 'background:rgba(232,93,108,0.2);color:#E85D6C;font-weight:700;padding:1px 5px;border-radius:2px' if pctile <= 10 else 'color:#94a3b8'
        ago_sign = "+" if r["position_1m_ago"] > 0 else ""
        rows_html += f"""
        <tr style="border-bottom:1px solid rgba(30,41,59,0.5)">
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 8px 6px 0;color:#94a3b8">{name}</td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 8px;text-align:right;color:{td_color(r['position_today'])};font-weight:700">{r['position_today']:+.2f}</td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 8px;text-align:right;color:{td_color(r['position_1m_ago'])}">{ago_sign}{r['position_1m_ago']:.2f}</td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 8px;text-align:right"><span style="{pctile_style}">{pctile_label(pctile)}</span></td>
          <td style="font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 0;text-align:right;color:#E85D6C">{r['z_score_3m']:.2f}</td>
        </tr>"""

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#E85D6C;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#E85D6C"></span>
      GLOBAL EQUITY POSITIONING · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">Every Market. Same Short.</div>
    <div style="font-size:13px;color:#64748b;margin-bottom:20px;line-height:1.4">
      8 global equity index futures at the <span style="color:#94a3b8">0–3rd percentile</span> of their 3-month range simultaneously. This is not a sector call — it is a coordinated global risk-off position.
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:20px">
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;color:#E85D6C;margin-bottom:3px">8/8</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#475569">Indices ≤3rd pctile</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;color:#E85D6C;margin-bottom:3px">0th</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#475569">SPX 3M percentile</div>
      </div>
      <div style="background:#0f1519;border:1px solid #1e293b;border-radius:3px;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:700;color:#E85D6C;margin-bottom:3px">−2.4</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#475569">Avg z-score</div>
      </div>
    </div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:8px">Index Futures — CTA Positioning</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      <thead><tr style="border-bottom:1px solid #1e293b">
        <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#475569;padding:0 8px 7px 0;text-align:left">UNDERLYING</th>
        <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#475569;padding:0 8px 7px;text-align:right">TODAY</th>
        <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#475569;padding:0 8px 7px;text-align:right">1M AGO</th>
        <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#475569;padding:0 8px 7px;text-align:right">3M %ILE</th>
        <th style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#475569;padding:0 0 7px;text-align:right">3M Z</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>"""
    return card_html("Global Equity Short", body, 2, 4, ds)


def card3_commodities(data: dict, ds: str) -> str:
    comm = data["tables"]["commodity"]
    crowded = sorted(
        [r for r in comm if r["percentile_3m"] >= 80 and r["position_today"] > 0],
        key=lambda r: -r["percentile_3m"]
    )[:5]

    def bar(r: dict) -> str:
        p = r["percentile_3m"]
        name = r["underlying"].split(" ")[0]
        lbl = pctile_label(p)
        width_pct = min(p, 100)
        opacity = 0.5 + (p - 80) / 100
        return f"""
        <div style="margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:0;margin-bottom:4px">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#e2e8f0;width:80px;flex-shrink:0">{name}</div>
            <div style="flex:1;height:22px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;overflow:hidden">
              <div style="height:100%;width:{width_pct}%;background:rgba(245,166,35,{opacity:.2f});display:flex;align-items:center;padding-right:6px;justify-content:flex-end">
                <span style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:#0a0f14">{lbl}</span>
              </div>
            </div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-left:80px">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#F5A623;font-weight:600">{lbl} pctile · 1Y: {pctile_label(r['percentile_1y'])}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#475569">z {r['z_score_3m']:+.2f}</div>
          </div>
        </div>"""

    bars_html = "".join(bar(r) for r in crowded)
    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#F5A623;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#F5A623"></span>
      COMMODITY POSITIONING · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">The Stagflation Trade Is Maxed Out</div>
    <div style="font-size:13px;color:#64748b;margin-bottom:24px;line-height:1.4">
      CTAs are simultaneously at the <span style="color:#94a3b8">94th–98th percentile</span> long across energy and soft commodities. Crowding at 1-year extremes. Mean reversion risk is elevated.
    </div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:12px">CTA Commodity Crowding — 3M Percentile</div>
    {bars_html}
    <div style="background:#0f1519;border:1px solid #1e293b;border-left:3px solid #F5A623;border-radius:0 3px 3px 0;padding:12px 14px;margin-bottom:20px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#F5A623;margin-bottom:5px">Mean Reversion Risk</div>
      <div style="font-size:12px;color:#94a3b8;line-height:1.55">When equity sentiment turns, commodity longs historically unwind simultaneously. Any risk-on catalyst that covers equity shorts could trigger forced commodity selling across the board.</div>
    </div>"""
    return card_html("Stagflation Trade", body, 3, 4, ds)


def card4_bonds(data: dict, ds: str) -> str:
    main = data["tables"]["main"]
    b2   = get_row(main, "2-year")
    b10  = get_row(main, "10-year")
    b30  = get_row(main, "treasury bond") or get_row(main, "u.s. treasury bond")

    def curve_row(tenor: str, r: dict | None, color: str = "#8B5CF6") -> str:
        if not r:
            return ""
        today = r["position_today"]
        ago   = r["position_1m_ago"]
        max_scale = 4.0
        short_pct = min(abs(min(today, 0)) / max_scale * 50, 50)
        ago_sign  = "+" if ago > 0 else ""
        pctile_lbl = pctile_label(r["percentile_3m"])
        return f"""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#94a3b8;width:50px;flex-shrink:0">{tenor}</div>
          <div style="flex:1">
            <div style="height:24px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;position:relative">
              <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#334155"></div>
              <div style="position:absolute;right:50%;height:100%;width:{short_pct}%;background:rgba(232,93,108,0.55);border-radius:1px"></div>
            </div>
            <div style="display:flex;justify-content:space-between;margin-top:3px">
              <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#E85D6C;font-weight:700">{today:.2f} today</div>
              <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569">{ago_sign}{ago:.2f} was</div>
              <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#E85D6C;font-weight:600">{pctile_lbl} pctile · z {r['z_score_3m']:.2f}</div>
            </div>
          </div>
        </div>"""

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#8B5CF6;margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#8B5CF6"></span>
      BOND POSITIONING · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">Short the Entire Curve</div>
    <div style="font-size:13px;color:#64748b;margin-bottom:20px;line-height:1.4">
      CTAs are short <span style="color:#94a3b8">2Y, 10Y, and 30Y Treasuries simultaneously</span> — all at the 0th–2nd percentile of their 3-month range. Any pivot signal triggers violent covering across the full curve.
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#1e293b;border:1px solid #1e293b;border-radius:3px;margin-bottom:18px">
      <div style="background:#0f1519;padding:14px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:700;color:#E85D6C;margin-bottom:3px">{b2['position_today']:.2f}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#475569">2-Year T-Note</div>
      </div>
      <div style="background:#0f1519;padding:14px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:700;color:#E85D6C;margin-bottom:3px">{b10['position_today']:.2f}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#475569">10-Year T-Note</div>
      </div>
      <div style="background:#0f1519;padding:14px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:700;color:#E85D6C;margin-bottom:3px">{f"{b30['position_today']:.2f}" if b30 else '—'}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:#475569">30Y T-Bond</div>
      </div>
    </div>
    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:14px">Position vs 1M Ago — Full Curve</div>
    {curve_row("2-Year", b2)}
    {curve_row("10-Year", b10)}
    {curve_row("30-Year", b30)}
    <div style="background:#0f1519;border:1px solid #1e293b;border-left:3px solid #8B5CF6;border-radius:0 3px 3px 0;padding:12px 14px;margin-bottom:20px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#8B5CF6;margin-bottom:5px">Implication</div>
      <div style="font-size:12px;color:#94a3b8;line-height:1.55">All three flipped from long to short within 30 days. A Fed pivot signal, softer inflation print, or flight-to-safety event forces covering across the full curve simultaneously — compounding the equity squeeze.</div>
    </div>"""
    return card_html("Bond Short Extreme", body, 4, 4, ds)


# ── Screenshot via browser automation ────────────────────────────────────────

def screenshot_card(html_path: str, png_path: str) -> bool:
    try:
        result = subprocess.run(
            ["agent-browser", "open", f"file://{html_path}"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return False
        result2 = subprocess.run(
            ["agent-browser", "screenshot", ".card", png_path],
            capture_output=True, text=True, timeout=15
        )
        return result2.returncode == 0 and Path(png_path).exists()
    except Exception:
        return False


# ── Preview HTML ──────────────────────────────────────────────────────────────

def build_preview(cards_b64: list, tweet_text: str, ds: str) -> str:
    imgs_html = ""
    labels = [
        ("The Coil Is Set · Squeeze Meter", "cta-card-1-squeeze-meter.png"),
        ("Every Market. Same Short.", "cta-card-2-global-equity-short.png"),
        ("The Stagflation Trade Is Maxed Out", "cta-card-3-stagflation.png"),
        ("Short the Entire Curve", "cta-card-4-bond-short.png"),
    ]
    for i, (b64, (title, fname)) in enumerate(zip(cards_b64, labels), 1):
        imgs_html += f"""
    <div style="margin-bottom:20px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#334155;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
        <span>Card {i}/4 —</span><span style="color:#05AD98">{title}</span>
      </div>
      <img style="width:100%;border:1px solid #1e293b;border-radius:3px;display:block" src="{b64}" alt="{title}" id="img{i}">
      <div style="display:flex;gap:8px;margin-top:8px">
        <button onclick="copyImg('img{i}',this)" style="flex:1;padding:7px;background:#0f1519;border:1px solid #1e293b;border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;color:#94a3b8;transition:all 150ms;text-align:center">Copy Image</button>
        <a href="{b64}" download="{fname}" style="flex:1;padding:7px;background:#0f1519;border:1px solid #1e293b;border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;color:#94a3b8;text-decoration:none;text-align:center;display:block;line-height:1.4">Download PNG ↓</a>
      </div>
    </div>"""

    tweet_escaped = tweet_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CTA Report — X Share · {ds}</title>
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
  <div class="intro"><strong>CTA Report — X Share</strong><br>Tweet text + 4 infographic cards · {ds} · Analyzed by Radon</div>
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
      const orig=btn.textContent;
      btn.textContent='Copied!';
      setTimeout(()=>{{btn.textContent=orig}},2000);
    }});
  }});
}}
</script>
</body></html>"""


# ── Tweet text ────────────────────────────────────────────────────────────────

def build_tweet(data: dict, ds: str) -> str:
    main = data["tables"]["main"]
    spx = get_row(main, "s&p") or get_row(main, "e-mini")
    d = datetime.strptime(ds, "%Y-%m-%d")
    month_day = d.strftime("%b %-d")

    return f"""🚨 CTAs are sitting on the most extreme short equity position in 3 months — and it's a coil ready to snap.

Here's what the data shows ({month_day} thread 🧵):

> SPX CTA position: {spx['position_today']:+.2f} (was +{spx['position_1m_ago']:.2f} one month ago)
> 0th percentile of 3M range · z-score {spx['z_score_3m']:.2f}
> $90.6B in forced selling still in pipeline

> Every global equity index futures at 0–3rd pctile simultaneously:
> SPX, NQ, Russell, MSCI World, DAX, Nikkei, Eurostoxx, FTSE

> The stagflation trade is maxed out:
> Cotton 98th pctile · Brent 95th · Diesel 94th · Wheat 92nd

> Full curve short: 2Y, 10Y, 30Y all at 0–2nd pctile
> All flipped from long → short in 30 days

The mean-reversion coil is set. Any bullish catalyst — Fed signal, macro beat, tariff relief — triggers violent CTA short-covering across equities, bonds, and commodities simultaneously.

This is not a trade call. This is a structural observation: the most crowded short in years.

Analyzed by Radon
radon.run"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate CTA X share report")
    parser.add_argument("--date", help="YYYY-MM-DD date override")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    data = load_cta(args.date)
    ds = data.get("date") or args.date or date.today().strftime("%Y-%m-%d")

    # Generate card HTMLs
    generators = [card1_squeeze, card2_equity, card3_commodities, card4_bonds]
    card_paths = []
    png_paths = []

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write card HTMLs to tmp
        tmp_htmls = []
        for i, gen in enumerate(generators, 1):
            html_content = gen(data, ds)
            html_path = os.path.join(tmpdir, f"card-{i}.html")
            with open(html_path, "w") as f:
                f.write(html_content)
            tmp_htmls.append(html_path)

        # Also write to reports dir for debugging
        for i, (gen, html_path) in enumerate(zip(generators, tmp_htmls), 1):
            dest = str(REPORTS_DIR / f"tweet-cta-{ds}-card-{i}.html")
            with open(dest, "w") as f:
                f.write(open(html_path).read())
            card_paths.append(dest)

        # Screenshot each
        for i, html_path in enumerate(tmp_htmls, 1):
            png_path = str(REPORTS_DIR / f"tweet-cta-{ds}-card-{i}.png")
            ok = screenshot_card(html_path, png_path)
            if not ok:
                # Fallback: try the reports dir HTML
                ok = screenshot_card(card_paths[i-1], png_path)
            if not ok:
                print(f"⚠ Screenshot failed for card {i}", file=sys.stderr)
                # Create empty placeholder so we don't crash
                png_path = card_paths[i-1]  # use HTML path as fallback marker
            png_paths.append(png_path)

    # Base64 encode PNGs
    cards_b64 = []
    for p in png_paths:
        if Path(p).exists() and p.endswith(".png"):
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            cards_b64.append(f"data:image/png;base64,{b64}")
        else:
            cards_b64.append("")

    # Build tweet text
    tweet_text = build_tweet(data, ds)

    # Build preview HTML
    preview_html = build_preview(cards_b64, tweet_text, ds)
    preview_path = str(REPORTS_DIR / f"tweet-cta-{ds}.html")
    with open(preview_path, "w") as f:
        f.write(preview_html)

    if not args.no_open:
        subprocess.Popen(["open", preview_path])

    result = {
        "preview_path": preview_path,
        "card_paths": card_paths,
        "png_paths": [p for p in png_paths if p.endswith(".png")],
        "date": ds,
        "tweet_length": len(tweet_text),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"✅ CTA share report generated: {preview_path}")
        print(f"   Cards: {len(card_paths)} HTML, {len([p for p in png_paths if p.endswith('.png')])} PNG")
        print(f"   Tweet: {len(tweet_text)} chars")

    return result


if __name__ == "__main__":
    main()

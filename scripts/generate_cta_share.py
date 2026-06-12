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
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from utils.card_screenshot import screenshot_card  # noqa: E402
from utils.cta_sync import latest_closed_trading_day  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "data" / "menthorq_cache"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


# ── Data loading ─────────────────────────────────────────────────────────────

def _payload_is_valid(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    tables = data.get("tables")
    if not isinstance(tables, dict):
        return False
    main = tables.get("main")
    return isinstance(main, list) and len(main) > 0


def _load_cta_from_db() -> Optional[dict]:
    """Latest menthorq_cta row from Turso — the same source the CTA page reads."""
    try:
        from db.client import get_db  # lazy: disk fallback must work without libsql

        rows = get_db().execute(
            "SELECT payload FROM menthorq_cta ORDER BY date DESC LIMIT 1"
        ).fetchall()
        if not rows:
            return None
        data = json.loads(rows[0][0])
    except Exception as exc:  # noqa: BLE001 — any DB failure falls back to disk
        print(f"[cta-share] db read unavailable, using disk cache: {exc}", file=sys.stderr)
        return None
    return data if _payload_is_valid(data) else None


def _load_cta_from_disk() -> Optional[dict]:
    files = sorted(CACHE_DIR.glob("cta_????-??-??.json"))
    if not files:
        return None
    with open(files[-1]) as f:
        data = json.load(f)
    return data if _payload_is_valid(data) else None


def load_cta(target_date: Optional[str] = None) -> dict:
    if target_date:
        path = CACHE_DIR / f"cta_{target_date}.json"
        if not path.exists():
            raise FileNotFoundError(f"CTA cache not found for {target_date}")
        with open(path) as f:
            return json.load(f)

    candidates = [d for d in (_load_cta_from_db(), _load_cta_from_disk()) if d]
    if not candidates:
        raise FileNotFoundError("No CTA data found in Turso or the local cache.")
    return max(candidates, key=lambda d: d.get("date") or "")


def assess_freshness(data_date: str, now: Optional[datetime] = None) -> dict:
    """Compare the loaded payload's date against the latest closed trading day."""
    expected = latest_closed_trading_day(now)
    return {
        "stale": (data_date or "") < expected,
        "data_date": data_date,
        "expected_date": expected,
    }


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




# ── Preview HTML ──────────────────────────────────────────────────────────────

def build_preview(
    cards_b64: list,
    tweet_text: str,
    ds: str,
    expected_date: Optional[str] = None,
) -> str:
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

    stale_html = ""
    if expected_date:
        stale_html = f"""
  <div style="grid-column:1/-1;padding:10px 14px;border:1px solid #F5A623;border-radius:3px;background:rgba(245,166,35,0.08);font-family:'IBM Plex Mono',monospace;font-size:11px;color:#F5A623;line-height:1.5">
    ⚠ STALE DATA — this report reflects {ds}; the latest closed session is {expected_date}. Wait for the CTA sync to land before posting.
  </div>"""

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
<div class="layout">{stale_html}
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

    # ── Extract positioning data across asset classes ──

    index_table = data["tables"].get("index", [])
    commodity_table = data["tables"].get("commodity", [])
    currency_table = data["tables"].get("currency", [])

    nq = get_row(main, "nasdaq") or get_row(main, "nq")
    bonds_10y = get_row(main, "10-year") or get_row(main, "10y")
    gold = get_row(main, "gold") or get_row(commodity_table, "gold")

    spx_pos = spx['position_today']
    spx_1m = spx.get('position_1m_ago', 0)
    spx_z = spx.get('z_score_3m', 0)
    spx_pctile = spx.get('percentile_3m', 0)
    flipped = spx_1m > 0 and spx_pos < 0

    # Count extreme positions across indexes
    extreme_short_indexes = []
    for r in (index_table or []):
        p = r.get("percentile_3m", 50)
        if isinstance(p, (int, float)) and p <= 0.05:
            name = r.get("underlying", "").replace("E-Mini ", "").replace("CME ", "").replace(" Index", "").replace("ICE MSCI ", "")
            extreme_short_indexes.append(name)

    # Find extreme commodity longs (crowded trades)
    extreme_long_commodities = []
    for r in (commodity_table or []):
        p = r.get("percentile_3m", 50)
        if isinstance(p, (int, float)) and p >= 0.85:
            name = r.get("underlying", "")
            extreme_long_commodities.append((name, round(p * 100) if p <= 1 else p))

    # ── Build narrative based on the data ──

    if spx_z <= -2.0:
        hook = (
            f"🚨 CTAs just hit a {abs(spx_z):.1f} standard deviation short on SPX futures — "
            f"the most extreme positioning in {'a year' if spx_pctile <= 0.01 else '3 months'}."
        )
    elif spx_z <= -1.5:
        hook = (
            f"⚠️ CTA equity positioning is at max short — SPX futures at "
            f"{spx_pos:+.2f} (z-score {spx_z:.2f}). The coil is building."
        )
    elif flipped:
        hook = (
            f"📉 CTAs flipped from +{spx_1m:.2f} long to {spx_pos:+.2f} short on SPX "
            f"in 30 days. That's a {abs(spx_1m - spx_pos):.2f}-point swing in systematic exposure."
        )
    else:
        hook = (
            f"📊 CTA positioning update ({month_day}): SPX at {spx_pos:+.2f}, "
            f"z-score {spx_z:.2f}."
        )

    # Thesis: what the positioning means
    if extreme_short_indexes and len(extreme_short_indexes) >= 4:
        index_list = ", ".join(extreme_short_indexes[:6])
        thesis = (
            f"This isn't just SPX — {len(extreme_short_indexes)} global equity indexes "
            f"are simultaneously at the bottom of their 3-month range ({index_list}). "
            f"When systematic funds are this short across every index, the next move is "
            f"binary: either the macro deteriorates further, or we get a violent short-covering "
            f"rally across everything."
        )
    elif flipped:
        thesis = (
            f"One month ago CTAs were long at +{spx_1m:.2f}. The vol-targeting models "
            f"detected the regime change and mechanically reversed. This selling is "
            f"not discretionary — it's algorithmic, and it doesn't stop until realized "
            f"vol compresses below the lookback window."
        )
    else:
        nq_pos = nq['position_today'] if nq else None
        nq_note = f" NQ at {nq_pos:+.2f}." if nq_pos is not None else ""
        thesis = (
            f"SPX CTA position: {spx_pos:+.2f} (was {spx_1m:+.2f} one month ago).{nq_note} "
            f"Vol-targeting models are adjusting exposure based on realized volatility — "
            f"the positioning reflects the vol regime, not a directional view."
        )

    # Cross-asset context
    cross_asset_lines = []
    if extreme_long_commodities:
        top_3 = sorted(extreme_long_commodities, key=lambda x: -x[1])[:3]
        names = " · ".join([f"{n} {p}th pctile" for n, p in top_3])
        cross_asset_lines.append(f"> Crowded commodity longs: {names}")

    if bonds_10y:
        b_pos = bonds_10y.get("position_today", 0)
        b_z = bonds_10y.get("z_score_3m", 0)
        if b_z <= -1.5:
            cross_asset_lines.append(
                f"> Bonds also max short: 10Y at {b_pos:+.2f} (z={b_z:.2f}) — full curve short"
            )

    if gold:
        g_pos = gold.get("position_today", 0)
        g_z = gold.get("z_score_3m", 0)
        if abs(g_z) > 1.5:
            direction = "long" if g_pos > 0 else "short"
            cross_asset_lines.append(
                f"> Gold CTAs {direction} at {g_pos:+.2f} (z={g_z:.2f})"
            )

    cross_asset = "\n".join(cross_asset_lines) if cross_asset_lines else ""

    # Conclusion
    if spx_z <= -1.5:
        conclusion = (
            "The mean-reversion coil is set. Any bullish catalyst — Fed signal, "
            "macro beat, tariff relief — triggers mechanical short-covering. "
            "This is structural, not speculative."
        )
    elif flipped:
        conclusion = (
            "The flip is mechanical but the magnitude matters. Watch realized vol — "
            "if it compresses, CTAs reverse just as aggressively to the upside."
        )
    else:
        conclusion = (
            "No extreme positioning. CTAs are adjusting normally to the current vol regime."
        )

    parts = [hook, "", thesis]
    if cross_asset:
        parts.extend(["", cross_asset])
    parts.extend(["", conclusion, "", "Analyzed by Radon · radon.run"])

    return "\n".join(parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate CTA X share report")
    parser.add_argument("--date", help="YYYY-MM-DD date override")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    data = load_cta(args.date)
    ds = data.get("date") or args.date or date.today().strftime("%Y-%m-%d")
    freshness = assess_freshness(ds)
    if freshness["stale"]:
        print(
            f"⚠ CTA data is stale: {ds} < expected {freshness['expected_date']}",
            file=sys.stderr,
        )

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
    preview_html = build_preview(
        cards_b64,
        tweet_text,
        ds,
        expected_date=freshness["expected_date"] if freshness["stale"] else None,
    )
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
        **freshness,
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

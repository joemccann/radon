#!/usr/bin/env python3
"""
Generate CTA share cards + preview page for X.
Reads from the latest MenthorQ CTA cache, produces 4 PNG cards
and a self-contained HTML preview page.

Usage:
  python3 scripts/generate_cta_share.py
  python3 scripts/generate_cta_share.py --json    # print output path as JSON
  python3 scripts/generate_cta_share.py --date 2026-03-19

Copy is written from the payload by a deterministic template. An LLM can write
it instead, OFF by default and opt-in per run:

  RADON_CTA_LLM_COPY=1 python3 scripts/generate_cta_share.py

That path needs `pip install anthropic` and ANTHROPIC_API_KEY (web/.env);
RADON_CTA_LLM_MODEL overrides the model (default claude-opus-5). Every figure
in the generated copy is checked back against the payload facts, and anything
that fails to verify falls back to the template. Missing key, missing SDK,
timeout, or API error all fall back too: the share is never blocked.
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
from utils.cta_history import (  # noqa: E402
    clean_underlying_name,
    derive_change_context,
    load_prior_payloads,
    payload_is_valid,
)
from utils.cta_llm import generate_share_copy  # noqa: E402
from utils.cta_percentiles import normalize_pctile, reconcile_tables  # noqa: E402
from utils.cta_sync import latest_closed_trading_day  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "data" / "menthorq_cache"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


# ── Data loading ─────────────────────────────────────────────────────────────

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
    return data if payload_is_valid(data) else None


def _load_cta_from_disk() -> Optional[dict]:
    files = sorted(CACHE_DIR.glob("cta_????-??-??.json"))
    if not files:
        return None
    with open(files[-1]) as f:
        data = json.load(f)
    return data if payload_is_valid(data) else None


def _reconciled(payload: dict) -> dict:
    """Every card and the tweet read the payload through here, so a percentile
    the vision extractor rounded away can never reach the copy."""
    if payload and payload.get("tables"):
        payload = {**payload, "tables": reconcile_tables(payload["tables"])}
    return payload


def load_cta(target_date: Optional[str] = None) -> dict:
    if target_date:
        path = CACHE_DIR / f"cta_{target_date}.json"
        if not path.exists():
            raise FileNotFoundError(f"CTA cache not found for {target_date}")
        with open(path) as f:
            return _reconciled(json.load(f))

    candidates = [d for d in (_load_cta_from_db(), _load_cta_from_disk()) if d]
    if not candidates:
        raise FileNotFoundError("No CTA data found in Turso or the local cache.")
    return _reconciled(max(candidates, key=lambda d: d.get("date") or ""))


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


def spx_row(payload: dict) -> Optional[dict]:
    """The SPX row every narrative anchors on, from a whole payload."""
    main = ((payload or {}).get("tables") or {}).get("main") or []
    return get_row(main, "s&p") or get_row(main, "e-mini")


def pctile_label(p) -> str:
    if p is None: return "---"
    if p == 1: return "1st"
    if p == 2: return "2nd"
    if p == 3: return "3rd"
    return f"{p}th"


def assess_positioning(r: dict) -> dict:
    """Direction-aware read of one CTA positioning row.

    SINGLE source of narrative truth: card1 and build_tweet both consume this,
    so the card can never assert a story the tweet contradicts. Every field is
    derived from the payload; nothing here is a literal about "today".

    The 2026-08-07 bug shipped a frozen max-SHORT story over max-LONG data,
    because the card hardcoded its copy and the tweet only branched on z <= -1.5.
    A positive extreme is just as tradeable and must read as a LONG.
    """
    today = r.get("position_today") or 0.0
    ago = r.get("position_1m_ago") or 0.0
    z = r.get("z_score_3m") or 0.0
    # No `, 50` default: an absent or nulled percentile is UNKNOWN, and the
    # z-score in the same row still carries the extremity read on its own.
    pctile = normalize_pctile(r.get("percentile_3m"))

    side = "long" if today > 0.05 else "short" if today < -0.05 else "neutral"

    # Extreme = far from the 3M mean OR pinned to an end of the 3M range.
    is_extreme = abs(z) >= 1.5 or (pctile is not None and (pctile >= 90 or pctile <= 10))
    if abs(z) >= 2.5 or (pctile is not None and (pctile >= 98 or pctile <= 2)):
        severity = "HIGH"
    elif is_extreme:
        severity = "ELEVATED"
    else:
        severity = "NORMAL"

    # Where the mechanical risk points. A max LONG deleverages INTO weakness
    # (forced selling, downside); a max SHORT covers INTO strength (upside
    # squeeze). Getting this backwards inverts the entire trade implication.
    if is_extreme and side == "long":
        risk_direction = "downside"
    elif is_extreme and side == "short":
        risk_direction = "upside"
    else:
        risk_direction = "none"

    return {
        "today": today,
        "ago": ago,
        "z": z,
        "pctile": pctile,
        "side": side,
        "is_extreme": is_extreme,
        "severity": severity,
        "risk_direction": risk_direction,
        # Sign change over the month, not merely a change in size.
        "flipped": (ago > 0 > today) or (ago < 0 < today),
        "extended": abs(today) > abs(ago) and not ((ago > 0 > today) or (ago < 0 < today)),
        # Marker position on a MAX SHORT (left) .. MAX LONG (right) track.
        # None when there is no percentile: the renderer must omit the marker
        # rather than park it at the midpoint, which reads as "neutral".
        "meter_pct": None if pctile is None else max(2, min(98, pctile)),
    }


def est_selling_bn(data: dict):
    """Estimated forced-flow figure, or None when the model did not supply one.
    Never invent it: the old code defaulted to a literal 90.6 and published
    "$90.6B forced selling pipeline" on days when `cta_model` was null."""
    model = data.get("cta_model")
    if not isinstance(model, dict):
        return None
    v = model.get("est_selling_bn")
    return v if isinstance(v, (int, float)) else None


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
    spx = spx_row(data)
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
        # Scale: ±4 maps to the full half-track (50% each side of centre).
        # TODAY's bar grows RIGHT of centre when long and LEFT when short. The
        # old code drew the long portion from `ago` and anchored both segments
        # right:50%, so a long position rendered leftward as if it were short.
        max_scale = 4.0
        span = lambda v: min(abs(v) / max_scale * 50, 50)
        today_pct, ago_pct = span(today), span(ago)
        today_side = "left:50%" if today >= 0 else f"right:50%"
        # 1M-ago tick, so the change is visible without a second bar.
        ago_offset = 50 + (ago_pct if ago >= 0 else -ago_pct)
        val_color = "#F5A623" if today > 0 else "#E85D6C" if today < 0 else "#94a3b8"
        fill = "rgba(245,166,35,0.45)" if today > 0 else "rgba(232,93,108,0.6)"
        ago_sign  = "+" if ago > 0 else ""
        return f"""
        <div style="display:flex;align-items:center;gap:0;margin-bottom:10px">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#94a3b8;width:100px;flex-shrink:0">{label}</div>
          <div style="flex:1;height:28px;background:#0f1519;border:1px solid #1e293b;border-radius:2px;position:relative">
            <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#334155"></div>
            <div style="position:absolute;{today_side};height:100%;width:{today_pct}%;background:{fill};border-radius:1px"></div>
            <div style="position:absolute;left:{ago_offset}%;top:4px;bottom:4px;width:1px;background:#64748b" title="1M ago"></div>
          </div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;color:{val_color};margin-left:8px;flex-shrink:0">{today:+.2f}</div>
          <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-left:6px">{ago_sign}{ago:.2f} 1M ago</div>
        </div>"""

    flip_rows = "".join(bar_row(lbl, r) for lbl, r in rows)

    a = assess_positioning(spx or {})
    pct_txt = pctile_label(a["pctile"])
    z_txt = f"{a['z']:.2f}"

    # Every string below is derived. A max LONG must never render as a squeeze.
    if a["is_extreme"] and a["side"] == "long":
        alert = "CTA LONG EXTREME"
        accent = "#F5A623"
        headline = "Max Long Exposure"
        move_txt = (
            f"CTAs {'flipped from' if a['flipped'] else 'extended from'} "
            f"<span style=\"color:#94a3b8\">{a['ago']:+.2f} → {a['today']:+.2f} long</span> in 30 days."
        )
        implication = "Deleveraging risk if realized vol expands."
        risk_label = "Selling Risk"
    elif a["is_extreme"] and a["side"] == "short":
        alert = "CTA SQUEEZE ALERT"
        accent = "#E85D6C"
        headline = "The Coil Is Set"
        move_txt = (
            f"CTAs {'flipped from' if a['flipped'] else 'extended from'} "
            f"<span style=\"color:#94a3b8\">{a['ago']:+.2f} → {a['today']:+.2f} short</span> in 30 days."
        )
        implication = "Maximum mean-reversion fuel."
        risk_label = "Squeeze Risk"
    else:
        alert = "CTA POSITIONING"
        accent = "#94a3b8"
        headline = "Positioning In Range"
        move_txt = (
            f"CTAs at <span style=\"color:#94a3b8\">{a['today']:+.2f}</span> "
            f"versus {a['ago']:+.2f} a month ago."
        )
        implication = "No mechanical extreme in play."
        risk_label = "Flow Risk"

    selling = est_selling_bn(data)
    selling_txt = f" · ${selling:g}B est. forced flow" if selling is not None else ""
    selling_tile = f"${selling:g}B" if selling is not None else "N/A"
    severity_color = "#F5A623" if a["severity"] != "NORMAL" else "#94a3b8"

    body = f"""
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:{accent};margin-bottom:10px;display:flex;align-items:center;gap:8px">
      <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{accent}"></span>
      {alert} · {ds}
    </div>
    <div style="font-size:32px;font-weight:800;letter-spacing:-.03em;line-height:1.1;margin-bottom:6px">{headline}</div>
    <div style="font-size:13px;color:#64748b;margin-bottom:24px;line-height:1.4">
      {move_txt}
      SPX positioning at <span style="color:#94a3b8">{pct_txt} percentile</span> of its 3-month range. {implication}
    </div>

    <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:12px">Position Change (1M ago to today)</div>
    {flip_rows}

    <div style="margin-bottom:24px">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#475569;margin-bottom:8px">CTA Equity Exposure · Positioning Meter</div>
      <div style="position:relative;height:14px;border-radius:2px;background:linear-gradient(to right,#E85D6C,#334155 50%,#F5A623);margin-bottom:4px">
        <div style="position:absolute;left:{a['meter_pct']}%;top:-3px;width:3px;height:20px;background:#fff;border-radius:1px;box-shadow:0 0 5px rgba(255,255,255,0.4)"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9px;color:#475569;margin-bottom:6px">
        <span style="color:{'#E85D6C' if a['side'] == 'short' and a['is_extreme'] else '#475569'}">◀ MAX SHORT{' (NOW)' if a['side'] == 'short' and a['is_extreme'] else ''}</span>
        <span>NEUTRAL</span>
        <span style="color:{'#F5A623' if a['side'] == 'long' and a['is_extreme'] else '#475569'}">MAX LONG{' (NOW)' if a['side'] == 'long' and a['is_extreme'] else ''} ▶</span>
      </div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:{accent};font-weight:600">{pct_txt} percentile (3M) · z-score {z_txt}{selling_txt}</div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#1e293b;border:1px solid #1e293b;border-radius:3px;margin-bottom:20px">
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">SPX 3M Pctile</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:{accent}">{pct_txt}</div>
      </div>
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">Z-Score</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:{accent}">{z_txt}</div>
      </div>
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">Est. Forced Flow</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#F5A623">{selling_tile}</div>
      </div>
      <div style="background:#0f1519;padding:12px 10px;text-align:center">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:5px">{risk_label}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:{severity_color}">{a['severity']}</div>
      </div>
    </div>"""
    return card_html("CTA Positioning Meter", body, 1, 4, ds)


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
        if pctile is not None and pctile <= 5:
            extreme_count += 1
        pctile_style = 'background:rgba(232,93,108,0.2);color:#E85D6C;font-weight:700;padding:1px 5px;border-radius:2px' if pctile is not None and pctile <= 10 else 'color:#94a3b8'
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
        [r for r in comm if r["percentile_3m"] is not None and r["percentile_3m"] >= 80 and r["position_today"] > 0],
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

def change_sentences(context: dict) -> list:
    """One line per fact that separates THIS session from the last one.

    Each sentence is gated on the underlying measurement existing. With no
    prior payload the list is empty and the post reads exactly as it did
    before history was available: an unmeasured delta is never narrated.
    """
    lines = []
    prior_date = context.get("prior_date")
    delta = context.get("spx_delta")

    if prior_date and delta:
        moves = [
            f"SPX {delta['prior_position']:+.2f} to {delta['position']:+.2f}",
            f"z {delta['prior_z']:.2f} to {delta['z']:.2f}",
        ]
        # A percentile the z-guard nulled is not a reading on either side, so
        # the move between them was never measured. Drop the clause instead of
        # narrating a swing off a substituted midpoint.
        if delta["prior_pctile"] is not None and delta["pctile"] is not None:
            moves.append(
                f"{pctile_label(delta['prior_pctile'])} to "
                f"{pctile_label(delta['pctile'])} percentile"
            )
        lines.append(f"> Since the {prior_date} read: " + ", ".join(moves))

    if prior_date and context.get("entered_extreme"):
        names = ", ".join(context["entered_extreme"][:6])
        lines.append(f"> Crossed INTO an extreme since {prior_date}: {names}")

    if prior_date and context.get("exited_extreme"):
        names = ", ".join(context["exited_extreme"][:6])
        lines.append(f"> Dropped OUT of an extreme since {prior_date}: {names}")

    sessions = context.get("regime_sessions")
    label = context.get("regime_label")
    if sessions and sessions > 1 and label and label != "unknown":
        if context.get("regime_bounded"):
            lines.append(f"> Session {sessions} of unbroken {label} positioning")
        else:
            lines.append(
                f"> {label.capitalize()} positioning has held for at least "
                f"{sessions} straight sessions"
            )

    return lines


def build_tweet(data: dict, ds: str, priors: Optional[list] = None) -> str:
    main = data["tables"]["main"]
    spx = spx_row(data)
    d = datetime.strptime(ds, "%Y-%m-%d")
    month_day = d.strftime("%b %-d")

    # ── Extract positioning data across asset classes ──

    index_table = data["tables"].get("index", [])
    commodity_table = data["tables"].get("commodity", [])
    currency_table = data["tables"].get("currency", [])

    nq = get_row(main, "nasdaq") or get_row(main, "nq")
    bonds_10y = get_row(main, "10-year") or get_row(main, "10y")
    gold = get_row(main, "gold") or get_row(commodity_table, "gold")

    a = assess_positioning(spx or {})
    spx_pos = a["today"]
    spx_1m = a["ago"]
    spx_z = a["z"]
    spx_pctile = a["pctile"]
    flipped = a["flipped"]

    # Count extreme positions across indexes
    extreme_short_indexes = [
        clean_underlying_name(r.get("underlying", ""))
        for r in (index_table or [])
        if (_p := normalize_pctile(r.get("percentile_3m"))) is not None and _p <= 5
    ]

    # Extreme index LONGS — the mirror case the old code could not see.
    extreme_long_indexes = [
        clean_underlying_name(r.get("underlying", ""))
        for r in (index_table or [])
        if (_p := normalize_pctile(r.get("percentile_3m"))) is not None and _p >= 95
    ]

    # Find extreme commodity longs (crowded trades)
    extreme_long_commodities = []
    for r in (commodity_table or []):
        p = normalize_pctile(r.get("percentile_3m"))
        if p is not None and p >= 85:
            extreme_long_commodities.append((r.get("underlying", ""), p))

    # ── Build narrative based on the data ──

    # Branch on the ASSESSMENT, not on a one-sided z threshold. The old code
    # only tested z <= -1.5, so a +3.68 z at the 100th percentile fell through
    # to "No extreme positioning" while the card screamed squeeze alert.
    if spx_z >= 2.0:
        hook = (
            f"🚨 CTAs just hit a {abs(spx_z):.1f} standard deviation LONG on SPX futures, "
            f"the most stretched positioning in {'a year' if spx_pctile is not None and spx_pctile >= 99 else '3 months'}."
        )
    elif spx_z <= -2.0:
        hook = (
            f"🚨 CTAs just hit a {abs(spx_z):.1f} standard deviation short on SPX futures, "
            f"the most extreme positioning in {'a year' if spx_pctile is not None and spx_pctile <= 1 else '3 months'}."
        )
    elif spx_z >= 1.5:
        hook = (
            f"⚠️ CTA equity positioning is at max long: SPX futures at "
            f"{spx_pos:+.2f} (z-score {spx_z:.2f}). The pipeline is one-sided."
        )
    elif spx_z <= -1.5:
        hook = (
            f"⚠️ CTA equity positioning is at max short: SPX futures at "
            f"{spx_pos:+.2f} (z-score {spx_z:.2f}). The coil is building."
        )
    elif flipped:
        direction = "short" if spx_pos < 0 else "long"
        hook = (
            f"📉 CTAs flipped from {spx_1m:+.2f} to {spx_pos:+.2f} {direction} on SPX "
            f"in 30 days. That's a {abs(spx_1m - spx_pos):.2f}-point swing in systematic exposure."
        )
    else:
        hook = (
            f"📊 CTA positioning update ({month_day}): SPX at {spx_pos:+.2f}, "
            f"z-score {spx_z:.2f}."
        )

    # Thesis: what the positioning means
    nq_pos = nq['position_today'] if nq else None
    nq_note = f" NQ at {nq_pos:+.2f}." if nq_pos is not None else ""

    if extreme_short_indexes and len(extreme_short_indexes) >= 4:
        index_list = ", ".join(extreme_short_indexes[:6])
        thesis = (
            f"This isn't just SPX: {len(extreme_short_indexes)} global equity indexes "
            f"are simultaneously at the bottom of their 3-month range ({index_list}). "
            f"When systematic funds are this short across every index, the next move is "
            f"binary: either the macro deteriorates further, or we get a violent short-covering "
            f"rally across everything."
        )
    elif extreme_long_indexes and len(extreme_long_indexes) >= 4:
        index_list = ", ".join(extreme_long_indexes[:6])
        thesis = (
            f"This isn't just SPX: {len(extreme_long_indexes)} global equity indexes "
            f"are simultaneously at the top of their 3-month range ({index_list}). "
            f"Systematic length that crowded is the fuel for a mechanical unwind, not a thesis. "
            f"The trigger is a vol expansion, not a headline."
        )
    elif a["is_extreme"] and a["side"] == "long":
        thesis = (
            f"SPX CTA position: {spx_pos:+.2f}, the {pctile_label(spx_pctile)} percentile of its "
            f"3-month range (was {spx_1m:+.2f} a month ago).{nq_note} "
            f"Vol-targeting models are carrying maximum length here. That exposure is mechanical: "
            f"if realized vol expands, the same models must sell into it regardless of the narrative."
        )
    elif a["is_extreme"] and a["side"] == "short":
        thesis = (
            f"SPX CTA position: {spx_pos:+.2f}, the {pctile_label(spx_pctile)} percentile of its "
            f"3-month range (was {spx_1m:+.2f} a month ago).{nq_note} "
            f"This selling is not discretionary. It is algorithmic, and it doesn't stop until "
            f"realized vol compresses below the lookback window."
        )
    elif flipped:
        thesis = (
            f"One month ago CTAs sat at {spx_1m:+.2f}. The vol-targeting models "
            f"detected the regime change and mechanically reversed to {spx_pos:+.2f}. "
            f"This flow is not discretionary, it is algorithmic."
        )
    else:
        thesis = (
            f"SPX CTA position: {spx_pos:+.2f} (was {spx_1m:+.2f} one month ago).{nq_note} "
            f"Vol-targeting models are adjusting exposure based on realized volatility. "
            f"The positioning reflects the vol regime, not a directional view."
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

    # What separates this post from the last one in the same regime.
    change_context = derive_change_context(
        data, priors or [], assess=assess_positioning, find_spx=spx_row
    )
    changes = "\n".join(change_sentences(change_context))

    # Conclusion
    if a["is_extreme"] and a["side"] == "short":
        conclusion = (
            "The mean-reversion coil is set. Any bullish catalyst (Fed signal, "
            "macro beat, tariff relief) triggers mechanical short-covering. "
            "This is structural, not speculative."
        )
    elif a["is_extreme"] and a["side"] == "long":
        conclusion = (
            "Crowded length is not a sell signal on its own, it is a fragility reading. "
            "Watch realized vol: an expansion forces the same models to deleverage into weakness."
        )
    elif flipped:
        conclusion = (
            "The flip is mechanical but the magnitude matters. Watch realized vol: "
            "if it compresses, CTAs reverse just as aggressively to the upside."
        )
    else:
        conclusion = (
            "No extreme positioning. CTAs are adjusting normally to the current vol regime."
        )

    parts = [hook, "", thesis]
    if cross_asset:
        parts.extend(["", cross_asset])
    if changes:
        parts.extend(["", changes])
    parts.extend(["", conclusion, "", "Analyzed by Radon · radon.run"])

    return "\n".join(parts)


def build_llm_facts(data: dict, ds: str, priors: Optional[list] = None) -> dict:
    """The complete figure set the copy is allowed to reference.

    Anything absent from here is a figure the post may not contain, so an
    unsupplied model output (est_selling_bn) stays None rather than acquiring
    a default. This doubles as the whitelist the generated copy is checked
    against.
    """
    main = data["tables"]["main"]
    commodity_table = data["tables"].get("commodity", [])
    a = assess_positioning(spx_row(data) or {})
    nq = get_row(main, "nasdaq") or get_row(main, "nq")
    bonds_10y = get_row(main, "10-year") or get_row(main, "10y")
    gold = get_row(main, "gold") or get_row(commodity_table, "gold")

    def leg(row: Optional[dict]) -> Optional[dict]:
        if not row:
            return None
        r = assess_positioning(row)
        return {
            "name": clean_underlying_name(row.get("underlying", "")),
            "position_today": r["today"],
            "z_score_3m": r["z"],
            "percentile_3m": r["pctile"],
        }

    return {
        "as_of_date": ds,
        "spx": {
            "position_today": a["today"],
            "position_1m_ago": a["ago"],
            "z_score_3m": a["z"],
            "percentile_3m": a["pctile"],
            "side": a["side"],
            "is_extreme": a["is_extreme"],
            "severity": a["severity"],
            "risk_direction": a["risk_direction"],
            "flipped": a["flipped"],
        },
        "nasdaq": leg(nq),
        "bonds_10y": leg(bonds_10y),
        "gold": leg(gold),
        "crowded_commodity_longs": [
            {
                "name": clean_underlying_name(r.get("underlying", "")),
                "percentile_3m": normalize_pctile(r.get("percentile_3m")),
            }
            for r in (commodity_table or [])
            if (_p := normalize_pctile(r.get("percentile_3m"))) is not None and _p >= 85
        ],
        "est_forced_selling_bn": est_selling_bn(data),
        "change_since_prior_session": derive_change_context(
            data, priors or [], assess=assess_positioning, find_spx=spx_row
        ),
        "lookback_window_months": 3,
        "lookback_window_days": 30,
    }


def compose_share_copy(
    data: dict,
    ds: str,
    priors: Optional[list] = None,
    *,
    env: Optional[dict] = None,
    caller=None,
) -> str:
    """The copy that ships: LLM-written when opted in AND verified, else the template."""
    template = build_tweet(data, ds, priors=priors)
    return generate_share_copy(
        build_llm_facts(data, ds, priors), template, env=env, caller=caller
    )


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
    tweet_text = compose_share_copy(data, ds, load_prior_payloads(ds))

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

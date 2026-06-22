#!/usr/bin/env python3
"""F5 — Polymarket event-odds overlay.

Maps relevant Polymarket prediction markets to a Radon ticker (macro / index
mapping to start, e.g. an FOMC or CPI market -> SPX), converts each market's
binary midpoint to an implied probability, and compares it against an
options-skew-implied probability derived from ``fetch_options.py`` for the same
ticker / event window. The signed gap (``polymarket_prob - options_prob``) is
the divergence the dashboard surfaces alongside the F3 catalyst card.

Overlay row shape:

    {
      question,            # the Polymarket market question
      market_ticker,       # mapped Radon ticker (e.g. SPX)
      end_date,            # market resolution date (ISO, or None)
      polymarket_prob,     # binary midpoint -> implied probability (0..1)
      options_prob,        # options-skew-implied probability (0..1) | None
      divergence,          # polymarket_prob - options_prob | None
    }

Payload:

    {ticker, scan_time, overlays: [...]}

Per-source failure tolerance: a dead options fetch leaves ``options_prob`` and
``divergence`` as None rather than dropping the whole overlay (the Polymarket
side still carries signal). A structurally empty payload (no overlays) is NOT
cached as a success — caching it would latch an empty surface
(cf. feedback_dont_cache_empty_results).

Usage:
    python3 scripts/fetch_event_odds.py SPX            # human summary
    python3 scripts/fetch_event_odds.py SPX --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from clients.polymarket_client import midpoint_to_probability
from fetch_options import fetch_options

# ── constants ─────────────────────────────────────────────────────
DATA_DIR = _PROJECT_DIR / "data"

_MARKET_LIMIT = 500

_EVENT_ODDS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS event_odds (
  ticker    TEXT NOT NULL,
  scan_time TEXT NOT NULL,
  payload   TEXT NOT NULL,
  PRIMARY KEY (ticker)
)
"""

# Macro / index keyword -> ticker mapping. A Polymarket market question that
# mentions any keyword in a group maps to that group's ticker. Lower-cased
# substring match keeps this robust to question phrasing.
_KEYWORD_TICKER_MAP: dict[str, tuple[str, ...]] = {
    "SPX": ("fomc", "fed", "federal reserve", "interest rate", "rate cut",
            "rate hike", "cpi", "inflation", "pce", "recession", "s&p 500",
            "jerome powell", "fed chair"),
    "QQQ": ("nasdaq", "tech earnings"),
    "GLD": ("gold price",),
    "USO": ("oil price", "crude oil"),
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum",),
}

# combined_bias -> base directional probability of the bullish/event-YES side.
_BIAS_BASE_PROB: dict[str, float] = {
    "BULLISH": 0.70,
    "LEAN_BULLISH": 0.60,
    "NEUTRAL": 0.50,
    "LEAN_BEARISH": 0.40,
    "BEARISH": 0.30,
}


# ── mapping ────────────────────────────────────────────────────────

def map_market_to_ticker(question: Any) -> Optional[str]:
    """Map a Polymarket market question to a Radon ticker, or None.

    Case-insensitive keyword match against ``_KEYWORD_TICKER_MAP``. Returns the
    first ticker whose keyword group matches.
    """
    if not question:
        return None
    text = str(question).lower()
    for ticker, keywords in _KEYWORD_TICKER_MAP.items():
        if any(keyword in text for keyword in keywords):
            return ticker
    return None


# ── options-skew implied probability ───────────────────────────────

def _clamp_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def options_skew_probability(options: Any) -> Optional[float]:
    """Derive an options-skew-implied probability of the bullish/event-YES side.

    Anchors on the combined chain+flow bias (a directional base rate) and nudges
    it with the put/call premium ratio so a heavier put skew lowers the implied
    probability and a heavier call skew raises it. Returns None when there is no
    options chain to read (NO_DATA), keeping the divergence honest rather than
    fabricating a 0.5.
    """
    if not isinstance(options, dict):
        return None
    chain = options.get("chain")
    if not isinstance(chain, dict):
        return None

    analysis = options.get("analysis") or {}
    bias = str(analysis.get("combined_bias") or "NEUTRAL").upper()
    base = _BIAS_BASE_PROB.get(bias, 0.50)

    pc_ratio = chain.get("put_call_ratio")
    skew_adjust = 0.0
    if isinstance(pc_ratio, (int, float)) and pc_ratio > 0:
        # pc_ratio == 1 is neutral. >1 (more puts) pulls probability down,
        # <1 (more calls) pulls it up. Bounded to +/-0.15.
        import math
        skew_adjust = max(-0.15, min(0.15, -0.15 * math.log10(float(pc_ratio))))

    return _clamp_unit(base + skew_adjust)


# ── divergence ─────────────────────────────────────────────────────

def compute_divergence(
    polymarket_prob: Optional[float],
    options_prob: Optional[float],
) -> Optional[float]:
    """Signed gap between the Polymarket and options-implied probabilities.

    Positive => Polymarket prices the event higher than the options market.
    None when either side is unavailable.
    """
    if polymarket_prob is None or options_prob is None:
        return None
    return polymarket_prob - options_prob


# ── overlay assembly ───────────────────────────────────────────────

def _yes_token_id(market: dict[str, Any]) -> Optional[str]:
    tokens = market.get("clobTokenIds") or market.get("clob_token_ids")
    if isinstance(tokens, list) and tokens:
        return str(tokens[0])
    if isinstance(tokens, str) and tokens:
        return tokens
    return None


def _safe_options(ticker: str) -> Optional[dict[str, Any]]:
    """Fetch options for the ticker, swallowing failures so one dead data
    source doesn't drop the Polymarket overlay."""
    try:
        return fetch_options(ticker)
    except Exception as exc:  # noqa: BLE001 — best-effort per overlay
        print(f"[event-odds] options fetch failed for {ticker}: {exc}", file=sys.stderr)
        return None


def fetch_event_odds(
    ticker: str,
    *,
    client: Any,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build the event-odds overlay for one ticker.

    Pulls the Polymarket markets list, keeps the markets that map to ``ticker``,
    converts each midpoint to a probability, derives the options-skew-implied
    probability once per ticker, and computes the divergence. Never raises on a
    per-market or options failure.
    """
    upper = ticker.upper()
    now = now or datetime.now(timezone.utc)

    try:
        markets = client.get_markets(active=True, limit=_MARKET_LIMIT)
    except Exception as exc:  # noqa: BLE001 — upstream failure tolerated
        print(f"[event-odds] markets fetch failed: {exc}", file=sys.stderr)
        markets = []

    mapped = [m for m in markets if isinstance(m, dict) and map_market_to_ticker(m.get("question")) == upper]

    overlays: list[dict[str, Any]] = []
    options_prob: Optional[float] = None
    options_fetched = False

    for market in mapped:
        token_id = _yes_token_id(market)
        polymarket_prob: Optional[float] = None
        if token_id:
            try:
                polymarket_prob = midpoint_to_probability(client.get_midpoint(token_id))
            except Exception as exc:  # noqa: BLE001
                print(f"[event-odds] midpoint failed for {token_id}: {exc}", file=sys.stderr)

        if not options_fetched:
            options_prob = options_skew_probability(_safe_options(upper))
            options_fetched = True

        overlays.append(
            {
                "question": market.get("question"),
                "market_ticker": upper,
                "end_date": market.get("endDate") or market.get("end_date"),
                "polymarket_prob": polymarket_prob,
                "options_prob": options_prob,
                "divergence": compute_divergence(polymarket_prob, options_prob),
            }
        )

    overlays.sort(
        key=lambda o: (abs(o["divergence"]) if o["divergence"] is not None else -1.0),
        reverse=True,
    )

    return {"ticker": upper, "overlays": overlays}


def has_overlay_activity(payload: dict[str, Any]) -> bool:
    """True when the payload carries at least one overlay. Gates the cache write
    and the dashboard badge."""
    return bool(payload.get("overlays"))


# ── persistence ───────────────────────────────────────────────────

def _cache_path(ticker: str) -> Path:
    return DATA_DIR / "event_odds" / f"{ticker.upper()}.json"


def _write_db_cache(payload: dict[str, Any], scan_time: str) -> None:
    """Persist the overlay snapshot to Turso. Best-effort: a DB failure must not
    crash the scan since the per-ticker JSON is the fallback."""
    try:
        from db import writer
        from db.client import get_db
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        db = get_db()
        db.execute(_EVENT_ODDS_TABLE_DDL)
        db.execute(
            "INSERT OR REPLACE INTO event_odds (ticker, scan_time, payload) VALUES (?, ?, ?)",
            (payload["ticker"], scan_time, json.dumps(payload)),
        )
        db.commit()
        writer.record_service_health("event-odds", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[event-odds] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(ticker: str, payload: dict[str, Any]) -> None:
    path = _cache_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def run(
    ticker: str,
    client: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fetch, compute, conditionally cache, and return the overlay payload.

    Empty payloads are returned (so callers can render an empty state) but NOT
    cached as success."""
    now = now or datetime.now(timezone.utc)
    if client is None:
        from clients.polymarket_client import PolymarketClient
        client = PolymarketClient()

    payload = fetch_event_odds(ticker, client=client, now=now)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["scan_time"] = scan_time

    if has_overlay_activity(payload):
        _write_db_cache(payload, scan_time)
        _write_json_cache(ticker, payload)
    else:
        print(f"[event-odds] {ticker.upper()}: no mapped markets — not caching", file=sys.stderr)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    ticker = payload["ticker"]
    overlays = payload["overlays"]
    print(f"\nEVENT ODDS OVERLAY — {ticker}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    if not overlays:
        print("  (no Polymarket markets mapped to this ticker)", file=sys.stderr)
        return
    for row in overlays[:10]:
        pm = row["polymarket_prob"]
        op = row["options_prob"]
        dv = row["divergence"]
        pm_s = f"{pm:.0%}" if pm is not None else "  ?"
        op_s = f"{op:.0%}" if op is not None else "  ?"
        dv_s = f"{dv:+.0%}" if dv is not None else "  ?"
        print(f"  PM {pm_s}  OPT {op_s}  DIV {dv_s}  {row['question']}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket event-odds overlay")
    parser.add_argument("ticker", help="Ticker symbol")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run(args.ticker)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

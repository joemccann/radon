#!/usr/bin/env python3
"""F4 — Congress + insider informed-flow surface.

Aggregates three Unusual Whales surfaces for a single ticker into one
normalised "informed flow" payload so the dashboard panel and the scanner
badge read one schema instead of UW's three different response shapes.

Sources (UWClient methods):
  - get_congress_recent_trades(ticker=...)  -> congress_trades[]
  - get_insider_transactions(ticker=...)    -> insider_trades[]
  - get_institutional_ownership(ticker)     -> institutional_summary

Output payload:

    {
      ticker,
      congress_trades:        [{ticker, person, date, side, amount, days_ago}],
      insider_trades:         [{ticker, person, title, date, side, shares, days_ago}],
      institutional_summary:  {ownership_pct, total_institutions, report_date} | None,
    }

``side`` is normalised to BUY / SELL / OTHER from each source's own vocabulary
(congress "Purchase"/"Sale", insider SEC transaction codes P/S). Rows are
sorted most-recent-first by ``days_ago``. Per-source failures are swallowed so
one dead endpoint doesn't take the whole surface down — institutional becomes
None, the trade lists simply omit the failed source.

Cached per-ticker to ``data/informed_flow/{TICKER}.json`` (disk fallback) and
to the Turso ``informed_flow`` table. A structurally empty payload (no congress
rows, no insider rows, no institutional summary) is NOT cached as a success —
that is the signature of a swallowed upstream failure, and caching it would
latch an empty surface (cf. feedback_dont_cache_empty_results).

Usage:
    python3 scripts/fetch_informed_flow.py AAPL            # human summary
    python3 scripts/fetch_informed_flow.py AAPL --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
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

# ── constants ─────────────────────────────────────────────────────
DATA_DIR = _PROJECT_DIR / "data"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDE_OTHER = "OTHER"

_CONGRESS_LIMIT = 50
_INSIDER_LIMIT = 50

_INFORMED_FLOW_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS informed_flow (
  ticker    TEXT NOT NULL,
  scan_time TEXT NOT NULL,
  payload   TEXT NOT NULL,
  PRIMARY KEY (ticker)
)
"""

_CONGRESS_BUY_TOKENS = ("purchase", "buy", "exchange")
_CONGRESS_SELL_TOKENS = ("sale", "sell")


# ── normalization ─────────────────────────────────────────────────

def _et_today(now: datetime) -> date:
    """The calendar day in ET for the given instant."""
    try:
        import zoneinfo
        return now.astimezone(zoneinfo.ZoneInfo("America/New_York")).date()
    except Exception:
        return now.astimezone(timezone.utc).date()


def _parse_day(value: Any) -> Optional[date]:
    """Parse an ISO date or datetime string to a calendar date (UTC day)."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if "T" in text:
            cleaned = text.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).astimezone(timezone.utc).date()
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def _days_ago(event_day: date, today: date) -> int:
    return (today - event_day).days


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _dict_from_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if data is None and payload:
            return payload
    return {}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> Optional[int]:
    parsed = _to_float(value)
    return int(parsed) if parsed is not None else None


def _congress_side(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if any(token in text for token in _CONGRESS_BUY_TOKENS):
        return SIDE_BUY
    if any(token in text for token in _CONGRESS_SELL_TOKENS):
        return SIDE_SELL
    return SIDE_OTHER


def _insider_side(code: Any) -> str:
    text = str(code or "").strip().upper()
    if text.startswith("P"):
        return SIDE_BUY
    if text.startswith("S"):
        return SIDE_SELL
    return SIDE_OTHER


def _normalize_congress(payload: Any, ticker: str, today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _rows_from_payload(payload):
        event_day = _parse_day(
            raw.get("transaction_date") or raw.get("traded") or raw.get("report_date")
        )
        if not event_day:
            continue
        person = raw.get("reporter") or raw.get("name") or raw.get("member") or "Unknown"
        rows.append(
            {
                "ticker": (raw.get("ticker") or ticker).upper(),
                "person": person,
                "date": event_day.isoformat(),
                "side": _congress_side(raw.get("txn_type") or raw.get("transaction_type")),
                "amount": raw.get("amounts") or raw.get("amount"),
                "days_ago": _days_ago(event_day, today),
            }
        )
    rows.sort(key=lambda r: (r["days_ago"], r["person"]))
    return rows


def _normalize_insider(payload: Any, ticker: str, today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _rows_from_payload(payload):
        event_day = _parse_day(raw.get("transaction_date") or raw.get("filing_date"))
        if not event_day:
            continue
        person = raw.get("full_name") or raw.get("owner_name") or raw.get("name") or "Unknown"
        rows.append(
            {
                "ticker": (raw.get("ticker") or ticker).upper(),
                "person": person,
                "title": raw.get("officer_title") or raw.get("title"),
                "date": event_day.isoformat(),
                "side": _insider_side(raw.get("transaction_code") or raw.get("code")),
                "shares": _to_int(raw.get("shares") or raw.get("amount")),
                "price": _to_float(raw.get("price")),
                "days_ago": _days_ago(event_day, today),
            }
        )
    rows.sort(key=lambda r: (r["days_ago"], r["person"]))
    return rows


def _normalize_institutional(payload: Any, ticker: str) -> Optional[dict[str, Any]]:
    data = _dict_from_payload(payload)
    if not data:
        return None
    raw_pct = _to_float(data.get("institutional_ownership") or data.get("ownership"))
    ownership_pct = raw_pct * 100 if (raw_pct is not None and raw_pct <= 1.0) else raw_pct
    total = _to_int(data.get("total_institutions") or data.get("institutions"))
    if ownership_pct is None and total is None:
        return None
    return {
        "ticker": (data.get("ticker") or ticker).upper(),
        "ownership_pct": ownership_pct,
        "total_institutions": total,
        "report_date": data.get("report_date") or data.get("date"),
    }


def _safe_call(fn) -> Any:
    """Call a UW endpoint, swallowing per-source failures so one dead endpoint
    doesn't take the whole surface down (cf. partial-source-failure tolerance)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — best-effort per source
        print(f"[informed-flow] source failed: {exc}", file=sys.stderr)
        return None


def fetch_informed_flow(
    ticker: str,
    *,
    client: Any,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Aggregate + normalise congress / insider / institutional data for one
    ticker into a single informed-flow payload. Never raises on per-source
    failure — a failed source contributes an empty list / None."""
    upper = ticker.upper()
    now = now or datetime.now(timezone.utc)
    today = _et_today(now)

    congress = _normalize_congress(
        _safe_call(
            lambda: client.get_congress_recent_trades(ticker=upper, limit=_CONGRESS_LIMIT)
        ),
        upper,
        today,
    )
    insider = _normalize_insider(
        _safe_call(
            lambda: client.get_insider_transactions(ticker=upper, limit=_INSIDER_LIMIT)
        ),
        upper,
        today,
    )
    institutional = _normalize_institutional(
        _safe_call(lambda: client.get_institutional_ownership(upper)),
        upper,
    )

    return {
        "ticker": upper,
        "congress_trades": congress,
        "insider_trades": insider,
        "institutional_summary": institutional,
    }


def has_informed_activity(payload: dict[str, Any]) -> bool:
    """True when the payload carries any congress / insider / institutional
    signal. Drives the cache-write gate and the scanner badge."""
    if payload.get("congress_trades"):
        return True
    if payload.get("insider_trades"):
        return True
    if payload.get("institutional_summary"):
        return True
    return False


# ── persistence ───────────────────────────────────────────────────

def _cache_path(ticker: str) -> Path:
    return DATA_DIR / "informed_flow" / f"{ticker.upper()}.json"


def _write_db_cache(payload: dict[str, Any], scan_time: str) -> None:
    """Persist the informed-flow snapshot to Turso. Best-effort: a DB failure
    must not crash the scan since the per-ticker JSON is the fallback."""
    try:
        from db import writer
        from db.client import get_db
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        db = get_db()
        db.execute(_INFORMED_FLOW_TABLE_DDL)
        db.execute(
            "INSERT OR REPLACE INTO informed_flow (ticker, scan_time, payload) VALUES (?, ?, ?)",
            (payload["ticker"], scan_time, json.dumps(payload)),
        )
        db.commit()
        writer.record_service_health("informed-flow", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[informed-flow] db cache non-fatal: {exc}", file=sys.stderr)


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
    """Fetch, normalise, conditionally cache, and return the informed-flow
    payload. Structurally empty payloads are returned (so callers can render an
    empty state) but NOT cached as success."""
    now = now or datetime.now(timezone.utc)
    if client is None:
        from clients.uw_client import UWClient
        client = UWClient()

    payload = fetch_informed_flow(ticker, client=client, now=now)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["scan_time"] = scan_time

    if has_informed_activity(payload):
        _write_db_cache(payload, scan_time)
        _write_json_cache(ticker, payload)
    else:
        print(
            f"[informed-flow] {ticker.upper()}: empty payload — not caching",
            file=sys.stderr,
        )
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    ticker = payload["ticker"]
    congress = payload["congress_trades"]
    insider = payload["insider_trades"]
    inst = payload["institutional_summary"]
    print(f"\nINFORMED FLOW — {ticker}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Congress trades: {len(congress)}", file=sys.stderr)
    for row in congress[:5]:
        print(
            f"    {row['days_ago']:>3}d ago  {row['side']:<5} {row['person']} ({row['amount']})",
            file=sys.stderr,
        )
    print(f"  Insider trades: {len(insider)}", file=sys.stderr)
    for row in insider[:5]:
        print(
            f"    {row['days_ago']:>3}d ago  {row['side']:<5} {row['person']} ({row['shares']} sh)",
            file=sys.stderr,
        )
    if inst:
        print(
            f"  Institutional ownership: {inst['ownership_pct']}% across "
            f"{inst['total_institutions']} institutions",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Congress + insider informed-flow surface")
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

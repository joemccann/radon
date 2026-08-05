#!/usr/bin/env python3
"""Standalone per-ticker earnings-date service.

Answers: for ticker T and optional structure DTE D, is there an upcoming
earnings release within the window, and if so what date/session?

Source: UWClient.get_earnings_by_ticker(ticker) — history + upcoming rows.
Unlike fetch_catalysts.py (today's premarket/afterhours calendars only), this
looks forward across arbitrary DTE windows so scanners (Theta Harvester first)
can flag earnings risk.

Public API:
    next_earnings(client, ticker, now=None) -> dict | None
    within_dte(days_until, dte) -> bool
    annotate(client, ticker, dte=None, now=None, cache=False) -> payload
    batch_annotate(client, items, now=None, cache=False) -> list[payload]
    normalize_report_time(raw) -> premarket|postmarket|unknown

Payload:
    {
      ticker, report_date, report_time, days_until, source,
      expected_move_pct, within_dte, dte, missing
    }

Usage:
    python3 scripts/earnings_dates.py HONA --dte 16
    python3 scripts/earnings_dates.py HONA AAPL --dte 16 --json
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
CACHE_DIR = _PROJECT_DIR / "data" / "earnings_dates"

REPORT_TIME_PREMARKET = "premarket"
REPORT_TIME_POSTMARKET = "postmarket"
REPORT_TIME_UNKNOWN = "unknown"

_PREMARKET_TOKENS = (
    "premarket",
    "pre-market",
    "pre market",
    "bmo",
    "before market",
    "before the open",
    "before open",
)
_POSTMARKET_TOKENS = (
    "postmarket",
    "post-market",
    "post market",
    "afterhours",
    "after-hours",
    "after hours",
    "amc",
    "after market",
    "after the close",
    "after close",
)


# ── calendar helpers ──────────────────────────────────────────────

def _et_today(now: datetime) -> date:
    """The calendar day in ET for the given instant."""
    try:
        import zoneinfo
        return now.astimezone(zoneinfo.ZoneInfo("America/New_York")).date()
    except Exception:
        return now.astimezone(timezone.utc).date()


def _parse_day(value: Any) -> Optional[date]:
    """Parse an ISO date or datetime string to a calendar date."""
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


def _days_until(event_day: date, today: date) -> int:
    return (event_day - today).days


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _has_actual_eps(raw: dict[str, Any]) -> bool:
    """True when actual_eps is present and not null/empty (reported already)."""
    val = raw.get("actual_eps")
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    return True


# ── normalization ─────────────────────────────────────────────────

def normalize_report_time(raw: Any) -> str:
    """Map UW report_time variants to premarket | postmarket | unknown."""
    if raw is None:
        return REPORT_TIME_UNKNOWN
    text = str(raw).strip().lower()
    if not text:
        return REPORT_TIME_UNKNOWN
    if text in (REPORT_TIME_PREMARKET, REPORT_TIME_POSTMARKET):
        return text
    for token in _PREMARKET_TOKENS:
        if token in text:
            return REPORT_TIME_PREMARKET
    for token in _POSTMARKET_TOKENS:
        if token in text:
            return REPORT_TIME_POSTMARKET
    return REPORT_TIME_UNKNOWN


def parse_expected_move_pct(raw: Any) -> Optional[float]:
    """UW may send fraction (0.087 = 8.7%) or percent points. abs < 1 → * 100."""
    value = _to_float(raw)
    if value is None:
        return None
    if abs(value) < 1.0:
        return value * 100.0
    return value


def within_dte(days_until: int, dte: int) -> bool:
    """True iff earnings falls inside [0, dte] inclusive calendar days."""
    return 0 <= days_until <= dte


def _missing_payload(ticker: str, dte: Optional[int]) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "report_date": None,
        "report_time": None,
        "days_until": None,
        "source": None,
        "expected_move_pct": None,
        "within_dte": None,
        "dte": dte,
        "missing": True,
    }


def _select_next_row(rows: list[dict[str, Any]], today: date) -> Optional[dict[str, Any]]:
    """Earliest report_date >= ET today; same-day with actual_eps is past."""
    candidates: list[tuple[date, dict[str, Any]]] = []
    for raw in rows:
        event_day = _parse_day(raw.get("report_date"))
        if event_day is None:
            continue
        if event_day < today:
            continue
        if event_day == today and _has_actual_eps(raw):
            continue
        candidates.append((event_day, raw))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def next_earnings(
    client: Any,
    ticker: str,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """Return the next upcoming earnings row (normalized) or None.

    Normalized keys: report_date, report_time, days_until, source,
    expected_move_pct. Raises if the client raises (caller decides).
    """
    now = now or datetime.now(timezone.utc)
    today = _et_today(now)
    upper = ticker.upper()
    payload = client.get_earnings_by_ticker(upper)
    rows = _rows_from_payload(payload)
    raw = _select_next_row(rows, today)
    if raw is None:
        return None
    event_day = _parse_day(raw.get("report_date"))
    assert event_day is not None  # selected rows always parse
    return {
        "report_date": event_day.isoformat(),
        "report_time": normalize_report_time(raw.get("report_time")),
        "days_until": _days_until(event_day, today),
        "source": raw.get("source"),
        "expected_move_pct": parse_expected_move_pct(raw.get("expected_move_perc")),
    }


# ── annotate ──────────────────────────────────────────────────────

def annotate(
    client: Any,
    ticker: str,
    dte: Optional[int] = None,
    now: Optional[datetime] = None,
    *,
    cache: bool = False,
) -> dict[str, Any]:
    """Annotate one ticker with next-earnings fields + within_dte gate.

    On network/API errors returns a missing payload and does not cache
    (cf. do not latch empty failures). Successful missing (empty UW data)
    may still be returned; cache only when we have a real upcoming row.
    """
    now = now or datetime.now(timezone.utc)
    upper = ticker.upper()
    try:
        nxt = next_earnings(client, upper, now=now)
    except Exception as exc:  # noqa: BLE001 — surface missing, don't crash batch
        print(f"[earnings-dates] {upper}: fetch failed: {exc}", file=sys.stderr)
        return _missing_payload(upper, dte)

    if nxt is None:
        payload = _missing_payload(upper, dte)
        return payload

    days = nxt["days_until"]
    within: Optional[bool]
    if dte is None:
        within = None
    else:
        within = within_dte(days, dte)

    payload = {
        "ticker": upper,
        "report_date": nxt["report_date"],
        "report_time": nxt["report_time"],
        "days_until": days,
        "source": nxt["source"],
        "expected_move_pct": nxt["expected_move_pct"],
        "within_dte": within,
        "dte": dte,
        "missing": False,
    }
    if cache:
        _write_json_cache(upper, payload)
    return payload


def batch_annotate(
    client: Any,
    items: list[dict[str, Any]],
    now: Optional[datetime] = None,
    *,
    cache: bool = False,
) -> list[dict[str, Any]]:
    """Annotate many tickers. Each item: {ticker, dte?}."""
    now = now or datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    for item in items:
        ticker = item.get("ticker") or ""
        dte = item.get("dte")
        results.append(annotate(client, str(ticker), dte=dte, now=now, cache=cache))
    return results


# ── persistence ───────────────────────────────────────────────────

def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.json"


def _write_json_cache(ticker: str, payload: dict[str, Any]) -> None:
    """Best-effort disk cache. Only called for non-missing success payloads."""
    try:
        path = _cache_path(ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"[earnings-dates] cache write non-fatal: {exc}", file=sys.stderr)


# ── CLI ───────────────────────────────────────────────────────────

def _print_human(payloads: list[dict[str, Any]]) -> None:
    for p in payloads:
        ticker = p["ticker"]
        if p.get("missing"):
            print(f"{ticker}: missing (no upcoming earnings)")
            continue
        dte_part = ""
        if p.get("dte") is not None:
            flag = "within" if p.get("within_dte") else "outside"
            dte_part = f"  dte={p['dte']} ({flag})"
        move = p.get("expected_move_pct")
        move_s = f"{move:.2f}%" if isinstance(move, (int, float)) else "n/a"
        print(
            f"{ticker}: {p['report_date']} {p['report_time']}  "
            f"days_until={p['days_until']}  source={p.get('source')}  "
            f"exp_move={move_s}{dte_part}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Next earnings date / session / within-DTE for ticker(s)."
    )
    parser.add_argument("tickers", nargs="+", help="One or more tickers")
    parser.add_argument("--dte", type=int, default=None, help="Structure DTE window")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip writing data/earnings_dates/{TICKER}.json",
    )
    args = parser.parse_args(argv)

    from clients.uw_client import UWClient

    now = datetime.now(timezone.utc)
    items = [{"ticker": t, "dte": args.dte} for t in args.tickers]
    with UWClient() as client:
        results = batch_annotate(
            client, items, now=now, cache=not args.no_cache
        )

    if args.json:
        out = results[0] if len(results) == 1 else results
        print(json.dumps(out, indent=2))
    else:
        _print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""F3 — Earnings / FDA / economic catalyst feed.

Aggregates four Unusual Whales calendar endpoints into a single normalised
catalyst stream so the dashboard and scanner rows can show "days-to-catalyst"
context without each surface re-implementing four different response shapes.

Sources (UWClient methods):
  - get_earnings_premarket   -> type "earnings", source "earnings_premarket"
  - get_earnings_afterhours  -> type "earnings", source "earnings_afterhours"
  - get_fda_calendar         -> type "fda",      source "fda"
  - get_economic_calendar    -> type "economic", source "economic"

Every event becomes a catalyst row:

    {ticker, type, title, date, source, days_until, event_time?}

``date`` is an ISO ``YYYY-MM-DD`` calendar day (ET). ``days_until`` is the
whole-day distance from "now" (negative = past; past events are dropped by
default). ``ticker`` is None for market-wide economic events.

Output is cached to the Turso ``catalysts`` table (additive CREATE TABLE IF
NOT EXISTS guard so no migration is required) and to ``data/catalysts.json``
as the disk fallback, matching the scan -> Turso + JSON pattern used by
vcg_scan / cri_scan.

Usage:
    python3 scripts/fetch_catalysts.py            # human summary
    python3 scripts/fetch_catalysts.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
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
CATALYSTS_JSON = _PROJECT_DIR / "data" / "catalysts.json"

TYPE_EARNINGS = "earnings"
TYPE_FDA = "fda"
TYPE_ECONOMIC = "economic"

EARNINGS_LOOKAHEAD_SESSIONS = 5
EARNINGS_PAGE_LIMIT = 100
EARNINGS_MAX_PAGES = 3
FDA_LOOKAHEAD_DAYS = 365

_CATALYSTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS catalysts (
  scan_time TEXT NOT NULL,
  payload   TEXT NOT NULL
)
"""


# ── normalization ─────────────────────────────────────────────────

def _et_today(now: datetime) -> date:
    """The calendar day in ET for the given instant."""
    try:
        import zoneinfo
        return now.astimezone(zoneinfo.ZoneInfo("America/New_York")).date()
    except Exception:
        return now.astimezone(timezone.utc).date()


def _parse_day(value: Any) -> Optional[date]:
    """Parse an ISO date or datetime string to an ET calendar date."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if "T" in text:
            cleaned = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            try:
                import zoneinfo
                return parsed.astimezone(zoneinfo.ZoneInfo("America/New_York")).date()
            except Exception:
                return parsed.astimezone(timezone.utc).date()
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


def _canonical_utc_time(value: Any) -> Optional[str]:
    """Return a provider timestamp as canonical UTC, or None when invalid."""
    if not value or "T" not in str(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError):
        return None


def _next_trading_sessions(today: date, count: int) -> list[date]:
    """Return today/next US trading sessions using the shared calendar SoT."""
    from utils.market_calendar import _is_trading_day

    sessions: list[date] = []
    candidate = today
    while len(sessions) < count:
        if _is_trading_day(datetime.combine(candidate, time.min)):
            sessions.append(candidate)
        candidate += timedelta(days=1)
    return sessions


def _fetch_earnings_pages(
    client: Any,
    method_name: str,
    sessions: list[date],
) -> list[dict[str, Any]]:
    """Fetch a bounded page set for each requested earnings session."""
    method = getattr(client, method_name)
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for page in range(EARNINGS_MAX_PAGES):
            payload = _safe_call(
                lambda session=session, page=page: method(
                    date=session.isoformat(),
                    limit=EARNINGS_PAGE_LIMIT,
                    page=page,
                )
            )
            page_rows = _rows_from_payload(payload)
            rows.extend(page_rows)
            if len(page_rows) < EARNINGS_PAGE_LIMIT:
                break
    return rows


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate provider rows returned across adjacent date queries."""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("type"),
            row.get("ticker"),
            row.get("date"),
            row.get("source"),
            row.get("title"),
            row.get("event_time"),
        )
        unique.setdefault(key, row)
    return list(unique.values())


def _normalize_earnings(payload: Any, source: str, today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _rows_from_payload(payload):
        ticker = (raw.get("symbol") or raw.get("ticker") or "").upper() or None
        event_day = _parse_day(raw.get("report_date"))
        if not event_day:
            continue
        rows.append(
            {
                "ticker": ticker,
                "type": TYPE_EARNINGS,
                "title": raw.get("full_name") or ticker or "Earnings",
                "date": event_day.isoformat(),
                "source": source,
                "days_until": _days_until(event_day, today),
                "expected_move_perc": raw.get("expected_move_perc"),
            }
        )
    return rows


def _normalize_fda(payload: Any, today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _rows_from_payload(payload):
        ticker = (raw.get("ticker") or "").upper() or None
        # UW's scheduled decision day is target_date. start/end describe the
        # trial window and can be months old.
        event_day = _parse_day(
            raw.get("target_date") or raw.get("end_date") or raw.get("start_date")
        )
        if not event_day:
            continue
        catalyst = raw.get("catalyst") or "FDA Event"
        drug = raw.get("drug")
        title = f"{catalyst} — {drug}" if drug else catalyst
        rows.append(
            {
                "ticker": ticker,
                "type": TYPE_FDA,
                "title": title,
                "date": event_day.isoformat(),
                "source": TYPE_FDA,
                "days_until": _days_until(event_day, today),
                "indication": raw.get("indication"),
            }
        )
    return rows


def _normalize_economic(payload: Any, today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _rows_from_payload(payload):
        event_day = _parse_day(raw.get("time") or raw.get("date"))
        if not event_day:
            continue
        rows.append(
            {
                "ticker": None,
                "type": TYPE_ECONOMIC,
                "title": raw.get("event") or "Economic Event",
                "date": event_day.isoformat(),
                "event_time": _canonical_utc_time(raw.get("time")),
                "source": TYPE_ECONOMIC,
                "days_until": _days_until(event_day, today),
                "forecast": raw.get("forecast"),
                "prev": raw.get("prev"),
            }
        )
    return rows


def _safe_call(fn) -> Any:
    """Call a UW endpoint, swallowing per-source failures so one dead endpoint
    doesn't take the whole feed down (cf. partial-source-failure tolerance)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — best-effort per source
        print(f"[catalysts] source failed: {exc}", file=sys.stderr)
        return None


def fetch_catalysts(
    client: Any,
    *,
    now: Optional[datetime] = None,
    include_past: bool = False,
) -> list[dict[str, Any]]:
    """Aggregate + normalise all catalyst sources into sorted catalyst rows.

    Past events (``days_until < 0``) are excluded unless ``include_past``.
    Sorted by ``days_until`` ascending so the nearest catalyst is first.
    """
    now = now or datetime.now(timezone.utc)
    today = _et_today(now)
    sessions = _next_trading_sessions(today, EARNINGS_LOOKAHEAD_SESSIONS)
    fda_end = today + timedelta(days=FDA_LOOKAHEAD_DAYS)

    rows: list[dict[str, Any]] = []
    rows += _normalize_earnings(
        {"data": _fetch_earnings_pages(client, "get_earnings_premarket", sessions)},
        "earnings_premarket",
        today,
    )
    rows += _normalize_earnings(
        {"data": _fetch_earnings_pages(client, "get_earnings_afterhours", sessions)},
        "earnings_afterhours",
        today,
    )
    rows += _normalize_fda(
        _safe_call(
            lambda: client.get_fda_calendar(
                target_date_min=today.isoformat(),
                target_date_max=fda_end.isoformat(),
                limit=200,
            )
        ),
        today,
    )
    rows += _normalize_economic(_safe_call(client.get_economic_calendar), today)

    rows = _dedupe_rows(rows)

    if not include_past:
        rows = [r for r in rows if r["days_until"] >= 0]

    rows.sort(key=lambda r: (r["days_until"], r["type"], r["ticker"] or ""))
    return rows


# ── persistence ───────────────────────────────────────────────────

def _write_db_cache(rows: list[dict[str, Any]], scan_time: str) -> None:
    """Persist the catalyst snapshot to Turso. Best-effort: a DB failure must
    not crash the scan since data/catalysts.json is the authoritative fallback."""
    try:
        from db import writer
        from db.client import get_db
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        db = get_db()
        db.execute(_CATALYSTS_TABLE_DDL)
        db.execute(
            "INSERT OR REPLACE INTO catalysts (scan_time, payload) VALUES (?, ?)",
            (scan_time, json.dumps(rows)),
        )
        db.execute(
            """
            DELETE FROM catalysts WHERE scan_time NOT IN (
              SELECT scan_time FROM catalysts ORDER BY scan_time DESC LIMIT 30
            )
            """
        )
        db.commit()
        writer.record_service_health("catalysts", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[catalysts] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    CATALYSTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CATALYSTS_JSON.write_text(json.dumps(payload, indent=2))


def run(
    client: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fetch, normalise, cache, and return the catalyst payload."""
    now = now or datetime.now(timezone.utc)
    if client is None:
        from clients.uw_client import UWClient
        client = UWClient()

    rows = fetch_catalysts(client=client, now=now)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {"scan_time": scan_time, "count": len(rows), "catalysts": rows}

    _write_db_cache(rows, scan_time)
    _write_json_cache(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    print(f"\nCATALYSTS — {payload['count']} upcoming events", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    for row in payload["catalysts"][:25]:
        tag = row["ticker"] or "MKT"
        print(
            f"  +{row['days_until']:>3}d  {row['type']:<9} {tag:<6} {row['title']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Earnings / FDA / economic catalyst feed")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""VIX P/C Indicator - 5-session cumulative VIX put/call ratio.

Descriptive regime read, not a forecast: a low reading means VIX call
volume dominates put volume over the last five sessions (more cautious
tape). A high reading means put volume dominates. See
docs/indicators/vix-pc.md section 0.

Source: Unusual Whales GET /api/stock/VIX/options-volume?limit=500.
IB does not serve put versus call volume. Output is dual-written to
Turso vix_pc_history + data/vix-pc.json.

Usage:
    python3 scripts/fetch_vix_pc.py              # human summary (stderr)
    python3 scripts/fetch_vix_pc.py --json       # JSON to stdout
    python3 scripts/fetch_vix_pc.py --backfill   # same UW limit=500 call
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
for _path in (_PROJECT_DIR, _SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

try:
    from db import writer  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - tests inject a FakeWriter
    writer = None  # type: ignore[assignment]

# ── constants (docs/indicators/vix-pc.md section C.1) ─────────────
WINDOW = 5                 # sessions, inclusive of the current session
MIN_OBSERVATIONS = 5       # == WINDOW. No partial-window cum5, ever.
TICKER = "VIX"
UW_LIMIT = 500
BAND_PCTL_LOW = 10.0       # CAUTIOUS if percentile < 10
BAND_PCTL_HIGH = 90.0      # COMPLACENT if percentile >= 90

VIX_PC_JSON = _PROJECT_DIR / "data" / "vix-pc.json"
SERVICE = "vix-pc"
USER_AGENT = "radon/2.0"
HISTORY_READ_PAGE_ROWS = 2000

STATUS_OK = "ok"
STATUS_STALE_SOURCE = "stale_source"


# ── pure math ─────────────────────────────────────────────────────

def daily_ratio(put: float, call: float) -> Optional[float]:
    """Single-session put/call; None if call <= 0 (never divide)."""
    if call <= 0:
        return None
    return put / call


def cum5_ratio(rows: list[dict[str, Any]]) -> Optional[float]:
    """Sum(put)/sum(call) over the whole list. None if short or call sum <= 0."""
    if len(rows) < MIN_OBSERVATIONS:
        return None
    put_sum = sum(int(row["put_volume"]) for row in rows)
    call_sum = sum(int(row["call_volume"]) for row in rows)
    if call_sum <= 0:
        return None
    return put_sum / call_sum


def percentile_value(values: list[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile: k = (n-1)*p/100, then floor/ceil."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    k = (n - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(ordered[int(lo)])
    frac = k - lo
    lower = float(ordered[int(lo)])
    upper = float(ordered[int(hi)])
    return lower + (upper - lower) * frac


def classify_regime(percentile: Optional[float]) -> Optional[str]:
    """C.4 band for a live percentile of stored history; None if no cum5."""
    if percentile is None:
        return None
    if percentile < BAND_PCTL_LOW:
        return "CAUTIOUS"
    if percentile < BAND_PCTL_HIGH:
        return "NORMAL"
    return "COMPLACENT"


def compute_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending series with daily_ratio and a 5-session cum5 (None first 4)."""
    ordered = sorted(rows, key=lambda row: row["date"])
    series: list[dict[str, Any]] = []
    for i, row in enumerate(ordered):
        put_volume = int(row["put_volume"])
        call_volume = int(row["call_volume"])
        out: dict[str, Any] = {
            "date": row["date"],
            "put_volume": put_volume,
            "call_volume": call_volume,
            "daily_ratio": daily_ratio(put_volume, call_volume),
            "cum5_ratio": None,
            "source": row.get("source") or "uw",
        }
        if i + 1 >= MIN_OBSERVATIONS:
            out["cum5_ratio"] = cum5_ratio(ordered[i - WINDOW + 1 : i + 1])
        series.append(out)
    return series


def parse_uw_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """UW options-volume rows -> [{date, put_volume, call_volume, source: uw}].

    Coerces ints, skips malformed rows, last-wins on duplicate dates, then
    sorts date ascending.
    """
    data = payload.get("data") or []
    by_date: dict[str, dict[str, Any]] = {}
    for entry in data:
        date = entry.get("date") if isinstance(entry, dict) else None
        if not isinstance(date, str) or not date:
            continue
        try:
            put_volume = int(entry["put_volume"])
            call_volume = int(entry["call_volume"])
        except (KeyError, TypeError, ValueError):
            continue
        by_date[date] = {
            "date": date,
            "put_volume": put_volume,
            "call_volume": call_volume,
            "source": "uw",
        }
    return [by_date[date] for date in sorted(by_date)]


def merge_history(
    stored: list[dict[str, Any]], fetched: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Upsert-merge fetched rows over stored history by date. Fetched wins."""
    by_date = {row["date"]: dict(row) for row in stored}
    for row in fetched:
        by_date[row["date"]] = dict(row)
    return [by_date[date] for date in sorted(by_date)]


def build_current(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Newest row plus live percentile, bands, and series extrema of cum5 history H."""
    if not series:
        return None
    cur = series[-1]
    history = [row["cum5_ratio"] for row in series if row["cum5_ratio"] is not None]
    cur_cum5 = cur.get("cum5_ratio")
    if not history or cur_cum5 is None:
        percentile = None
        series_min = series_max = series_p10 = series_p90 = None
        series_min_date = None
        is_series_low = False
    else:
        percentile = sum(1 for value in history if value < cur_cum5) / len(history) * 100.0
        series_min = min(history)
        series_max = max(history)
        series_p10 = percentile_value(history, BAND_PCTL_LOW)
        series_p90 = percentile_value(history, BAND_PCTL_HIGH)
        series_min_date = next(
            row["date"] for row in series if row.get("cum5_ratio") == series_min
        )
        is_series_low = cur_cum5 == series_min
    return {
        "date": cur["date"],
        "put_volume": cur["put_volume"],
        "call_volume": cur["call_volume"],
        "daily_ratio": cur.get("daily_ratio"),
        "cum5_ratio": cur_cum5,
        "percentile": percentile,
        "regime": classify_regime(percentile),
        "series_min": series_min,
        "series_max": series_max,
        "series_p10": series_p10,
        "series_p90": series_p90,
        "series_min_date": series_min_date,
        "is_series_low": is_series_low,
    }


def compute_stats(values: list[float]) -> Optional[dict[str, Any]]:
    """Distribution of all non-null cum5 history, or None when empty."""
    if not values:
        return None
    return {
        "min": min(values),
        "p10": percentile_value(values, 10),
        "p25": percentile_value(values, 25),
        "median": percentile_value(values, 50),
        "p75": percentile_value(values, 75),
        "p90": percentile_value(values, 90),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


# ── fetch ─────────────────────────────────────────────────────────

def _real_uw_fetch() -> dict[str, Any]:
    """UW VIX options-volume JSON (limit=500). Honest UA radon/2.0 via UWClient."""
    from clients.uw_client import UWClient

    with UWClient() as client:
        return client._get(
            f"stock/{TICKER}/options-volume", params={"limit": UW_LIMIT}
        )


# ── persistence ───────────────────────────────────────────────────

def _history_from_json() -> list[dict[str, Any]]:
    """data/vix-pc.json fallback series, ascending."""
    try:
        payload = json.loads(VIX_PC_JSON.read_text())
    except (OSError, ValueError):
        return []
    default_source = payload.get("source") or "uw"
    rows: list[dict[str, Any]] = []
    for row in payload.get("series") or []:
        try:
            rows.append(
                {
                    "date": row["date"],
                    "put_volume": int(row["put_volume"]),
                    "call_volume": int(row["call_volume"]),
                    "source": row.get("source") or default_source,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda row: row["date"])
    return rows


def load_history() -> list[dict[str, Any]]:
    """Turso vix_pc_history first; data/vix-pc.json series fallback when empty.

    Keyset-paginated on date (Hrana I/O bounding).
    """
    try:
        from db.client import get_db

        db = get_db()
        rows: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page = db.execute(
                "SELECT date, put_volume, call_volume, source FROM vix_pc_history "
                "WHERE date > ? ORDER BY date LIMIT ?",
                (cursor, HISTORY_READ_PAGE_ROWS),
            ).fetchall()
            if not page:
                break
            rows.extend(
                {
                    "date": row[0],
                    "put_volume": int(row[1]),
                    "call_volume": int(row[2]),
                    "source": row[3],
                }
                for row in page
            )
            cursor = page[-1][0]
            if len(page) < HISTORY_READ_PAGE_ROWS:
                break
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 - JSON fallback still works
        print(f"[vix-pc] turso history rehydrate non-fatal: {exc}", file=sys.stderr)
    return _history_from_json()


def load_prior_payload() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/vix-pc.json, if present."""
    try:
        return json.loads(VIX_PC_JSON.read_text())
    except (OSError, ValueError):
        return None


def persist_json(payload: dict[str, Any]) -> None:
    """Atomic write of data/vix-pc.json. Refuses an empty series."""
    if not payload.get("series"):
        print("[vix-pc] refusing to persist empty series", file=sys.stderr)
        return
    VIX_PC_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = VIX_PC_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, VIX_PC_JSON)


def _write_db(
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Snapshot + heartbeat every cycle; row upserts only when the source moved."""
    if writer is None:
        return
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_vix_pc_rows(payload["series"], recorded_at=scan_time)
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
        writer.record_service_health(
            SERVICE,
            "ok" if health_error is None else "error",
            finished_at=scan_time,
            error=health_error,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort mirror
        print(f"[vix-pc] db cache non-fatal: {exc}", file=sys.stderr)


# ── payload ───────────────────────────────────────────────────────

def _market_status(now: datetime) -> str:
    try:
        from utils.market_calendar import market_state

        return "open" if market_state(now).get("is_open") else "closed"
    except Exception:  # noqa: BLE001 - daily post-close job defaults closed
        return "closed"


def _expected_session(now: datetime) -> Optional[str]:
    try:
        from utils.market_calendar import last_completed_session_date

        return last_completed_session_date(now)
    except Exception:  # noqa: BLE001 - advisory field only
        return None


def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    status: str,
    source: str,
    now: datetime,
) -> dict[str, Any]:
    """Full API contract for the vix-pc snapshot / JSON fallback (spec E.4)."""
    valid = [row["cum5_ratio"] for row in series if row["cum5_ratio"] is not None]
    as_of = series[-1]["date"] if series else None
    return {
        "scan_time": scan_time,
        "status": status,
        "source": source,
        "as_of": as_of,
        "data_date": as_of,
        "expected_session": _expected_session(now),
        "market_status": _market_status(now),
        "window": WINDOW,
        "count": len(series),
        "cum5_count": len(valid),
        "current": build_current(series),
        "stats": compute_stats(valid),
        "series": series,
    }


# ── orchestration ─────────────────────────────────────────────────

def _rows_changed(stored: list[dict[str, Any]], merged: list[dict[str, Any]]) -> bool:
    """Any date added or any put/call volume changed vs the loaded history."""
    stored_vols = {
        row["date"]: (int(row["put_volume"]), int(row["call_volume"])) for row in stored
    }
    return any(
        stored_vols.get(row["date"]) != (int(row["put_volume"]), int(row["call_volume"]))
        for row in merged
    )


def _serve_cached(scan_time: str, exc: Exception) -> dict[str, Any]:
    """UW down: re-serve the cached payload as stale_source, page. No cache -> raise."""
    cached = load_prior_payload()
    if not cached:
        raise exc
    payload = {**cached, "scan_time": scan_time, "status": STATUS_STALE_SOURCE}
    print(
        f"[vix-pc] UW down; re-serving cached payload through {payload.get('as_of')}",
        file=sys.stderr,
    )
    health_error = {
        "message": (
            f"vix-pc: UW failed ({exc}); "
            f"serving cached payload through {payload.get('as_of')}"
        )
    }
    _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
    persist_json(payload)
    return payload


def run(
    uw_fetch: Optional[Callable[[], dict[str, Any]]] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fetch VIX put/call volume, merge over stored history, dual-write, return.

    Weekend and holiday runs restate the same volumes: rows_changed is False
    and the cycle refreshes the snapshot + heartbeat only.
    """
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        fetcher = uw_fetch if uw_fetch is not None else _real_uw_fetch
        fetched = parse_uw_rows(fetcher())
    except Exception as exc:  # noqa: BLE001 - cached path decides
        print(f"[vix-pc] UW fetch failed: {exc}", file=sys.stderr)
        return _serve_cached(scan_time, exc)

    stored = load_history()
    merged = merge_history(stored, fetched)
    series = compute_series(merged)
    if not series:
        raise RuntimeError("vix-pc: empty series (never cache empty)")
    rows_changed = _rows_changed(stored, merged)
    if not rows_changed:
        print("[vix-pc] source unchanged; refreshing snapshot only", file=sys.stderr)

    payload = build_payload(
        series,
        scan_time=scan_time,
        status=STATUS_OK,
        source="uw",
        now=now,
    )
    print(
        f"[vix-pc] {payload['count']} sessions through {payload['as_of']} "
        f"({payload['cum5_count']} with cum5) via uw",
        file=sys.stderr,
    )
    _write_db(payload, scan_time, rows_changed=rows_changed)
    persist_json(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    cum5 = current.get("cum5_ratio")
    print(
        f"\nVIX P/C - {payload['count']} sessions through {payload.get('as_of')} "
        f"[{payload.get('status')}]",
        file=sys.stderr,
    )
    print(
        f"  cum5      {cum5:.6f}" if cum5 is not None else "  cum5      n/a",
        file=sys.stderr,
    )
    print(f"  percentile {current.get('percentile')}", file=sys.stderr)
    print(f"  regime     {current.get('regime')}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VIX P/C - 5-session cumulative VIX put/call ratio"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Same UW limit=500 call (this feed's full history)",
    )
    args = parser.parse_args()
    if args.backfill:
        print("[vix-pc] --backfill is the same UW limit=500 call", file=sys.stderr)

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

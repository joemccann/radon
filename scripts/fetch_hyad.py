#!/usr/bin/env python3
"""HYAD Indicator - High Yield Bond Cumulative Advance-Decline Line.

FINRA TRACE end-of-day corporate bond market breadth, high-yield bucket.
For each trading day the CORP and CORP_144A bondTypes are combined (HY is
column fieldC on both); net = advances - declines and cum is the running
sum of net from the first stored date (2018-01-22). ma21/ma50 are derived
at payload build, never stored. AGENCY rows reuse the columns for
different agencies and are ignored.

Source (docs/indicators/hyad.md): POST
services-dynarep.ddwa.finra.org .../MarketActivityAggregates with an
honest non-browser UA (FINRA Cloudflare blocks browser UAs - see
scripts/clients/finra_client.py) and a self-minted double-submit CSRF
pair (matching XSRF-TOKEN cookie + X-XSRF-TOKEN header). The response
envelope nests returnBody.data as a JSON STRING requiring a second
json.loads. The SPX overlay is read from Turso
credit_spread_history.spx_close and never refetched here.

Output is dual-written to Turso hyad_history + scan_snapshots and
data/hyad.json.

Usage:
    python3 scripts/fetch_hyad.py             # human summary (stderr)
    python3 scripts/fetch_hyad.py --json      # JSON to stdout
    python3 scripts/fetch_hyad.py --backfill  # one-time 2018-01-22+ build
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# -- path setup ----------------------------------------------------
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

from db import writer

# -- constants -----------------------------------------------------
SERVICE = "hy-ad"
HYAD_JSON = _PROJECT_DIR / "data" / "hyad.json"

FINRA_URL = (
    "https://services-dynarep.ddwa.finra.org/public/reporting/v2/data/group/"
    "FixedIncomeMarket/name/MarketActivityAggregates"
)
# Honest non-browser UA (finra_client precedent): FINRA Cloudflare serves
# plain clients and challenges browser impersonators. Never "fix" a 403
# by pretending to be Chrome.
USER_AGENT = "radon/2.0"
FETCH_TIMEOUT_S = 30

PAGE_LIMIT = 5000
BACKFILL_START = "2018-01-22"
DAILY_WINDOW_DAYS = 10
HISTORY_READ_PAGE_ROWS = 500

HY_BOND_TYPES = ("CORP", "CORP_144A")
COUNT_KEYS = ("advances", "declines", "unchanged", "total")
COUNT_KEY_BY_DESCRIPTION = {
    "Advances": "advances",
    "Declines": "declines",
    "Unchanged": "unchanged",
    "Total Issues Traded": "total",
}
MA_SHORT_WINDOW = 21
MA_LONG_WINDOW = 50

REQUEST_FIELDS = [
    "originalTradeReportedDate",
    "bondType",
    "dataTypeDescription",
    "fieldA",
    "fieldB",
    "fieldC",
    "fieldD",
]


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- FINRA fetch ---------------------------------------------------

def _request_body(bond_type: str, start_date: str, end_date: str, offset: int) -> dict[str, Any]:
    return {
        "fields": REQUEST_FIELDS,
        "limit": PAGE_LIMIT,
        "offset": offset,
        "sortFields": ["+originalTradeReportedDate"],
        "compareFilters": [
            {"fieldName": "bondType", "fieldValue": bond_type, "compareType": "EQUAL"}
        ],
        "dateRangeFilters": [
            {
                "fieldName": "originalTradeReportedDate",
                "startDate": start_date,
                "endDate": end_date,
            }
        ],
    }


def fetch_breadth_page(
    bond_type: str, start_date: str, end_date: str, offset: int = 0
) -> dict[str, Any]:
    """One MarketActivityAggregates page as the raw transport envelope.

    The endpoint requires the double-submit CSRF pair: any self-minted
    UUID works as long as the XSRF-TOKEN cookie and X-XSRF-TOKEN header
    match. GET is unsupported; api.finra.org must not be used.
    """
    from urllib.request import Request, urlopen

    token = str(uuid.uuid4())
    request = Request(
        FINRA_URL,
        data=json.dumps(_request_body(bond_type, start_date, end_date, offset)).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": f"XSRF-TOKEN={token}",
            "X-XSRF-TOKEN": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


# -- pure parsing / computation ------------------------------------

def parse_breadth_rows(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-date-per-bondType HY counts from a raw transport envelope.

    Handles the double-decode quirk (returnBody.data is a JSON string),
    drops AGENCY rows, and extracts the HY column (fieldC) for the four
    count dataTypes. Ascending by (date, bond_type).
    """
    data = envelope["returnBody"]["data"]
    records = json.loads(data) if isinstance(data, str) else data
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for record in records:
        key = COUNT_KEY_BY_DESCRIPTION.get(record.get("dataTypeDescription"))
        if key is None or record.get("bondType") not in HY_BOND_TYPES:
            continue
        slot = counts.setdefault(
            (record["originalTradeReportedDate"], record["bondType"]), {}
        )
        slot[key] = int(record["fieldC"])
    return [
        {"date": day, "bond_type": bond_type, **values}
        for (day, bond_type), values in sorted(counts.items())
    ]


def merge_bond_types(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CORP + CORP_144A HY counts summed per date, ascending by date."""
    by_date: dict[str, dict[str, int]] = {}
    for row in rows:
        merged = by_date.setdefault(row["date"], dict.fromkeys(COUNT_KEYS, 0))
        for key in COUNT_KEYS:
            merged[key] += row.get(key, 0)
    return [{"date": day, **by_date[day]} for day in sorted(by_date)]


def validate_day(row: dict[str, Any]) -> None:
    """Counts must be non-negative and adv+dec+unch must not exceed total.

    Strictly less-or-equal, never equality: securities without a trade in
    the prior 10 business days are excluded from adv/dec/unch but count
    in Total.
    """
    for key in COUNT_KEYS:
        if row[key] < 0:
            raise ValueError(f"{row['date']}: {key} is negative ({row[key]})")
    classified = row["advances"] + row["declines"] + row["unchanged"]
    if classified > row["total"]:
        raise ValueError(
            f"{row['date']}: advances + declines + unchanged ({classified}) "
            f"exceeds total ({row['total']})"
        )


def _trailing_mean(values: list[int], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def build_series(
    rows: list[dict[str, Any]], spx_by_date: dict[str, float]
) -> list[dict[str, Any]]:
    """Ascending net/cum/ma21/ma50 points joined against SPX closes.

    Zero rows is a retryable soft failure, never a payload: raising keeps
    the run from latching service_health ok on a degenerate window.
    """
    if not rows:
        raise RuntimeError("empty HYAD window; refusing to build a degenerate series")
    ordered = sorted(rows, key=lambda row: row["date"])
    series: list[dict[str, Any]] = []
    cums: list[int] = []
    cum = 0
    for row in ordered:
        net = row["advances"] - row["declines"]
        cum += net
        cums.append(cum)
        series.append(
            {
                "date": row["date"],
                "net": net,
                "cum": cum,
                "ma21": _trailing_mean(cums, MA_SHORT_WINDOW),
                "ma50": _trailing_mean(cums, MA_LONG_WINDOW),
                "spx_close": spx_by_date.get(row["date"]),
            }
        )
    return series


def build_output(
    *,
    rows: list[dict[str, Any]],
    spx_by_date: dict[str, float],
    scan_time: str,
) -> dict[str, Any]:
    """Snapshot payload: current merges the last stored counts with the
    last series point's derived net/cum/ma21/ma50."""
    series = build_series(rows, spx_by_date)
    last_row = max(rows, key=lambda row: row["date"])
    last_point = series[-1]
    current = {
        "date": last_point["date"],
        "advances": last_row["advances"],
        "declines": last_row["declines"],
        "unchanged": last_row["unchanged"],
        "total": last_row["total"],
        "net": last_point["net"],
        "cum": last_point["cum"],
        "ma21": last_point["ma21"],
        "ma50": last_point["ma50"],
    }
    return {
        "scan_time": scan_time,
        "data_date": last_point["date"],
        "current": current,
        "series": series,
    }


# -- Turso reads (SPX join + stored history) -----------------------

def _spx_by_date() -> dict[str, float]:
    """Ascending credit_spread_history spx_close map, keyset-paginated
    (Hrana I/O bounding). SPX is never refetched here."""
    from db.client import get_db

    db = get_db()
    cursor = ""
    closes: dict[str, float] = {}
    while True:
        page = db.execute(
            "SELECT date, spx_close FROM credit_spread_history "
            "WHERE spx_close IS NOT NULL AND date > ? ORDER BY date LIMIT ?",
            (cursor, HISTORY_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        closes.update({row[0]: float(row[1]) for row in page})
        cursor = page[-1][0]
        if len(page) < HISTORY_READ_PAGE_ROWS:
            break
    return closes


def _turso_history() -> list[dict[str, Any]]:
    """Ascending hyad_history rows, keyset-paginated (Hrana I/O bounding)."""
    from db.client import get_db

    db = get_db()
    cursor = ""
    rows: list[dict[str, Any]] = []
    while True:
        page = db.execute(
            "SELECT date, advances, declines, unchanged, total "
            "FROM hyad_history WHERE date > ? ORDER BY date LIMIT ?",
            (cursor, HISTORY_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        rows.extend(
            {
                "date": row[0],
                "advances": int(row[1]),
                "declines": int(row[2]),
                "unchanged": int(row[3]),
                "total": int(row[4]),
            }
            for row in page
        )
        cursor = page[-1][0]
        if len(page) < HISTORY_READ_PAGE_ROWS:
            break
    return rows


def merge_series(
    history: list[dict[str, Any]], fresh_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_date = {row["date"]: row for row in history}
    by_date.update({row["date"]: row for row in fresh_rows})
    return [by_date[day] for day in sorted(by_date)]


def _is_unchanged(row: dict[str, Any], stored: Optional[dict[str, Any]]) -> bool:
    if stored is None:
        return False
    return all(row[key] == stored[key] for key in COUNT_KEYS)


# -- persistence ---------------------------------------------------

def _write_json_cache(payload: dict[str, Any]) -> None:
    HYAD_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = HYAD_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, HYAD_JSON)


def persist_result(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.

    ``rows`` is empty on the unchanged-day fast path (weekend / holiday /
    already-stored runs) - the row upsert is skipped but the snapshot AND
    the service-health heartbeat are written EVERY cycle so the staleness
    banner notices a silent writer.
    """
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if rows:
        writer.upsert_hyad_rows(rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    _write_json_cache(payload)


# -- daily orchestration -------------------------------------------

def fetch_window(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """One request per HY bondType over the window, merged per date."""
    rows: list[dict[str, Any]] = []
    for bond_type in HY_BOND_TYPES:
        envelope = fetch_breadth_page(bond_type, start_date, end_date)
        page = parse_breadth_rows(envelope)
        _log(f"window: {bond_type} {start_date}..{end_date}: {len(page)} rows")
        rows.extend(page)
    return merge_bond_types(rows)


def run() -> dict[str, Any]:
    """Rolling last-10-calendar-day window run, self-healing across
    bond-market holidays and late finalization."""
    end = date.today()
    start = end - timedelta(days=DAILY_WINDOW_DAYS)
    window = fetch_window(start.isoformat(), end.isoformat())
    if not window:
        raise RuntimeError(
            f"FINRA returned zero HY rows for {start.isoformat()}..{end.isoformat()}; "
            "retryable soft failure, not persisting"
        )
    for row in window:
        validate_day(row)

    scan_time = _iso_utc_now()
    history = _turso_history()
    stored_by_date = {row["date"]: row for row in history}
    changed = [
        row for row in window if not _is_unchanged(row, stored_by_date.get(row["date"]))
    ]
    if not changed:
        _log("source unchanged; refreshing snapshot only")

    payload = build_output(
        rows=merge_series(history, window),
        spx_by_date=_spx_by_date(),
        scan_time=scan_time,
    )
    persist_result(payload, changed)
    return payload


# -- backfill (run once with --backfill) ---------------------------

def run_backfill() -> int:
    """One-time build from 2018-01-22 through today, one request per
    bondType per calendar year. Offset paging is unusable here: the
    endpoint sorts only by date, intra-date row order is unstable across
    requests, and a page boundary that cuts inside a date duplicates or
    drops rows (observed corrupting 2020-11-24). A year window holds at
    most ~1,800 rows, far under the 5,000-row page cap, and every date
    belongs wholly to exactly one window."""
    today = date.today()
    start = date.fromisoformat(BACKFILL_START)
    raw: list[dict[str, Any]] = []
    for bond_type in HY_BOND_TYPES:
        for year in range(start.year, today.year + 1):
            window_start = max(start, date(year, 1, 1)).isoformat()
            window_end = min(today, date(year, 12, 31)).isoformat()
            envelope = fetch_breadth_page(bond_type, window_start, window_end)
            page = parse_breadth_rows(envelope)
            _log(f"backfill: {bond_type} {year}: {len(page)} rows")
            raw.extend(page)

    rows = merge_bond_types(raw)
    if not rows:
        _log("backfill: refusing to persist zero rows")
        return 1
    for row in rows:
        validate_day(row)

    scan_time = _iso_utc_now()
    writer.ensure_no_replica_for_writers()
    writer.upsert_hyad_rows(rows, recorded_at=scan_time)
    _log(
        f"backfill: upserted {len(rows)} daily rows "
        f"({rows[0]['date']} .. {rows[-1]['date']})"
    )
    return 0


# -- CLI -----------------------------------------------------------

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nHYAD - {len(payload['series'])} rows", file=sys.stderr)
    print(
        f"  {current['date']}: adv {current['advances']} / dec {current['declines']} "
        f"net {current['net']:+d} cum {current['cum']:+d}",
        file=sys.stderr,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="FINRA high yield bond cumulative advance-decline line"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill", action="store_true",
        help="One-time daily build, 2018-01-22..now, then exit",
    )
    args = parser.parse_args(argv)

    if args.backfill:
        return run_backfill()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())

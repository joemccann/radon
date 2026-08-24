#!/usr/bin/env python3
"""HHLEV Indicator - US Household Leverage, percent of net worth.

Federal Reserve Z.1 Financial Accounts, household + nonprofit sector
(B.101 family): leverage_pct = 100 * TLBSHNO / TNWBSHNO, quarterly since
1945Q4. Z.1 revises full history each release, so EVERY run re-upserts
the entire series (one small CSV, chunked Hrana-bounded multi-row
upsert) - there is no unchanged-day fast path and no --backfill flag.

Source (docs/indicators/hhlev.md):
  1. Primary - keyless fredgraph CSV, both series in one request:
     GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=TLBSHNO,TNWBSHNO
     UA quirk (mandatory): fred.stlouisfed.org's edge TCP-resets the bare
     UA "radon/2.0"; the contact-URL form "radon/2.0 (+https://radon.run)"
     is served. Honest bot UA, no browser impersonation.
  2. Fallback - keyed FRED API per series, exact fred_client conventions
     (FRED_API_KEY already in env).

Output is dual-written to Turso hhlev_history + scan_snapshots and
data/hhlev.json.

Usage:
    python3 scripts/fetch_hhlev.py             # human summary (stderr)
    python3 scripts/fetch_hhlev.py --json      # JSON to stdout
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
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
SERVICE = "hhlev"
HHLEV_JSON = _PROJECT_DIR / "data" / "hhlev.json"

LIABILITIES_SERIES = "TLBSHNO"
NET_WORTH_SERIES = "TNWBSHNO"

FREDGRAPH_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    f"?id={LIABILITIES_SERIES},{NET_WORTH_SERIES}"
)
FRED_API_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_SERIES_URL = "https://api.stlouisfed.org/fred/series"

# fred.stlouisfed.org's edge TCP-resets the bare "radon/2.0"; the
# contact-URL form is served (verified 200). Honest bot UA, never a
# browser impersonation.
USER_AGENT = "radon/2.0 (+https://radon.run)"
FETCH_TIMEOUT_S = 30

# Guard band: the fixture holds 304 populated quarters back to 1945Q4;
# fewer than 250 means a truncated download. The latest-quarter ratio
# has lived between 3.79 and 24.26 for 80 years - strictly outside the
# inclusive 2..40 band is a corrupt vintage. Historic rows are exempt:
# Z.1 revisions own history.
MIN_QUARTERS = 250
RATIO_BAND_LOW = 2.0
RATIO_BAND_HIGH = 40.0

# The keyed API's missing sentinel is "."; fredgraph CSVs use an empty
# field. Both mark annual-only early data (1946Q1..1951Q3).
MISSING_SENTINELS = ("", ".")


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- HTTP transport ------------------------------------------------

def _http_get(url: str) -> str:
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return response.read().decode("utf-8-sig", "replace")


def fetch_fredgraph_csv() -> str:
    """Both series in one keyless fredgraph CSV request."""
    return _http_get(FREDGRAPH_CSV_URL)


# -- pure parsing / computation ------------------------------------

def _is_missing(value: Optional[str]) -> bool:
    return value is None or value.strip() in MISSING_SENTINELS


def parse_fredgraph_csv(text: str) -> list[dict[str, Any]]:
    """Populated quarters from the fredgraph CSV, in file order.

    Rows missing either series (empty field or "." sentinel) are
    skipped - both fields are required to form a ratio.
    """
    rows: list[dict[str, Any]] = []
    for record in csv.DictReader(io.StringIO(text)):
        liabilities = record.get(LIABILITIES_SERIES)
        net_worth = record.get(NET_WORTH_SERIES)
        if _is_missing(liabilities) or _is_missing(net_worth):
            continue
        rows.append(
            {
                "date": record["observation_date"],
                "liabilities_musd": float(liabilities),
                "net_worth_musd": float(net_worth),
            }
        )
    return rows


def parse_api_observations(by_series: dict[str, Any]) -> list[dict[str, Any]]:
    """Keyed-API fallback join: one observations block per series id,
    joined on date. "." sentinels and dates missing either series are
    skipped - the same populated-quarter contract as the CSV path.
    """
    net_worth_by_date: dict[str, float] = {}
    for observation in by_series[NET_WORTH_SERIES]["observations"]:
        if _is_missing(observation.get("value")):
            continue
        net_worth_by_date[observation["date"]] = float(observation["value"])

    rows: list[dict[str, Any]] = []
    for observation in by_series[LIABILITIES_SERIES]["observations"]:
        day = observation["date"]
        if _is_missing(observation.get("value")) or day not in net_worth_by_date:
            continue
        rows.append(
            {
                "date": day,
                "liabilities_musd": float(observation["value"]),
                "net_worth_musd": net_worth_by_date[day],
            }
        )
    return rows


def compute_leverage(liabilities: float, net_worth: float) -> float:
    """Household leverage in percent of net worth."""
    return 100.0 * liabilities / net_worth


def _latest_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda row: row["date"])


def ensure_plausible_series(rows: list[dict[str, Any]]) -> None:
    """Refuse a degenerate series - raising keeps the run retryable
    instead of latching service_health ok on garbage. Only the LATEST
    quarter is band-checked: Z.1 revisions own history.
    """
    if len(rows) < MIN_QUARTERS:
        raise RuntimeError(
            f"degenerate series: {len(rows)} quarters < {MIN_QUARTERS}"
        )
    latest = _latest_row(rows)
    if latest["net_worth_musd"] <= 0:
        raise RuntimeError(
            f"degenerate series: {latest['date']} net worth "
            f"{latest['net_worth_musd']} is not positive"
        )
    ratio = compute_leverage(latest["liabilities_musd"], latest["net_worth_musd"])
    if not RATIO_BAND_LOW <= ratio <= RATIO_BAND_HIGH:
        raise RuntimeError(
            f"degenerate series: {latest['date']} leverage {ratio:.2f}% is "
            f"outside the {RATIO_BAND_LOW}..{RATIO_BAND_HIGH} band"
        )


def build_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending leverage-only points - components live in ``current``
    and Turso, never in the series payload."""
    ordered = sorted(rows, key=lambda row: row["date"])
    return [
        {
            "date": row["date"],
            "leverage_pct": round(
                compute_leverage(row["liabilities_musd"], row["net_worth_musd"]), 2
            ),
        }
        for row in ordered
    ]


def build_output(
    *,
    rows: list[dict[str, Any]],
    scan_time: str,
    source_last_modified: Optional[str],
) -> dict[str, Any]:
    """Snapshot payload: latest quarter with components, ascending
    leverage-only series."""
    series = build_series(rows)
    latest = _latest_row(rows)
    current = {
        "date": latest["date"],
        "leverage_pct": round(
            compute_leverage(latest["liabilities_musd"], latest["net_worth_musd"]), 2
        ),
        "liabilities_musd": latest["liabilities_musd"],
        "net_worth_musd": latest["net_worth_musd"],
    }
    return {
        "scan_time": scan_time,
        "source_last_modified": source_last_modified,
        "data_date": latest["date"],
        "current": current,
        "series": series,
    }


# -- keyed FRED API fallback ---------------------------------------

def _fred_api_key() -> str:
    key = os.environ.get("FRED_API_KEY") or os.environ.get("FRED_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set; keyed FRED fallback unavailable")
    return key


def _fetch_api_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    from urllib.parse import urlencode

    return json.loads(_http_get(f"{url}?{urlencode(params)}"))


def _fetch_series_last_updated(series_id: str, api_key: str) -> Optional[str]:
    """FRED series last_updated date, best-effort."""
    try:
        payload = _fetch_api_json(
            FRED_API_SERIES_URL,
            {"series_id": series_id, "api_key": api_key, "file_type": "json"},
        )
        return str(payload["seriess"][0]["last_updated"])[:10]
    except Exception as exc:  # noqa: BLE001 - metadata is advisory
        _log(f"api: series metadata fetch failed: {exc}")
        return None


def fetch_via_api() -> tuple[list[dict[str, Any]], Optional[str]]:
    """Keyed FRED API fallback -> (rows, source_last_modified)."""
    api_key = _fred_api_key()
    by_series = {
        series_id: _fetch_api_json(
            FRED_API_OBSERVATIONS_URL,
            {"series_id": series_id, "api_key": api_key, "file_type": "json"},
        )
        for series_id in (LIABILITIES_SERIES, NET_WORTH_SERIES)
    }
    rows = parse_api_observations(by_series)
    last_updated = _fetch_series_last_updated(LIABILITIES_SERIES, api_key)
    if last_updated is None and rows:
        last_updated = _latest_row(rows)["date"]
    return rows, last_updated


def fetch_rows() -> tuple[list[dict[str, Any]], Optional[str]]:
    """fredgraph CSV first; the keyed API when the CSV transport fails.
    source_last_modified is FRED's series last_updated on the API path
    and the latest observation date on the CSV path."""
    try:
        rows = parse_fredgraph_csv(fetch_fredgraph_csv())
    except Exception as exc:  # noqa: BLE001 - transport failure falls back
        _log(f"fredgraph csv transport failed: {exc}; falling back to keyed FRED API")
        return fetch_via_api()
    source_last_modified = _latest_row(rows)["date"] if rows else None
    return rows, source_last_modified


# -- persistence ---------------------------------------------------

def _write_json_cache(payload: dict[str, Any]) -> None:
    HHLEV_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = HHLEV_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, HHLEV_JSON)


def persist_result(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON
    fallback. Z.1 revises full history each release, so EVERY run
    re-upserts ALL rows - deliberately no unchanged-day fast path.
    """
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    writer.upsert_hhlev_rows(rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    _write_json_cache(payload)


# -- daily orchestration -------------------------------------------

def run() -> dict[str, Any]:
    rows, source_last_modified = fetch_rows()
    _log(f"fetched {len(rows)} populated quarters")
    ensure_plausible_series(rows)

    payload = build_output(
        rows=rows,
        scan_time=_iso_utc_now(),
        source_last_modified=source_last_modified,
    )
    persist_result(payload, rows)
    return payload


# -- CLI -----------------------------------------------------------

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nHHLEV - {len(payload['series'])} quarters", file=sys.stderr)
    print(
        f"  {current['date']}: leverage {current['leverage_pct']:.2f}% "
        f"(liabilities {current['liabilities_musd']:,.0f}M / "
        f"net worth {current['net_worth_musd']:,.0f}M)",
        file=sys.stderr,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="US household leverage (Z.1 liabilities as percent of net worth)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args(argv)

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())

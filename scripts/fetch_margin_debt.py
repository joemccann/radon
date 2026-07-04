#!/usr/bin/env python3
"""Margin Debt Acceleration Indicator — FINRA margin statistics series.

Builds the monthly total-margin-debt series (FINRA debit balances, Jan 1997+,
spliced behind the legacy NYSE member-firm series when
data/margin_debt_legacy.csv exists) plus the transforms the regime chart
needs, computed at ingestion time like vcg_scan / cri_scan:

  - level_yoy_pct    — YoY% growth, calendar-month lookback; NULL for the
                       first 12 months and for the 12 seam months where the
                       lookback crosses the NYSE->FINRA scope change
  - level_display    — pre-1997 rows ratio-adjusted by the Jan-1997 overlap
                       (chart view); DB keeps the raw level + source
  - net_level        — debit balances minus available free credit
  - level_real       — CPI-deflated to latest-month dollars (FRED CPIAUCSL)
  - level_pct_gdp    — % of nominal GDP (FRED GDP, quarterly)

FRED views are best-effort: without FRED_API_KEY (or on fetch failure) they
are null and the payload flags normalization.available = false.

Output is dual-written to Turso margin_debt_history + data/margin_debt.json.

Usage:
    python3 scripts/fetch_margin_debt.py            # human summary (stderr)
    python3 scripts/fetch_margin_debt.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

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

from backfill_margin_debt_legacy import load_legacy_csv

# ── constants ─────────────────────────────────────────────────────
MARGIN_DEBT_JSON = _PROJECT_DIR / "data" / "margin_debt.json"

SOURCE_FINRA = "finra"
SOURCE_NYSE_LEGACY = "nyse_legacy"

# Jan-1997 overlap of the two member-firm scopes: FINRA all-member 103,337 $mm
# vs NYSE member-only 99,460 $mm (both verified 2026-07-03). Pre-1997 display
# values are lifted by this factor so the chart line is continuous; raw levels
# are stored unadjusted with a source tag.
NYSE_TO_FINRA_SPLICE_RATIO = 103337.0 / 99460.0

FRED_CPI_SERIES = "CPIAUCSL"
FRED_GDP_SERIES = "GDP"

_SHEET_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_MONTH_KEY_RE = re.compile(r"^(\d{4})-(\d{2})$")


# ── FINRA XLSX parsing ────────────────────────────────────────────

_XLSX_COLUMNS = {"A": "date", "B": "level", "C": "free_credit_cash", "D": "free_credit_margin"}


def _cell_column(ref: Optional[str]) -> Optional[str]:
    if not ref:
        return None
    letters = "".join(ch for ch in ref if ch.isalpha())
    return letters or None


def _cell_value(cell: ElementTree.Element) -> Optional[str]:
    if cell.get("t") == "inlineStr":
        return cell.findtext("m:is/m:t", None, _SHEET_NS)
    return cell.findtext("m:v", None, _SHEET_NS)


def parse_margin_xlsx(data: bytes) -> list[dict[str, Any]]:
    """Parse the FINRA margin-statistics workbook into ascending month rows.

    The sheet stores strings as inlineStr (no sharedStrings part) and some
    rows omit trailing cells, so cells are mapped by their r= column
    reference. Levels are $ millions, dates already YYYY-MM.
    """
    with zipfile.ZipFile(BytesIO(data)) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    rows: list[dict[str, Any]] = []
    for sheet_row in sheet.findall(".//m:sheetData/m:row", _SHEET_NS):
        raw: dict[str, Optional[str]] = {}
        for cell in sheet_row.findall("m:c", _SHEET_NS):
            name = _XLSX_COLUMNS.get(_cell_column(cell.get("r")) or "")
            if name:
                raw[name] = _cell_value(cell)
        date = raw.get("date")
        if not date or not _MONTH_KEY_RE.match(date):
            continue  # header / malformed row
        try:
            fields: dict[str, Any] = {
                "date": date,
                "level": float(raw["level"]),
                "free_credit_cash": float(raw["free_credit_cash"]) if raw.get("free_credit_cash") else None,
                "free_credit_margin": float(raw["free_credit_margin"]) if raw.get("free_credit_margin") else None,
            }
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(fields)

    rows.sort(key=lambda r: r["date"])
    return rows


# ── merge + transforms ────────────────────────────────────────────

def merge_series(
    legacy_rows: list[dict[str, Any]], finra_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Splice legacy NYSE rows behind FINRA rows; FINRA wins any overlap."""
    by_date: dict[str, dict[str, Any]] = {}
    for row in legacy_rows:
        by_date[row["date"]] = {**row, "source": SOURCE_NYSE_LEGACY}
    for row in finra_rows:
        by_date[row["date"]] = {**row, "source": SOURCE_FINRA}
    return [by_date[d] for d in sorted(by_date)]


def _lookback_month(month_key: str) -> str:
    year, month = _MONTH_KEY_RE.match(month_key).groups()
    return f"{int(year) - 1}-{month}"


def compute_yoy(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach level_yoy_pct = (L_t / L_{t-12} - 1) * 100.

    Calendar-month lookback (a gap 12 months back yields NULL, regardless of
    row count) and NULL when the lookback month has a different source — the
    NYSE->FINRA member-firm scope change makes those 12 ratios meaningless.
    """
    by_date = {row["date"]: row for row in series}
    out: list[dict[str, Any]] = []
    for row in series:
        prior = by_date.get(_lookback_month(row["date"]))
        yoy = None
        if prior and prior.get("source") == row.get("source") and prior["level"]:
            yoy = (row["level"] / prior["level"] - 1.0) * 100.0
        out.append({**row, "level_yoy_pct": yoy})
    return out


def build_display_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach level_display (splice-adjusted) and net_level; raw level untouched."""
    out: list[dict[str, Any]] = []
    for row in series:
        adjust = NYSE_TO_FINRA_SPLICE_RATIO if row.get("source") == SOURCE_NYSE_LEGACY else 1.0
        credits = [row.get("free_credit_cash"), row.get("free_credit_margin")]
        known = [c for c in credits if c is not None]
        out.append(
            {
                **row,
                "level_display": row["level"] * adjust,
                "net_level": row["level"] - sum(known) if known else None,
            }
        )
    return out


def deflate_series(
    series: list[dict[str, Any]], cpi_by_month: dict[str, float], reference_cpi: Optional[float]
) -> list[dict[str, Any]]:
    """Attach level_real = level rebased to reference-month dollars via CPI."""
    out: list[dict[str, Any]] = []
    for row in series:
        cpi = cpi_by_month.get(row["date"])
        real = row["level"] * reference_cpi / cpi if cpi and reference_cpi else None
        out.append({**row, "level_real": real})
    return out


def _quarter_key(month_key: str) -> str:
    year, month = _MONTH_KEY_RE.match(month_key).groups()
    return f"{year}-Q{(int(month) - 1) // 3 + 1}"


def pct_of_gdp_series(
    series: list[dict[str, Any]], gdp_bn_by_quarter: dict[str, float]
) -> list[dict[str, Any]]:
    """Attach level_pct_gdp — level ($mm) over the month's quarterly GDP ($bn)."""
    out: list[dict[str, Any]] = []
    for row in series:
        gdp_bn = gdp_bn_by_quarter.get(_quarter_key(row["date"]))
        pct = (row["level"] / (gdp_bn * 1000.0)) * 100.0 if gdp_bn else None
        out.append({**row, "level_pct_gdp": pct})
    return out


def attach_spx_series(
    series: list[dict[str, Any]], spx_close_by_month: dict[str, float]
) -> list[dict[str, Any]]:
    """Attach spx_close — the S&P 500 monthly close the chart overlays."""
    return [{**row, "spx_close": spx_close_by_month.get(row["date"])} for row in series]


SPX_LEGACY_CSV = _PROJECT_DIR / "data" / "spx_monthly_legacy.csv"


def _load_spx_legacy_csv() -> dict[str, float]:
    """1959-1984 S&P 500 monthly levels (Shiller composite, monthly average).

    Yahoo's chart API now caps ^GSPC history at ~1985, so the pre-1985 stretch
    is a committed one-time extract of Robert Shiller's monthly composite —
    those values are historical constants. Note the methodology difference:
    Shiller's monthly figure is the average of daily closes, not month-end;
    at log scale over 65 years the distinction is invisible.
    """
    import csv

    if not SPX_LEGACY_CSV.exists():
        return {}
    with SPX_LEGACY_CSV.open() as fh:
        return {r["date"]: float(r["spx_close"]) for r in csv.DictReader(fh)}


def _fetch_spx_monthly_closes() -> dict[str, float]:
    """Monthly ^GSPC closes from Yahoo (1959+ needed; series runs from 1927).

    Yahoo is the sanctioned source here: IB and UW carry no multi-decade
    monthly index history, so the priority chain falls through. Best-effort —
    an empty dict leaves spx_close null and the chart hides the overlay.
    """
    from urllib.request import Request, urlopen

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
        f"?period1=0&period2={int(datetime.now(timezone.utc).timestamp())}&interval=1mo"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as exc:  # noqa: BLE001 — overlay is optional
        print(f"[margin-debt] SPX fetch failed (non-fatal): {exc}", file=sys.stderr)
        return {}

    by_month: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        by_month[f"{stamp.year}-{stamp.month:02d}"] = float(close)
    return by_month


# ── FRED normalization inputs (best-effort) ───────────────────────

def _fetch_fred_observations(series_id: str, api_key: str) -> dict[str, float]:
    import requests

    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": api_key, "file_type": "json"},
        timeout=30,
    )
    resp.raise_for_status()
    observations = resp.json().get("observations", [])
    values: dict[str, float] = {}
    for obs in observations:
        try:
            values[obs["date"][:7]] = float(obs["value"])
        except (KeyError, ValueError, TypeError):
            continue  # FRED encodes missing values as "."
    return values


def _load_normalization_inputs() -> tuple[dict[str, float], dict[str, float], bool]:
    """(cpi_by_month, gdp_bn_by_quarter, available). Never raises."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("[margin-debt] FRED_API_KEY not set; normalization views null", file=sys.stderr)
        return {}, {}, False
    try:
        cpi = _fetch_fred_observations(FRED_CPI_SERIES, api_key)
        gdp_monthly = _fetch_fred_observations(FRED_GDP_SERIES, api_key)
        gdp_by_quarter = {_quarter_key(month): value for month, value in gdp_monthly.items()}
        return cpi, gdp_by_quarter, bool(cpi and gdp_by_quarter)
    except Exception as exc:  # noqa: BLE001 — normalization is optional
        print(f"[margin-debt] FRED fetch failed (non-fatal): {exc}", file=sys.stderr)
        return {}, {}, False


# ── persistence ───────────────────────────────────────────────────

def _write_db_cache(
    payload: dict[str, Any], scan_time: str, *, rows_changed: bool
) -> None:
    """Best-effort Turso mirror; data/margin_debt.json is the disk fallback.

    Two writes: the durable per-month margin_debt_history rows (skipped when
    the FINRA file is unchanged — 809 upserts over HTTP are the expensive
    part) and the full payload snapshot the Next.js route dbFirstReads. The
    snapshot + heartbeat run EVERY cycle so the staleness banner notices a
    silent writer (cf. feedback_service_health_heartbeat).
    """
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_margin_debt_rows(payload["series"], recorded_at=scan_time)
        writer.upsert_scan_snapshot("margin-debt", scan_time, payload)
        writer.record_service_health("margin-debt", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[margin-debt] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    MARGIN_DEBT_JSON.parent.mkdir(parents=True, exist_ok=True)
    MARGIN_DEBT_JSON.write_text(json.dumps(payload, indent=2))


# ── orchestration ─────────────────────────────────────────────────

def build_series(finra_xlsx: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Full pipeline: parse, splice, YoY, display, normalization views."""
    finra_rows = parse_margin_xlsx(finra_xlsx)
    if not finra_rows:
        raise ValueError("FINRA workbook parsed to zero rows")
    legacy_rows = load_legacy_csv()

    series = compute_yoy(merge_series(legacy_rows, finra_rows))
    series = build_display_series(series)

    cpi, gdp, available = _load_normalization_inputs()
    latest_cpi = cpi.get(series[-1]["date"]) or (cpi[max(cpi)] if cpi else None)
    series = deflate_series(series, cpi, latest_cpi)
    series = pct_of_gdp_series(series, gdp)
    # Live Yahoo months win any overlap with the static pre-1985 extract.
    series = attach_spx_series(series, {**_load_spx_legacy_csv(), **_fetch_spx_monthly_closes()})
    return series, available


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(MARGIN_DEBT_JSON.read_text())
    except (OSError, ValueError):
        return None


def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch, transform, cache, and return the margin-debt payload.

    Runs daily from a timer against a monthly-updated file: the cached
    Last-Modified rides an If-Modified-Since so unchanged months skip the
    FINRA parse, FRED calls, and the 800+ Turso row upserts — only the
    snapshot + heartbeat refresh.
    """
    now = now or datetime.now(timezone.utc)
    if client is None:
        from clients.finra_client import FinraClient
        client = FinraClient()

    cached = _read_json_cache()
    cached_stamp = cached.get("source_last_modified") if cached else None
    xlsx, last_modified = client.fetch_margin_statistics(if_modified_since=cached_stamp)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    if xlsx is None and cached:
        print("[margin-debt] source unchanged (304); refreshing snapshot only", file=sys.stderr)
        payload = {**cached, "scan_time": scan_time}
        _write_db_cache(payload, scan_time, rows_changed=False)
        _write_json_cache(payload)
        return payload

    series, normalization_available = build_series(xlsx)
    latest = series[-1]
    payload = {
        "scan_time": scan_time,
        "source_last_modified": last_modified,
        "count": len(series),
        "splice": {
            "legacy_source": SOURCE_NYSE_LEGACY,
            "ratio": NYSE_TO_FINRA_SPLICE_RATIO,
            "first_finra_month": next(
                (r["date"] for r in series if r["source"] == SOURCE_FINRA), None
            ),
        },
        "normalization": {"available": normalization_available},
        "current": {
            "date": latest["date"],
            "level": latest["level"],
            "level_yoy_pct": latest["level_yoy_pct"],
        },
        "series": series,
    }

    _write_db_cache(payload, scan_time, rows_changed=True)
    _write_json_cache(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    yoy = current["level_yoy_pct"]
    print(f"\nMARGIN DEBT — {payload['count']} months through {current['date']}", file=sys.stderr)
    print(f"  level  ${current['level'] / 1000.0:,.1f}bn", file=sys.stderr)
    print(f"  YoY    {yoy:+.1f}%" if yoy is not None else "  YoY    n/a", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="FINRA margin debt series + acceleration transform")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

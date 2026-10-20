#!/usr/bin/env python3
"""Latest-revised official total exports; six-month alignment is not a forecast.

IB/Robinhood OHLC and UW economic calendars do not supply sovereign customs
totals. MOF/KCS specialized official feeds are the documented source for these
series. No unofficial fallback or semiconductor substitution is used.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from clients.asia_export_client import AsiaExportClient, TAIWAN_URL, KOREA_URL
from utils.atomic_io import atomic_save, verified_load

SERVICE = "asia-export-lead"
ASIA_EXPORT_LEAD_JSON = SCRIPT_DIR.parent / "data/asia_export_lead.json"
COMPARISON = {"available": False, "label": "MSCI US forward 12-month EPS",
              "reason": "Licensed EPS series not connected"}
NUMERIC_FIELDS = ("taiwan_exports_usd_mn", "korea_exports_usd_mn", "taiwan_yoy_pct",
                  "korea_yoy_pct", "mean_yoy_pct", "ma3_yoy_pct", "ma3_change_pp")
# Verified coverage of the selected full-history official endpoints, not a
# frozen observation count. Publication dates do not supply an end-of-file marker.
SOURCE_HISTORY_START = {"taiwan": "2001-01", "korea": "2000-01"}


def _month(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError(f"Invalid export month: {value!r}")
    datetime.strptime(value, "%Y-%m")
    return value


def shift_month(value, offset):
    year, month = map(int, _month(value).split("-"))
    year, month = divmod(year * 12 + month - 1 + offset, 12)
    return _month(f"{year:04d}-{month + 1:02d}")


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean export value")
    number = float(str(value).strip().replace(",", ""))
    if not math.isfinite(number) or number < 0:
        raise ValueError("Exports must be finite and nonnegative")
    return number


def _normalize(rows):
    values = {}
    for row in rows:
        date, value = _month(row["date"]), _number(row["exports_usd_mn"])
        if date in values and values[date] != value:
            raise ValueError(f"Conflicting duplicate export month {date}")
        values[date] = value
    if not values:
        raise ValueError("No monthly export observations")
    return [{"date": date, "exports_usd_mn": values[date]} for date in sorted(values)]


def parse_taiwan_csv(raw: bytes):
    reader = csv.reader(raw.decode("utf-8-sig").splitlines())
    header = next(reader, [])
    try:
        index = [re.sub(r"\s", "", name) for name in header].index("按美元計算/出口總值")
    except ValueError as exc:
        raise ValueError("Taiwan USD export header missing") from exc
    rows = []
    for row in reader:
        if not row:
            continue
        match = re.fullmatch(r"(\d+)年\s*(\d+)月", row[0].strip())
        if match:
            if len(row) <= index:
                raise ValueError("Truncated Taiwan monthly row")
            rows.append({"date": f"{int(match[1]) + 1911:04d}-{int(match[2]):02d}",
                         "exports_usd_mn": _number(row[index])})
    return _normalize(rows)


def parse_korea_json(raw: bytes):
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Korea monthly response is not a data object")
    items = data["items"]
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(items):
        raise ValueError("Korea response is truncated or missing its count")
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Korea monthly row is not an object")
        period = item.get("priodTitle")
        if period == "TOTAL":
            continue
        if not isinstance(period, str) or not re.fullmatch(r"\d{4}\.(0[1-9]|1[0-2])", period):
            raise ValueError("Korea row is not a full calendar month")
        rows.append({"date": period.replace(".", "-"),
                     "exports_usd_mn": _number(item.get("expUsdAmt")) / 1000})
    return _normalize(rows)


def compute_series(taiwan_rows, korea_rows):
    tw = {row["date"]: row["exports_usd_mn"] for row in _normalize(taiwan_rows)}
    kr = {row["date"]: row["exports_usd_mn"] for row in _normalize(korea_rows)}
    by_date = {}
    for date in sorted(tw.keys() | kr.keys()):
        row = {"date": date, "lead_date": shift_month(date, 6),
               **dict.fromkeys(NUMERIC_FIELDS)}
        prior = shift_month(date, -12)
        for country, values in (("taiwan", tw), ("korea", kr)):
            row[f"{country}_exports_usd_mn"] = values.get(date)
            if date in values and values.get(prior, 0) > 0:
                growth = 100 * (values[date] / values[prior] - 1)
                row[f"{country}_yoy_pct"] = growth if math.isfinite(growth) else None
        rates = [row["taiwan_yoy_pct"], row["korea_yoy_pct"]]
        if all(value is not None for value in rates):
            row["mean_yoy_pct"] = sum(rates) / 2
        by_date[date] = row
        window = [by_date.get(shift_month(date, -offset), {}).get("mean_yoy_pct") for offset in range(3)]
        if all(value is not None for value in window):
            row["ma3_yoy_pct"] = sum(window) / 3
            previous = by_date.get(shift_month(date, -1), {}).get("ma3_yoy_pct")
            if previous is not None:
                row["ma3_change_pp"] = row["ma3_yoy_pct"] - previous
    return list(by_date.values())


def _state(value, positive, negative, zero):
    return "unavailable" if value is None else positive if value > 0 else negative if value < 0 else zero


def build_output(taiwan_rows, korea_rows, *, scan_time=None, source_metadata=None):
    stamp = scan_time or datetime.now(timezone.utc).isoformat()
    when = datetime.fromisoformat(stamp)
    if when.utcoffset() is None:
        raise ValueError("scan_time must be timezone aware")
    stamp = when.astimezone(timezone.utc).isoformat()
    series = compute_series(taiwan_rows, korea_rows)
    common = [r for r in series if r["taiwan_exports_usd_mn"] is not None and r["korea_exports_usd_mn"] is not None]
    if not common:
        raise ValueError("No common full export month")
    current = dict(common[-1])
    current["growth"] = _state(current["ma3_yoy_pct"], "expanding", "contracting", "flat")
    current["momentum"] = _state(current["ma3_change_pp"], "accelerating", "slowing", "unchanged")
    year, month = map(int, current["date"].split("-"))
    metadata = source_metadata or {}
    sources = {}
    for country, rows, name, url, licence_url in (
        ("taiwan", taiwan_rows, "Taiwan Ministry of Finance", TAIWAN_URL, "https://data.gov.tw/license"),
        ("korea", korea_rows, "Korea Customs Service", KOREA_URL,
         "https://www.customs.go.kr/kcs/cm/cntnts/cntntsView.do?cntntsId=2611&mi=7422"),
    ):
        sources[country] = {"name": name, "url": url, "licence_url": licence_url,
                            "latest_month": max(r["date"] for r in rows),
                            "last_modified": metadata.get(country, {}).get("last_modified"),
                            "sampled_at": stamp}
    return {"missing": False, "scan_time": stamp,
            "source_last_modified": sources["taiwan"]["last_modified"],
            "data_date": f"{current['date']}-{calendar.monthrange(year, month)[1]:02d}",
            "count": len(series), "series": series, "current": current,
            "sources": sources, "comparison": dict(COMPARISON)}


def _validate_payload(payload):
    if not isinstance(payload, dict) or payload.get("missing") is not False:
        raise ValueError("Refusing missing export snapshot")
    rows = payload.get("series")
    if not isinstance(rows, list) or not rows or payload.get("count") != len(rows):
        raise ValueError("Refusing empty or inconsistent export snapshot")
    tw, kr = _cached_levels(payload, "taiwan"), _cached_levels(payload, "korea")
    expected = build_output(tw, kr, scan_time=payload["scan_time"], source_metadata=payload.get("sources"))
    if any(payload.get(key) != expected[key] for key in ("series", "current", "data_date", "comparison")):
        raise ValueError("Export snapshot does not match its source levels")
    json.dumps(payload, allow_nan=False)


def _cached_levels(previous, country):
    return _normalize([{"date": r["date"], "exports_usd_mn": r[f"{country}_exports_usd_mn"]}
                       for r in (previous or {}).get("series", [])
                       if r.get(f"{country}_exports_usd_mn") is not None])


def _check_source_coverage(country, rows, previous_rows=None):
    dates = {row["date"] for row in rows}
    if previous_rows is not None:
        missing = {row["date"] for row in previous_rows} - dates
        if missing:
            raise ValueError(f"{country} export response lost previously available month {min(missing)}")
        return
    # A first sample must contain the verified history floor and every calendar
    # month through its own latest observation. Its tail cannot be authenticated
    # from these payloads: a delayed release and a valid tail cut look identical.
    month, latest = SOURCE_HISTORY_START[country], max(dates)
    if month not in dates:
        raise ValueError(f"{country} export response is missing its verified history start {month}")
    while month <= latest:
        if month not in dates:
            raise ValueError(f"{country} export response is missing calendar month {month}")
        month = shift_month(month, 1)


def _write_db_cache(payload, scan_time, *, rows_changed):
    from db import writer
    writer.ensure_no_replica_for_writers()
    if rows_changed:
        writer.upsert_asia_export_lead_rows(payload["series"], recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(SERVICE, "ok", finished_at=scan_time)


def persist_result(payload, *, previous=None, path=None):
    _validate_payload(payload)
    if previous is not None:
        _validate_payload(previous)
        for country in SOURCE_HISTORY_START:
            _check_source_coverage(country, _cached_levels(payload, country), _cached_levels(previous, country))
    rows_changed = previous is None or previous.get("series") != payload["series"]
    _write_db_cache(payload, payload["scan_time"], rows_changed=rows_changed)
    atomic_save(str(path or ASIA_EXPORT_LEAD_JSON), payload)


def refresh(client, *, previous=None, now=None, path=None):
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    if previous is not None:
        _validate_payload(previous)
    prior_modified = (previous or {}).get("sources", {}).get("taiwan", {}).get("last_modified")
    raw, modified = client.fetch_taiwan(if_modified_since=prior_modified)
    taiwan = _cached_levels(previous, "taiwan") if raw is None else parse_taiwan_csv(raw)
    local_month = now.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m")
    end_month = shift_month(local_month, -1)
    korea = parse_korea_json(client.fetch_korea(end_month=end_month.replace("-", "")))
    if any(r["date"] > end_month for r in taiwan + korea):
        raise ValueError("Official exports include an incomplete or future calendar month")
    if previous is None:
        _check_source_coverage("taiwan", taiwan)
        _check_source_coverage("korea", korea)
    result = build_output(taiwan, korea, scan_time=now.isoformat(),
                          source_metadata={"taiwan": {"last_modified": modified}})
    persist_result(result, previous=previous, path=path)
    return result


def _record_failure(exc):
    """An error heartbeat is best effort; failure must still exit nonzero."""
    try:
        from db import writer
        writer.record_service_health(
            SERVICE, "error", finished_at=datetime.now(timezone.utc).isoformat(),
            error={"class": "asia_export_lead_failed", "message": str(exc)},
        )
    except Exception as health_exc:
        print(f"[{SERVICE}] error heartbeat failed: {health_exc}", file=sys.stderr)


def _read_previous(path=None):
    from db.hrana_http import hrana_query
    try:
        rows = hrana_query("SELECT payload FROM scan_snapshots WHERE service = ? ORDER BY scan_time DESC LIMIT 1", (SERVICE,))
        if rows:
            payload = json.loads(rows[0][0])
            _validate_payload(payload)
            return payload
    except Exception as exc:
        print(f"[{SERVICE}] previous DB snapshot unavailable: {exc}", file=sys.stderr)
    try:
        payload = verified_load(str(path or ASIA_EXPORT_LEAD_JSON))
        _validate_payload(payload)
        return payload
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        previous = _read_previous()
        with AsiaExportClient() as client:
            result = refresh(client, previous=previous)
    except Exception as exc:
        print(f"[{SERVICE}] {exc}", file=sys.stderr)
        _record_failure(exc)
        return 1
    if args.json:
        print(json.dumps(result, allow_nan=False))
    else:
        print(f"EXPORT LEAD: {result['data_date']}; Data Freshness: sampled {result['scan_time']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

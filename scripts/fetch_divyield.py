#!/usr/bin/env python3
"""DIVYIELD Indicator — percent of S&P 500 stocks with trailing dividend
yield above the 10-Year Treasury yield.

For each current constituent, trailing yield = TTM cash dividends (specials
included) / last regular-market price. The indicator is the share of
constituents whose yield is STRICTLY above the 10Y CMT par yield already
maintained in Turso yield_curve_history (never refetched here).

Sources, in order (docs/indicators/divyield.md):
  1. Constituents — github datasets constituents.csv (public domain),
     falling back to the Wikipedia parse API, then to the existing
     scripts/clients/index_constituents.py cache/seed chain (never fails).
  2. Dividends + price — Yahoo v8 chart per ticker (sanctioned bulk
     deviation per the bpi_scan precedent: IB has no bulk dividend API and
     UW has no dividend endpoint). Aggregate display only.
  3. 10Y — Turso yield_curve_history.y10 (treasury.gov via radon-yield-curve).

Output is dual-written to Turso divyield_history + scan_snapshots and
data/divyield.json.

Usage:
    python3 scripts/fetch_divyield.py             # human summary (stderr)
    python3 scripts/fetch_divyield.py --json      # JSON to stdout
    python3 scripts/fetch_divyield.py --backfill  # monthly 1990+ approximate build
"""
from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import os
import re
import sys
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

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

from clients.index_constituents import (
    MIN_PLAUSIBLE_COUNTS,
    normalize_ticker,
    resolve_constituents,
)
from db import writer
from utils.ipv4_first import prefer_ipv4

prefer_ipv4()

# ── constants ─────────────────────────────────────────────────────
SERVICE = "div-yield"
DIVYIELD_JSON = _PROJECT_DIR / "data" / "divyield.json"

MIN_CONSTITUENTS = MIN_PLAUSIBLE_COUNTS["SPX"]
MIN_VALID_FRACTION = 0.8
LEADER_COUNT = 10
FETCH_WORKERS = 6
FETCH_TIMEOUT_S = 30
HISTORY_READ_PAGE_ROWS = 500

GITHUB_CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)
WIKIPEDIA_CONSTITUENTS_URL = (
    "https://en.wikipedia.org/w/api.php?action=parse&page=List_of_S%26P_500_companies"
    "&prop=wikitext&section=1&format=json&formatversion=2"
)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# Honest non-browser UA for GitHub/Wikipedia (index_constituents precedent);
# browser UA for Yahoo (fetch_credit_spread / bpi_scan precedent — the plain
# UA gets 429).
PLAIN_UA = "radon/2.0"
BROWSER_UA = "Mozilla/5.0"

BACKFILL_PERIOD1 = 631152000  # 1990-01-01T00:00:00Z

_ET = ZoneInfo("America/New_York")
_WIKI_SYMBOL_RE = re.compile(r"\{\{[A-Za-z]*[Ss]ymbol\|([A-Z][A-Z0-9.\-]*)\}\}")


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── constituents (3-tier fallback) ────────────────────────────────

def _http_get(url: str, user_agent: str) -> str:
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8-sig", "replace")


def parse_constituents_csv(text: str) -> list[str]:
    """Tickers from the github datasets constituents CSV (Symbol column),
    in file order, normalized to canonical DASH form ("BRK-B")."""
    tickers: list[str] = []
    for record in csv.DictReader(io.StringIO(text)):
        ticker = normalize_ticker(record.get("Symbol"))
        if ticker:
            tickers.append(ticker)
    return tickers


def parse_wikipedia_wikitext(wikitext: str) -> list[str]:
    """Tickers from the {{NyseSymbol|X}}-family templates in the Wikipedia
    constituents table wikitext, deduplicated in appearance order."""
    seen: dict[str, None] = {}
    for raw in _WIKI_SYMBOL_RE.findall(wikitext):
        ticker = normalize_ticker(raw)
        if ticker:
            seen.setdefault(ticker, None)
    return list(seen)


def _fetch_github_constituents() -> list[str]:
    return parse_constituents_csv(_http_get(GITHUB_CONSTITUENTS_URL, PLAIN_UA))


def _fetch_wikipedia_constituents() -> list[str]:
    data = json.loads(_http_get(WIKIPEDIA_CONSTITUENTS_URL, PLAIN_UA))
    return parse_wikipedia_wikitext(data["parse"]["wikitext"])


def fetch_constituents() -> tuple[list[str], str]:
    """Current S&P 500 tickers plus a source label. Never fails: github ->
    Wikipedia -> the index_constituents cache/seed chain."""
    tiers = (
        ("github-datasets", _fetch_github_constituents),
        ("wikipedia", _fetch_wikipedia_constituents),
    )
    for label, fetch in tiers:
        try:
            tickers = fetch()
        except Exception as exc:  # noqa: BLE001 — every failure falls back
            _log(f"constituents: {label} fetch failed: {exc}")
            continue
        if len(tickers) >= MIN_CONSTITUENTS:
            return tickers, label
        _log(
            f"constituents: {label} implausible "
            f"({len(tickers)} tickers < {MIN_CONSTITUENTS}); falling back"
        )
    tickers, source = resolve_constituents("SPX")
    return tickers, f"index-constituents-{source}"


# ── Yahoo quotes ──────────────────────────────────────────────────

def fetch_ticker_chart(ticker: str) -> dict[str, Any]:
    """Yahoo v8 chart with TTM dividend events for one ticker."""
    url = f"{YAHOO_CHART_URL.format(ticker=ticker)}?range=1y&interval=1d&events=div"
    return json.loads(_http_get(url, BROWSER_UA))


def _chart_result(chart: dict[str, Any]) -> dict[str, Any]:
    return chart["chart"]["result"][0]


def compute_trailing_yield(chart: dict[str, Any]) -> Optional[float]:
    """Trailing 12M dividend yield in percent; 0.0 for a payer of nothing,
    None when the quote carries no usable price."""
    result = _chart_result(chart)
    price = result.get("meta", {}).get("regularMarketPrice")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    dividends = (result.get("events") or {}).get("dividends") or {}
    ttm = sum(event.get("amount", 0.0) for event in dividends.values())
    return 100.0 * ttm / float(price)


def _chart_session_date(chart: dict[str, Any]) -> Optional[str]:
    """Last trading date from the quote's regularMarketTime, in ET."""
    stamp = _chart_result(chart).get("meta", {}).get("regularMarketTime")
    if not isinstance(stamp, (int, float)):
        return None
    return datetime.fromtimestamp(stamp, tz=_ET).strftime("%Y-%m-%d")


def sweep_yields(tickers: list[str]) -> tuple[dict[str, float], int, Optional[str]]:
    """Parallel Yahoo sweep -> (yields_by_ticker, quote_errors, data_date)."""
    yields: dict[str, float] = {}
    errors = 0
    data_date: Optional[str] = None

    def fetch_one(ticker: str) -> tuple[str, Optional[dict[str, Any]]]:
        try:
            return ticker, fetch_ticker_chart(ticker)
        except Exception as exc:  # noqa: BLE001 — counted against the guard
            _log(f"quote: {ticker} failed: {exc}")
            return ticker, None

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for done, (ticker, chart) in enumerate(pool.map(fetch_one, tickers), 1):
            if done % 100 == 0:
                _log(f"quote sweep {done}/{len(tickers)}")
            trailing = compute_trailing_yield(chart) if chart is not None else None
            if trailing is None:
                errors += 1
                continue
            yields[ticker] = trailing
            session = _chart_session_date(chart)
            if session and (data_date is None or session > data_date):
                data_date = session
    return yields, errors, data_date


# ── pure computation ──────────────────────────────────────────────

def compute_pct_above(yields: list[float], y10: float) -> dict[str, Any]:
    """Share of yields STRICTLY above y10 — the boundary does not count."""
    count_above = sum(1 for value in yields if value > y10)
    total = len(yields)
    pct_above = 100.0 * count_above / total if total else 0.0
    return {"pct_above": pct_above, "count_above": count_above, "total": total}


def ensure_plausible_payload(constituents_count: int, valid_count: int) -> None:
    """Refuse a degenerate sweep — raising keeps the run retryable instead of
    latching service_health ok on garbage (feedback_dont_latch_last_run)."""
    if constituents_count < MIN_CONSTITUENTS:
        raise RuntimeError(
            f"degenerate payload: {constituents_count} constituents < {MIN_CONSTITUENTS}"
        )
    if valid_count < MIN_VALID_FRACTION * constituents_count:
        raise RuntimeError(
            f"degenerate payload: {valid_count}/{constituents_count} valid yields "
            f"is below the {MIN_VALID_FRACTION:.0%} floor"
        )


def is_unchanged_day(new_row: dict[str, Any], latest_row: Optional[dict[str, Any]]) -> bool:
    """Unchanged-day fast path key: same (date, count_above, total) as the
    latest stored row. y10 revisions alone do not force a row upsert."""
    if not latest_row:
        return False
    return all(
        new_row.get(key) == latest_row.get(key)
        for key in ("date", "count_above", "total")
    )


def _leaders(yields_by_ticker: dict[str, float]) -> list[dict[str, Any]]:
    ranked = sorted(yields_by_ticker.items(), key=lambda item: item[1], reverse=True)
    return [
        {"ticker": ticker, "yield_pct": trailing}
        for ticker, trailing in ranked[:LEADER_COUNT]
    ]


def build_output(
    yields_by_ticker: dict[str, float],
    y10: float,
    y10_date: str,
    data_date: str,
    scan_time: str,
    source: dict[str, Any],
    series: list[dict[str, Any]],
    backfill_cutover: Optional[str],
) -> dict[str, Any]:
    current = {
        "date": data_date,
        **compute_pct_above(list(yields_by_ticker.values()), y10),
        "y10": y10,
        "leaders": _leaders(yields_by_ticker),
    }
    return {
        "scan_time": scan_time,
        "source": source,
        "data_date": data_date,
        "y10_date": y10_date,
        "current": current,
        "series": series,
        "backfill_cutover": backfill_cutover,
    }


# ── Turso reads (10Y + history) ───────────────────────────────────

def _latest_y10() -> tuple[str, float]:
    """Newest (date, y10) from yield_curve_history — the 10Y is never
    refetched here (radon-yield-curve owns it)."""
    from db.client import get_db

    row = get_db().execute(
        "SELECT date, y10 FROM yield_curve_history "
        "WHERE y10 IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("yield_curve_history has no y10 row; run fetch_yield_curve first")
    return row[0], float(row[1])


def _y10_series() -> list[tuple[str, float]]:
    """Full ascending (date, y10) series, keyset-paginated (Hrana I/O bounding)."""
    from db.client import get_db

    db = get_db()
    cursor = ""
    rows: list[tuple[str, float]] = []
    while True:
        page = db.execute(
            "SELECT date, y10 FROM yield_curve_history "
            "WHERE y10 IS NOT NULL AND date > ? ORDER BY date LIMIT ?",
            (cursor, HISTORY_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        rows.extend((row[0], float(row[1])) for row in page)
        cursor = page[-1][0]
        if len(page) < HISTORY_READ_PAGE_ROWS:
            break
    return rows


def _history_row(row: Any) -> dict[str, Any]:
    return {
        "date": row[0],
        "pct_above": float(row[1]),
        "count_above": int(row[2]),
        "total": int(row[3]),
        "y10": float(row[4]),
        "approximate": int(row[5]),
    }


def _turso_history() -> list[dict[str, Any]]:
    """Ascending divyield_history rows, keyset-paginated (Hrana I/O bounding)."""
    from db.client import get_db

    db = get_db()
    cursor = ""
    rows: list[dict[str, Any]] = []
    while True:
        page = db.execute(
            "SELECT date, pct_above, count_above, total, y10, approximate "
            "FROM divyield_history WHERE date > ? ORDER BY date LIMIT ?",
            (cursor, HISTORY_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        rows.extend(_history_row(row) for row in page)
        cursor = page[-1][0]
        if len(page) < HISTORY_READ_PAGE_ROWS:
            break
    return rows


def _json_history() -> list[dict[str, Any]]:
    try:
        cached = json.loads(DIVYIELD_JSON.read_text())
    except (OSError, ValueError):
        return []
    series = cached.get("series") if isinstance(cached, dict) else None
    return series if isinstance(series, list) else []


def load_history() -> list[dict[str, Any]]:
    """Turso is the source of truth; data/divyield.json is the stale fallback."""
    try:
        history = _turso_history()
        if history:
            return history
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        _log(f"turso history rehydrate non-fatal: {exc}")
    return _json_history()


def merge_series(
    history: list[dict[str, Any]], fresh_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_date = {row["date"]: row for row in history}
    by_date.update({row["date"]: row for row in fresh_rows})
    return [by_date[date] for date in sorted(by_date)]


def first_exact_date(series: list[dict[str, Any]]) -> Optional[str]:
    """backfill_cutover — the first non-approximate date in the series."""
    return next(
        (row["date"] for row in series if not row.get("approximate")), None
    )


# ── persistence ───────────────────────────────────────────────────

def _write_json_cache(payload: dict[str, Any]) -> None:
    DIVYIELD_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = DIVYIELD_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, DIVYIELD_JSON)


def persist_result(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.

    ``rows`` is empty on the unchanged-day fast path — the row upsert is
    skipped but the snapshot + heartbeat run EVERY cycle so the staleness
    banner notices a silent writer (cf. feedback_service_health_heartbeat).
    """
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if rows:
        writer.upsert_divyield_rows(rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    _write_json_cache(payload)


# ── daily orchestration ───────────────────────────────────────────

def run() -> dict[str, Any]:
    tickers, constituents_source = fetch_constituents()
    _log(f"constituents: {len(tickers)} tickers via {constituents_source}")

    yields_by_ticker, quote_errors, data_date = sweep_yields(tickers)
    ensure_plausible_payload(len(tickers), len(yields_by_ticker))
    if data_date is None:
        raise RuntimeError("no quote carried a regularMarketTime; cannot key the daily row")

    y10_date, y10 = _latest_y10()
    scan_time = _iso_utc_now()

    metrics = compute_pct_above(list(yields_by_ticker.values()), y10)
    new_row = {"date": data_date, **metrics, "y10": y10, "approximate": 0}

    history = load_history()
    latest_row = history[-1] if history else None
    if is_unchanged_day(new_row, latest_row):
        _log("source unchanged; refreshing snapshot only")
        rows: list[dict[str, Any]] = []
    else:
        rows = [new_row]

    series = merge_series(history, [new_row])
    payload = build_output(
        yields_by_ticker=yields_by_ticker,
        y10=y10,
        y10_date=y10_date,
        data_date=data_date,
        scan_time=scan_time,
        source={
            "constituents": constituents_source,
            "constituents_count": len(tickers),
            "quote_errors": quote_errors,
        },
        series=series,
        backfill_cutover=first_exact_date(series),
    )
    persist_result(payload, rows)
    return payload


# ── backfill (Option B — approximate, run once) ───────────────────

def fetch_ticker_monthly_chart(ticker: str) -> dict[str, Any]:
    """Monthly bars 1990+ with dividend events. Explicit period1/period2 —
    range=max silently degrades to 3mo bars."""
    period2 = int(datetime.now(timezone.utc).timestamp())
    url = (
        f"{YAHOO_CHART_URL.format(ticker=ticker)}"
        f"?period1={BACKFILL_PERIOD1}&period2={period2}&interval=1mo&events=div"
    )
    return json.loads(_http_get(url, BROWSER_UA))


def _month_key(stamp: int) -> tuple[int, int]:
    moment = datetime.fromtimestamp(stamp, tz=_ET)
    return moment.year, moment.month


def _month_end_date(key: tuple[int, int]) -> str:
    year, month = key
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def monthly_yields_for_chart(chart: dict[str, Any]) -> dict[tuple[int, int], float]:
    """Per-month trailing yield: TTM dividend sum / that month's close."""
    result = _chart_result(chart)
    timestamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    close_by_month: dict[tuple[int, int], float] = {}
    for stamp, close in zip(timestamps, closes):
        if close is not None and close > 0:
            close_by_month[_month_key(stamp)] = float(close)

    dividends_by_month: dict[tuple[int, int], float] = {}
    for event in ((result.get("events") or {}).get("dividends") or {}).values():
        amount = event.get("amount")
        if amount is None or "date" not in event:
            continue
        key = _month_key(event["date"])
        dividends_by_month[key] = dividends_by_month.get(key, 0.0) + float(amount)

    months = sorted(close_by_month)
    yields: dict[tuple[int, int], float] = {}
    for key in months:
        year, month = key
        window = {
            ((year * 12 + month - 1 - offset) // 12, (year * 12 + month - 1 - offset) % 12 + 1)
            for offset in range(12)
        }
        ttm = sum(dividends_by_month.get(month_key, 0.0) for month_key in window)
        yields[key] = 100.0 * ttm / close_by_month[key]
    return yields


def _y10_at_or_before(dates: list[str], values: list[float], date: str) -> Optional[float]:
    idx = bisect_right(dates, date) - 1
    return values[idx] if idx >= 0 else None


def build_backfill_rows(
    per_ticker_yields: list[dict[tuple[int, int], float]],
    y10_rows: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    """Aggregate per-ticker monthly yields into approximate monthly rows,
    joined against each month-end's last available y10."""
    y10_dates = [row[0] for row in y10_rows]
    y10_values = [row[1] for row in y10_rows]
    this_month = _month_key(int(datetime.now(timezone.utc).timestamp()))

    months: set[tuple[int, int]] = set()
    for yields in per_ticker_yields:
        months.update(yields)
    months.discard(this_month)  # partial month; the daily timer owns today

    rows: list[dict[str, Any]] = []
    for key in sorted(months):
        date = _month_end_date(key)
        y10 = _y10_at_or_before(y10_dates, y10_values, date)
        if y10 is None:
            continue
        month_yields = [
            yields[key] for yields in per_ticker_yields if key in yields
        ]
        if not month_yields:
            continue
        metrics = compute_pct_above(month_yields, y10)
        rows.append({"date": date, **metrics, "y10": y10, "approximate": 1})
    return rows


def run_backfill() -> int:
    """One-time approximate monthly build, 1990+. Pre-cutover history is a
    survivorship-biased approximation: today's constituents projected
    backward, not point-in-time index membership."""
    tickers, constituents_source = fetch_constituents()
    _log(f"backfill: {len(tickers)} tickers via {constituents_source}")

    def fetch_one(ticker: str) -> Optional[dict[tuple[int, int], float]]:
        try:
            return monthly_yields_for_chart(fetch_ticker_monthly_chart(ticker))
        except Exception as exc:  # noqa: BLE001 — dead tickers are expected
            _log(f"backfill: {ticker} failed: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        per_ticker = [result for result in pool.map(fetch_one, tickers) if result]
    _log(f"backfill: monthly series for {len(per_ticker)}/{len(tickers)} tickers")

    rows = build_backfill_rows(per_ticker, _y10_series())
    if not rows:
        _log("backfill: refusing to persist zero rows")
        return 1
    scan_time = _iso_utc_now()
    writer.ensure_no_replica_for_writers()
    writer.upsert_divyield_rows(rows, recorded_at=scan_time)
    _log(f"backfill: upserted {len(rows)} approximate monthly rows "
         f"({rows[0]['date']} .. {rows[-1]['date']})")
    return 0


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nDIVYIELD — {len(payload['series'])} rows", file=sys.stderr)
    print(
        f"  {current['date']}: {current['pct_above']:.2f}% "
        f"({current['count_above']} / {current['total']}) above 10Y {current['y10']:.2f}%",
        file=sys.stderr,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SPX dividend yield breadth vs the 10Y Treasury yield"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill", action="store_true",
        help="One-time approximate monthly build, 1990..now, then exit",
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

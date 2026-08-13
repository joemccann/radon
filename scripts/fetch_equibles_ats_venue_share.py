#!/usr/bin/env python3
"""ATS venue-share regime — FINRA off-exchange volume per watchlist ticker.

Radon already ingests trade-level dark-pool PRINTS from Unusual Whales but has
no authoritative denominator for them. FINRA's weekly off-exchange file (via
Equibles) is that denominator: ATS (dark pool) and non-ATS OTC volume and trade
counts per ticker per week.

Derived at ingestion time, stored, and served as-is to the UI:

  ats_share_pct           ats_volume / total_off_exchange_volume (0-100)
  avg_ats_print_size      ats_volume / ats_trade_count  (block footprint)
  avg_non_ats_print_size  non_ats_otc_volume / non_ats_otc_trade_count
  *_z                     trailing 52-week z-score per ticker
  short_volume_pct        weekly FINRA short-volume share, from the daily file

THE SIGNAL: rising ATS share of off-exchange flow AND rising average ATS print
size together read as an institutional accumulation footprint: more of the
off-exchange tape is dark-pool rather than retail internalization, and the
prints are bigger. Either alone is noise, so `classify` requires BOTH z-scores
past the threshold. Thin history returns neutral plus a reason string rather
than a z-score computed off four observations.

CONSOLIDATED DENOMINATOR (resolved 2026-08-13). The daily `/stocks/{t}/prices`
bars carry the all-venue tape, so off_exchange_share_pct is computed as
totalOffExchangeVolume / sum(that week's daily volume). Verified live: AAPL
39.9% and NVDA 48.2% for the week of 2026-07-06, both inside the plausible
band for large caps.

The short-volume file's `totalVolume` is NOT a valid denominator - it is
off-exchange only, and the same two weeks come out at 127% and 116% against it.
That substitution is guarded by a test. A week covered by fewer than
MIN_CONSOLIDATED_DAYS sessions is withheld rather than published understated,
and if the prices walk fails the share degrades to null while the rest of the
cycle still writes.

UPSTREAM PERCENT FIELD (verified live 2026-08-11). Equibles returns
`shortVolumePercent` ALREADY IN PERCENT (42.37 for AAPL 2026-08-10, where
shortVolume/totalVolume = 0.4237), not as a fraction, so the shared client's
fraction registry scales it a second time and emits shortVolumePct = 4236.67.
This module never reads that field: short_volume_pct is computed from the raw
shortVolume and totalVolume, which are unambiguous.

CADENCE: FINRA publishes the off-exchange file weekly and roughly five weeks in
arrears (latest week 2026-07-06 as of 2026-08-11), so the series only ever
advances one row per week no matter how often this runs. Nothing downstream may
claim faster refresh than that.

Output is dual-written to Turso equibles_ats_venue_share + scan_snapshots and
data/equibles_ats_venue_share.json.

Usage:
    python3 scripts/fetch_equibles_ats_venue_share.py                 # watchlist
    python3 scripts/fetch_equibles_ats_venue_share.py --tickers AAPL NVDA
    python3 scripts/fetch_equibles_ats_venue_share.py --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

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
SERVICE = "equibles-ats-venue-share"
ATS_VENUE_SHARE_JSON = _PROJECT_DIR / "data" / "equibles_ats_venue_share.json"

LOOKBACK_WEEKS = 104          # two years: one trailing z-window plus a year of chart
Z_WINDOW_WEEKS = 52           # trailing window the z-scores are measured against
MIN_HISTORY_WEEKS = 12        # fewer observations than this and a z-score is noise
MIN_SHORT_VOLUME_DAYS = 3     # a week with thinner daily coverage has no usable total

ACCUMULATION_Z = 1.0
DISTRIBUTION_Z = -1.0

OFF_EXCHANGE_PAGE_LIMIT = 500
SHORT_VOLUME_PAGE_LIMIT = 500
PRICES_PAGE_LIMIT = 500
OFF_EXCHANGE_MAX_PAGES = 2
SHORT_VOLUME_MAX_PAGES = 3
PRICES_MAX_PAGES = 3

_UPSERT_CHUNK_ROWS = 200

CLASSIFICATION_ACCUMULATION = "accumulation"
CLASSIFICATION_DISTRIBUTION = "distribution"
CLASSIFICATION_NEUTRAL = "neutral"

CONSOLIDATED_FROM_PAYLOAD = "off_exchange_payload"
CONSOLIDATED_FROM_PRICES = "equibles_daily_prices"

# A week short of this many sessions understates the denominator, which would
# overstate the share, so its total is withheld rather than published wrong.
MIN_CONSOLIDATED_DAYS = 4

# Upstream field aliases. The documented name is first; the rest are defensive
# so a renamed or absent field degrades to null instead of crashing the cycle.
_WEEK_KEYS = ("weekStartDate", "weekStart", "week", "date")
_DAY_KEYS = ("date", "tradeDate", "settlementDate")
_ATS_VOLUME_KEYS = ("atsVolume",)
_ATS_TRADE_COUNT_KEYS = ("atsTradeCount", "atsTrades")
_NON_ATS_VOLUME_KEYS = ("nonAtsOtcVolume", "nonAtsVolume")
_NON_ATS_TRADE_COUNT_KEYS = ("nonAtsOtcTradeCount", "nonAtsTradeCount")
_TOTAL_OFF_EXCHANGE_KEYS = ("totalOffExchangeVolume", "offExchangeVolume")
_CONSOLIDATED_KEYS = ("consolidatedVolume", "consolidatedShareVolume", "marketVolume")
_SHORT_VOLUME_KEYS = ("shortVolume",)
_DAILY_TOTAL_VOLUME_KEYS = ("totalVolume",)
_DAILY_CONSOLIDATED_VOLUME_KEYS = ("volume",)

_Z_METRICS = (
    ("ats_share_pct", "ats_share_z"),
    ("avg_ats_print_size", "avg_ats_print_size_z"),
)

_ROW_COLUMNS = (
    "ticker",
    "week_start_date",
    "ats_volume",
    "ats_trade_count",
    "non_ats_otc_volume",
    "non_ats_otc_trade_count",
    "total_off_exchange_volume",
    "ats_share_pct",
    "avg_ats_print_size",
    "avg_non_ats_print_size",
    "ats_share_z",
    "avg_ats_print_size_z",
    "short_volume_pct",
    "consolidated_volume",
    "consolidated_volume_source",
    "off_exchange_share_pct",
    "classification",
    "classification_reason",
)


# ── field access ──────────────────────────────────────────────────

def _first_present(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _number(row: dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    value = _first_present(row, keys)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


# ── week bucketing ────────────────────────────────────────────────

def week_start(day: Optional[str]) -> Optional[str]:
    """The Monday of the ISO week containing `day`; None if unparseable."""
    if not isinstance(day, str):
        return None
    try:
        parsed = date.fromisoformat(day[:10])
    except ValueError:
        return None
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def _row_week(row: dict[str, Any], keys: Iterable[str] = _WEEK_KEYS) -> Optional[str]:
    return week_start(_first_present(row, keys))


def weekly_short_volume(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Daily FINRA short-volume rows folded into weekly totals.

    The share is recomputed from the raw volumes: the upstream percent field
    arrives already in percent and is scaled a second time at the client
    boundary, so it is never read here.

    A week covered by fewer than MIN_SHORT_VOLUME_DAYS sessions has an
    understated total, which would distort the share, so its volumes are
    withheld (None) and only the day count is reported.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        week = _row_week(row, _DAY_KEYS)
        if week is None:
            continue
        bucket = buckets.setdefault(week, {"short_volume": 0.0, "total_volume": 0.0, "days": 0})
        bucket["short_volume"] += _number(row, _SHORT_VOLUME_KEYS) or 0.0
        bucket["total_volume"] += _number(row, _DAILY_TOTAL_VOLUME_KEYS) or 0.0
        bucket["days"] += 1

    for bucket in buckets.values():
        if bucket["days"] < MIN_SHORT_VOLUME_DAYS:
            bucket.update(short_volume=None, total_volume=None, short_volume_pct=None)
            continue
        share = _ratio(bucket["short_volume"], bucket["total_volume"])
        bucket["short_volume_pct"] = share * 100.0 if share is not None else None
    return buckets


# ── per-week derived metrics ──────────────────────────────────────

def weekly_consolidated_volume(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Daily /prices bars folded into weekly consolidated-tape totals.

    These bars are the all-venue tape, which is what makes the off-exchange
    share computable. A week covered by fewer than MIN_CONSOLIDATED_DAYS
    sessions is withheld, since a partial denominator inflates the share.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        week = _row_week(row, _DAY_KEYS)
        if week is None:
            continue
        bucket = buckets.setdefault(week, {"consolidated_volume": 0.0, "days": 0})
        bucket["consolidated_volume"] += _number(row, _DAILY_CONSOLIDATED_VOLUME_KEYS) or 0.0
        bucket["days"] += 1

    for bucket in buckets.values():
        if bucket["days"] < MIN_CONSOLIDATED_DAYS or not bucket["consolidated_volume"]:
            bucket["consolidated_volume"] = None
    return buckets


def _resolve_consolidated_volume(
    row: dict[str, Any], consolidated_week: Optional[dict[str, Any]]
) -> tuple[Optional[float], Optional[str]]:
    """All-venue denominator: the payload's own figure, else the /prices week.

    Only a genuinely consolidated figure qualifies. The short-volume file's
    totalVolume is off-exchange only, so substituting it would report an
    off-exchange share above 100%; absent a real denominator the share stays
    null and the venue mix is measured by ats_share_pct instead.
    """
    from_payload = _number(row, _CONSOLIDATED_KEYS)
    if from_payload:
        return from_payload, CONSOLIDATED_FROM_PAYLOAD

    from_prices = (consolidated_week or {}).get("consolidated_volume")
    if from_prices:
        return from_prices, CONSOLIDATED_FROM_PRICES

    return None, None


def derive_week_metrics(
    row: dict[str, Any],
    short_week: Optional[dict[str, Any]],
    consolidated_week: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """One weekly off-exchange row plus its short-volume and consolidated weeks."""
    week = _row_week(row)
    if week is None:
        return None

    ats_volume = _number(row, _ATS_VOLUME_KEYS)
    ats_trade_count = _number(row, _ATS_TRADE_COUNT_KEYS)
    non_ats_volume = _number(row, _NON_ATS_VOLUME_KEYS)
    non_ats_trade_count = _number(row, _NON_ATS_TRADE_COUNT_KEYS)
    total_off_exchange = _number(row, _TOTAL_OFF_EXCHANGE_KEYS)
    consolidated, consolidated_source = _resolve_consolidated_volume(row, consolidated_week)
    off_exchange_share = _ratio(total_off_exchange, consolidated)
    ats_share = _ratio(ats_volume, total_off_exchange)

    return {
        "week_start_date": week,
        "ats_volume": ats_volume,
        "ats_trade_count": ats_trade_count,
        "non_ats_otc_volume": non_ats_volume,
        "non_ats_otc_trade_count": non_ats_trade_count,
        "total_off_exchange_volume": total_off_exchange,
        "ats_share_pct": ats_share * 100.0 if ats_share is not None else None,
        "avg_ats_print_size": _ratio(ats_volume, ats_trade_count),
        "avg_non_ats_print_size": _ratio(non_ats_volume, non_ats_trade_count),
        "short_volume_pct": (short_week or {}).get("short_volume_pct"),
        "consolidated_volume": consolidated,
        "consolidated_volume_source": consolidated_source,
        "off_exchange_share_pct": (
            off_exchange_share * 100.0 if off_exchange_share is not None else None
        ),
    }


# ── trailing z-scores ─────────────────────────────────────────────

def _trailing_zscore(history: list[float], value: Optional[float]) -> Optional[float]:
    if value is None or len(history) < MIN_HISTORY_WEEKS:
        return None
    deviation = statistics.pstdev(history)
    if deviation == 0:
        return None
    return (value - statistics.fmean(history)) / deviation


def attach_zscores(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach each metric's z-score against its own trailing 52-week window.

    The window holds only PRIOR observations, so a week is scored against the
    history that preceded it and never against itself. Nulls are skipped, not
    zero-filled: a gap shortens the window instead of dragging the mean.
    """
    scored: list[dict[str, Any]] = []
    for index, row in enumerate(series):
        window = series[max(0, index - Z_WINDOW_WEEKS):index]
        enriched = dict(row)
        for metric, z_key in _Z_METRICS:
            history = [r[metric] for r in window if r.get(metric) is not None]
            enriched[z_key] = _trailing_zscore(history, row.get(metric))
            if metric == _Z_METRICS[0][0]:
                enriched["z_observations"] = len(history)
        scored.append(enriched)
    return scored


# ── classification ────────────────────────────────────────────────

def classify(
    ats_share_z: Optional[float],
    avg_ats_print_size_z: Optional[float],
    observations: int,
) -> tuple[str, Optional[str]]:
    """Joint reading of venue mix and block footprint.

    Accumulation needs BOTH elevated: ATS share alone rises on any week the
    retail internalizers go quiet, and print size alone rises on a single
    cross. Together they are the institutional footprint.
    """
    if ats_share_z is None or avg_ats_print_size_z is None:
        if observations < MIN_HISTORY_WEEKS:
            return (
                CLASSIFICATION_NEUTRAL,
                f"insufficient history: {observations} of {MIN_HISTORY_WEEKS} weeks",
            )
        return (
            CLASSIFICATION_NEUTRAL,
            "z-score unavailable: flat or incomplete trailing history",
        )
    if ats_share_z >= ACCUMULATION_Z and avg_ats_print_size_z >= ACCUMULATION_Z:
        return CLASSIFICATION_ACCUMULATION, None
    if ats_share_z <= DISTRIBUTION_Z and avg_ats_print_size_z <= DISTRIBUTION_Z:
        return CLASSIFICATION_DISTRIBUTION, None
    return CLASSIFICATION_NEUTRAL, None


# ── series assembly ───────────────────────────────────────────────

def build_ticker_series(
    off_exchange_rows: list[dict[str, Any]],
    short_volume_rows: list[dict[str, Any]],
    price_rows: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Raw endpoint rows to the ascending, fully derived weekly series."""
    short_by_week = weekly_short_volume(short_volume_rows)
    consolidated_by_week = weekly_consolidated_volume(price_rows or [])

    by_week: dict[str, dict[str, Any]] = {}
    for row in off_exchange_rows:
        week = _row_week(row)
        if week is None:
            continue
        metrics = derive_week_metrics(
            row, short_by_week.get(week), consolidated_by_week.get(week)
        )
        if metrics is not None:
            by_week[week] = metrics

    ascending = [by_week[week] for week in sorted(by_week)]
    classified: list[dict[str, Any]] = []
    for row in attach_zscores(ascending):
        verdict, reason = classify(
            row.get("ats_share_z"),
            row.get("avg_ats_print_size_z"),
            row.get("z_observations", 0),
        )
        classified.append({**row, "classification": verdict, "classification_reason": reason})
    return classified


def _conviction_rank(row: dict[str, Any]) -> float:
    z = row.get("ats_share_z")
    return -z if isinstance(z, (int, float)) else float("inf")


def build_payload(
    series_by_ticker: dict[str, list[dict[str, Any]]],
    scan_time: str,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Route payload: latest week per ticker, ranked, plus the full series."""
    current = [
        {**series[-1], "ticker": ticker}
        for ticker, series in sorted(series_by_ticker.items())
        if series
    ]
    current.sort(key=_conviction_rank)
    weeks = [row["week_start_date"] for row in current if row.get("week_start_date")]

    return {
        "scan_time": scan_time,
        "source": "finra-off-exchange-weekly",
        "count": len(current),
        "tickers": sorted(row["ticker"] for row in current),
        "week_start_date": max(weeks) if weeks else None,
        "thresholds": {
            "accumulation_z": ACCUMULATION_Z,
            "distribution_z": DISTRIBUTION_Z,
            "z_window_weeks": Z_WINDOW_WEEKS,
            "min_history_weeks": MIN_HISTORY_WEEKS,
        },
        "current": current,
        "series": series_by_ticker,
        "errors": errors,
    }


def payload_has_data(payload: dict[str, Any]) -> bool:
    """Cache-write gate: an all-error cycle must never overwrite good data."""
    return bool(payload.get("current"))


# ── persistence ───────────────────────────────────────────────────

def ats_venue_share_upsert(
    rows: list[dict[str, Any]], recorded_at: str
) -> tuple[str, tuple[Any, ...]]:
    """Chunk-ready multi-row upsert, idempotent on (ticker, week_start_date).

    One statement per chunk: `executemany` is a round-trip PER ROW over Hrana
    (cf. scripts/CLAUDE.md, Turso I/O bounding).
    """
    columns = ", ".join((*_ROW_COLUMNS, "recorded_at"))
    placeholders = ", ".join(f"({', '.join('?' * (len(_ROW_COLUMNS) + 1))})" for _ in rows)
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in (*_ROW_COLUMNS[2:], "recorded_at")
    )
    params: list[Any] = []
    for row in rows:
        params.extend(row.get(column) for column in _ROW_COLUMNS)
        params.append(recorded_at)

    sql = (
        f"INSERT INTO equibles_ats_venue_share ({columns}) VALUES {placeholders} "
        f"ON CONFLICT(ticker, week_start_date) DO UPDATE SET {assignments}"
    )
    return sql, tuple(params)


def _storage_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**row, "ticker": ticker}
        for ticker, series in payload.get("series", {}).items()
        for row in series
    ]


def _write_db_cache(payload: dict[str, Any], scan_time: str) -> None:
    """Durable weekly rows plus the snapshot the Next.js route dbFirstReads."""
    try:
        from db import writer
        from db.client import get_db
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        rows = _storage_rows(payload)
        db = get_db()
        for start in range(0, len(rows), _UPSERT_CHUNK_ROWS):
            sql, params = ats_venue_share_upsert(rows[start:start + _UPSERT_CHUNK_ROWS], scan_time)
            db.execute(sql, params)
        db.commit()
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[ats-venue-share] db cache non-fatal: {exc}", file=sys.stderr)


def _record_health(state: str, scan_time: str, error: Optional[dict[str, Any]] = None) -> None:
    """Writer-state heartbeat, written on EVERY cycle so silence reads as down."""
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.record_service_health(SERVICE, state, finished_at=scan_time, error=error)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[ats-venue-share] health write non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    ATS_VENUE_SHARE_JSON.parent.mkdir(parents=True, exist_ok=True)
    ATS_VENUE_SHARE_JSON.write_text(json.dumps(payload, indent=2))


# ── orchestration ─────────────────────────────────────────────────

def watchlist_tickers() -> list[str]:
    from db.readers import read_watchlist_tickers

    return read_watchlist_tickers()


def _fetch_ticker(
    client: Any, ticker: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """The bounded pagination walks for one ticker, off-exchange first.

    The daily-prices walk supplies the consolidated denominator; it is the only
    all-venue volume Equibles publishes, so without it off_exchange_share_pct
    stays null. A failure there degrades that one field rather than the cycle.
    """
    off_exchange = client.paginate(
        client.get_off_exchange_volume,
        ticker,
        limit=OFF_EXCHANGE_PAGE_LIMIT,
        max_pages=OFF_EXCHANGE_MAX_PAGES,
        start_date=start_date,
        end_date=end_date,
    )
    short_volume = client.paginate(
        client.get_short_volume,
        ticker,
        limit=SHORT_VOLUME_PAGE_LIMIT,
        max_pages=SHORT_VOLUME_MAX_PAGES,
        start_date=start_date,
        end_date=end_date,
    )

    try:
        prices = client.paginate(
            client.get_prices,
            ticker,
            limit=PRICES_PAGE_LIMIT,
            max_pages=PRICES_MAX_PAGES,
            start_date=start_date,
            end_date=end_date,
        )
        price_rows = prices.rows
    except Exception as exc:  # noqa: BLE001 - denominator is optional, cycle is not
        print(
            f"[ats-venue-share] {ticker}: consolidated denominator unavailable: {exc}",
            file=sys.stderr,
        )
        price_rows = []

    return build_ticker_series(off_exchange.rows, short_volume.rows, price_rows)


def run(
    client: Optional[Any] = None,
    *,
    tickers: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    lookback_weeks: int = LOOKBACK_WEEKS,
) -> dict[str, Any]:
    """Fetch, derive, cache, and return the ATS venue-share payload.

    One ticker's failure is recorded and stepped over: a 404 on a delisted
    watchlist name must not cost the other tickers their refresh.
    """
    from clients.equibles_client import EquiblesAPIError

    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_date = now.date()
    start_date = end_date - timedelta(weeks=lookback_weeks)

    owned_client = client is None
    if owned_client:
        from clients.equibles_client import EquiblesClient
        client = EquiblesClient()

    series_by_ticker: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    try:
        for ticker in tickers or watchlist_tickers():
            try:
                series = _fetch_ticker(
                    client, ticker, start_date.isoformat(), end_date.isoformat()
                )
            except EquiblesAPIError as exc:
                errors.append({"ticker": ticker, "error": str(exc), "code": exc.code})
                continue
            if series:
                series_by_ticker[ticker] = series
    finally:
        if owned_client:
            client.close()

    payload = build_payload(series_by_ticker, scan_time, errors)
    if not payload_has_data(payload):
        _record_health("error", scan_time, error={"message": "no ticker produced a series"})
        return payload

    _write_db_cache(payload, scan_time)
    _write_json_cache(payload)
    _record_health("ok", scan_time)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    week = payload.get("week_start_date") or "n/a"
    print(f"\nATS VENUE SHARE — {payload['count']} tickers, week of {week}", file=sys.stderr)
    for row in payload["current"][:20]:
        share = row.get("ats_share_pct")
        print_size = row.get("avg_ats_print_size")
        share_text = f"{share:5.1f}%" if share is not None else "  n/a"
        size_text = f"{print_size:,.0f}" if print_size is not None else "n/a"
        print(
            f"  {row['ticker']:<6} ats share {share_text}  ats print {size_text:>9}"
            f"  {row['classification'].upper()}",
            file=sys.stderr,
        )
    for error in payload.get("errors", []):
        print(f"  ! {error['ticker']}: {error['error']}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FINRA off-exchange / ATS venue-share regime per watchlist ticker"
    )
    parser.add_argument("--tickers", nargs="+", help="Override the watchlist")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run(tickers=[t.upper() for t in args.tickers] if args.tickers else None)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

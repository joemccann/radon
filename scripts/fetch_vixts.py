#!/usr/bin/env python3
"""VIX TS Indicator — VIX / VIX3M term-structure ratio.

Descriptive regime read, not a forecast: below 1.00 the volatility curve is
in contango and near-term vol is priced under 3-month vol; above 1.00 it is
in backwardation and stress has moved into the front of the curve.

Source: Cboe CDN daily-price CSVs (VIX_History.csv + VIX3M_History.csv, with
SPX_History.csv as the chart overlay). VIX3M history starts 2009-09-18 and is
the binding constraint. Cboe CDN only: IB and UW cannot serve this series
cleanly, so the plausibility guard in lib/vixts_math.py is the sole
protection against a bad source. Spec: docs/indicators/vixts.md.

Output is dual-written to Turso vixts_history + data/vixts.json.

Usage:
    python3 scripts/fetch_vixts.py            # human summary (stderr)
    python3 scripts/fetch_vixts.py --json     # JSON to stdout
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

try:
    from db import writer  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — tests inject a FakeWriter
    writer = None  # type: ignore[assignment]

from lib.vixts_math import (  # noqa: E402
    build_current,
    compute_stats,
    ensure_plausible_series,
    join_series,
)

# ── constants ─────────────────────────────────────────────────────
VIXTS_JSON = _PROJECT_DIR / "data" / "vixts.json"

SERVICE = "vixts"
# Calendar days of lag tolerated before the run stops calling itself fresh.
# One session plus a weekend. Applies to BOTH branches: a 304 and a 200 that
# rebuilds an unmoved series are the same staleness. R-333 / T-263.
_MAX_CACHE_LAG_DAYS = 4

# SPX_History.csv genuinely publishes its close under an "SPX" column, not
# "CLOSE" (same quirk as VVIX_History.csv), so the column is per-symbol.
_SYMBOLS = ("VIX", "VIX3M", "SPX")
_VALUE_COLUMN = {"VIX": "CLOSE", "VIX3M": "CLOSE", "SPX": "SPX"}

# Mirrors radon-vixts.timer (OnCalendar=*-*-* 02:45:00 UTC) so heartbeat copy
# can name the next attempt instead of hardcoding cadence text.
TIMER_HOUR_UTC = 2
TIMER_MINUTE_UTC = 45


# ── Cboe CSV parsing ──────────────────────────────────────────────

def parse_index_csv(text: str, value_column: str) -> list[dict[str, Any]]:
    """Cboe daily-prices CSV → ascending [{date: "YYYY-MM-DD", value: float}].

    Dates arrive MM/DD/YYYY; header and malformed rows (bad date, missing
    or empty value) are skipped.
    """
    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        try:
            date = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y")
            value = float(raw[value_column])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"date": date.date().isoformat(), "value": value})
    rows.sort(key=lambda r: r["date"])
    return rows


# ── persistence ───────────────────────────────────────────────────

def _read_json_cache() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/vixts.json, if present."""
    try:
        return json.loads(VIXTS_JSON.read_text())
    except (OSError, ValueError):
        return None


def _write_json_cache(payload: dict[str, Any]) -> None:
    """Atomic write of data/vixts.json — fallback only; Turso is the truth."""
    VIXTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = VIXTS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, VIXTS_JSON)


def _write_db(
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Snapshot + heartbeat every cycle; row upserts only when the source moved.

    The snapshot and heartbeat run even on the 304 path so a silently dead
    writer trips the staleness banner (feedback_service_health_heartbeat).
    """
    if writer is None:
        return
    row_error: Optional[dict[str, Any]] = None
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_vixts_rows(payload["series"], recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001
        # R-192: see fetch_vixcor._write_db — a failed row upsert must not
        # take the snapshot and the heartbeat down with it and still exit 0.
        print(f"[vixts] row upsert failed: {exc}", file=sys.stderr)
        row_error = {
            "message": f"vixts row upsert failed: {exc}",
            "class": "db_write_failed",
        }
    snapshot_error: Optional[dict[str, Any]] = None
    try:
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    except Exception as exc:  # noqa: BLE001
        # Same argument R-192 made for the row upsert, applied one statement
        # later: a failed snapshot must not take the HEARTBEAT down with it.
        # Sharing one try meant a snapshot failure produced no service_health
        # row at all, which is the R-331 outcome by another route.
        print(f"[vixts] snapshot write failed: {exc}", file=sys.stderr)
        snapshot_error = {
            "message": f"vixts snapshot write failed: {exc}",
            "class": "db_write_failed",
        }
    error = health_error or row_error or snapshot_error
    try:
        writer.record_service_health(
            SERVICE,
            "ok" if error is None else "error",
            finished_at=scan_time,
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[vixts] health heartbeat non-fatal: {exc}", file=sys.stderr)


def _record_startup_failure(scan_time: str, exc: Exception) -> None:
    """Heartbeat an error row for a failure that predates any payload."""
    if writer is None:
        return
    try:
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=scan_time,
            error={
                "message": f"vixts client init failed: {exc}",
                "class": "client_init_failed",
            },
        )
    except Exception as inner:  # noqa: BLE001 — best-effort mirror
        print(f"[vixts] startup health write non-fatal: {inner}", file=sys.stderr)


# ── payload ───────────────────────────────────────────────────────

def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    source_last_modified: dict[str, Optional[str]],
) -> dict[str, Any]:
    """Full API contract for the vixts snapshot / JSON fallback."""
    return {
        "scan_time": scan_time,
        "source_last_modified": source_last_modified,
        "data_date": series[-1]["date"] if series else None,
        "count": len(series),
        "current": build_current(series),
        "stats": compute_stats(series),
        "series": series,
    }


# ── orchestration ─────────────────────────────────────────────────

def _fetch_all(client: Any, cached_stamps: dict[str, Any]) -> tuple[dict, dict]:
    """One conditional GET per symbol, keyed by the cached per-file stamp."""
    texts: dict[str, Optional[str]] = {}
    stamps: dict[str, Optional[str]] = {}
    for symbol in _SYMBOLS:
        text, last_modified = client.fetch_history(
            symbol, if_modified_since=cached_stamps.get(symbol.lower())
        )
        texts[symbol] = text
        stamps[symbol.lower()] = last_modified
    return texts, stamps


def _refetch_unchanged(client: Any, texts: dict, stamps: dict) -> None:
    """Re-pull unconditionally: a rebuild needs all three texts, 304 or not."""
    for symbol in _SYMBOLS:
        if texts[symbol] is None:
            texts[symbol], stamps[symbol.lower()] = client.fetch_history(symbol)


def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch all three Cboe legs, compute, dual-write, and return the payload.

    Conditional GET on every file using the cached per-file stamps: when all
    three come back 304 the cached payload is reused with a fresh scan_time
    and only the snapshot + heartbeat refresh. Any change rebuilds the whole
    series, which is idempotent because Cboe serves full history every pull.
    """
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if client is None:
        try:
            from clients.cboe_client import CboeClient
            client = CboeClient()
        except Exception as exc:  # noqa: BLE001 — re-raised after the heartbeat
            # A ctor that raises here would otherwise kill the oneshot before
            # any service_health row exists: silent, not stale, and nothing
            # alerts. Heartbeat the failure, then let it propagate.
            _record_startup_failure(scan_time, exc)
            raise

    # R-331 (NF-9): everything past the client constructor used to be
    # uncovered, so a raise from _fetch_all / parse / join / plausibility /
    # write exited the oneshot with NO service_health row and yesterday's `ok`
    # left standing — the R-276 class recurring in a file written the same
    # week R-276 was fixed in fetch_vixcor. Mirror that fix: heartbeat the
    # failure, then let it propagate so the run stays retryable.
    try:
        return _run_cycle(client, scan_time=scan_time, now=now)
    except Exception as exc:  # noqa: BLE001 — re-raised after the heartbeat
        _record_cycle_failure(scan_time, exc)
        raise


def _run_cycle(client: Any, *, scan_time: str, now: datetime) -> dict[str, Any]:
    cached = _read_json_cache()
    texts, stamps = _fetch_all(client, (cached or {}).get("source_last_modified") or {})

    if cached and all(text is None for text in texts.values()):
        print("[vixts] all sources unchanged (304); refreshing snapshot only", file=sys.stderr)
        # A conditional GET reuses the previous numbers, so it must re-derive
        # the previous VERDICT too. Writing a bare `ok` here let a stuck CDN
        # edge answering 304 indefinitely hold the 26h window green with the
        # ratio frozen at an old session: only `scan_time` may advance while
        # the data stands still. Same contract as
        # `fetch_vixcor.restate_cached_payload`. R-333.
        payload, health_error = restate_cached_payload(cached, scan_time=scan_time, now=now)
        _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
        _write_json_cache(payload)
        return payload

    _refetch_unchanged(client, texts, stamps)
    parsed = {
        symbol: parse_index_csv(texts[symbol], _VALUE_COLUMN[symbol]) for symbol in _SYMBOLS
    }
    series = join_series(parsed["VIX"], parsed["VIX3M"], parsed["SPX"])
    ensure_plausible_series(series)

    payload = build_payload(series, scan_time=scan_time, source_last_modified=stamps)
    # A 200 is not evidence of a new session: Cboe re-touches Last-Modified
    # intraday WITHOUT appending the session row (clients/cboe_client.py), so
    # the rebuild branch republishes an unmoved series just as readily as a
    # 304 reuses one. Age it the same way. T-263.
    payload, health_error = _apply_freshness_verdict(
        payload, str(payload["data_date"] or ""), now=now
    )
    print(
        f"[vixts] {payload['count']} joined sessions through {payload['data_date']} "
        f"(ratio {payload['current']['ratio']}, {payload['current']['regime']}) "
        f"[{payload['status']}]",
        file=sys.stderr,
    )
    _write_db(payload, scan_time, rows_changed=True, health_error=health_error)
    _write_json_cache(payload)
    return payload


def restate_cached_payload(
    cached: dict[str, Any], *, scan_time: str, now: datetime
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Re-age the freshness verdict of the payload a 304 reuses. R-333."""
    payload = {**cached, "scan_time": scan_time}
    data_date = str(cached.get("data_date") or "")
    if not data_date:
        payload["expected_session"] = _expected_session(now)
        payload["status"] = "stale_source"
        return payload, {
            "message": "vixts cache carries no data_date; cannot age the 304 reuse",
            "class": "stale_source",
        }
    return _apply_freshness_verdict(payload, data_date, now=now)


def _expected_session(now: datetime) -> str:
    from utils.market_calendar import last_completed_session_date

    return last_completed_session_date(now)


def _apply_freshness_verdict(
    payload: dict[str, Any], data_date: str, *, now: datetime
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Stamp status / lag_days / expected_session and name the staleness error.

    Shared by both branches of `_run_cycle`. R-333 built this defence on the
    304 branch only, i.e. the one that CANNOT carry new data; the rebuild
    branch emitted no verdict at all and heartbeat a bare `ok`, so a source
    that re-serves an old series with a fresh Last-Modified rendered a
    confident regime badge on a two-week-old session. T-263.
    """
    expected_session = _expected_session(now)
    lag_days = _calendar_days_between(data_date, expected_session)
    payload["expected_session"] = expected_session
    payload["lag_days"] = lag_days
    # One session of lag is the normal shape between the timer fire and Cboe
    # publishing; beyond that the source is not moving.
    if lag_days > _MAX_CACHE_LAG_DAYS:
        payload["status"] = "stale_source"
        return payload, {
            "message": (
                f"vixts data is dated {data_date} against an expected "
                f"{expected_session} ({lag_days} calendar days); the source is "
                "not publishing new sessions"
            ),
            "class": "stale_source",
        }
    payload["status"] = "ok"
    return payload, None


def _calendar_days_between(start: str, end: str) -> int:
    from datetime import date as _date

    try:
        return (_date.fromisoformat(end) - _date.fromisoformat(start)).days
    except ValueError:
        return _MAX_CACHE_LAG_DAYS + 1


def _record_cycle_failure(scan_time: str, exc: Exception) -> None:
    """Heartbeat an error row for any failure inside the fetch/build cycle."""
    if writer is None:
        return
    try:
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=scan_time,
            error={
                "message": f"vixts cycle failed: {exc}",
                "class": "cycle_failed",
            },
        )
    except Exception as inner:  # noqa: BLE001 — best-effort mirror
        print(f"[vixts] cycle health write non-fatal: {inner}", file=sys.stderr)


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    print(
        f"\nVIX TS — {payload['count']} sessions through {payload.get('data_date')}",
        file=sys.stderr,
    )
    print(
        f"  ratio      {current.get('ratio')}  [{current.get('regime')}]",
        file=sys.stderr,
    )
    print(
        f"  vix/vix3m  {current.get('vix')} / {current.get('vix3m')}",
        file=sys.stderr,
    )
    if stats:
        print(
            f"  range      {stats['min']:.4f} .. {stats['max']:.4f}  "
            f"median {stats['median']:.4f}",
            file=sys.stderr,
        )
        print(
            f"  backwardation  {stats['days_backwardation']} days "
            f"({stats['pct_backwardation']:.2f}%), last {stats['last_backwardation_date']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VIX TS — VIX / VIX3M term-structure ratio (Cboe CDN)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

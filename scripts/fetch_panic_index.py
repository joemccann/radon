#!/usr/bin/env python3
"""Panic Proxy — equal-weight mean of 252-session z-scores of four Cboe series.

Radon's reconstruction of the four inputs Goldman names for its Panic Index
(VIX, VVIX, VIX/VIX3M, Cboe SKEW). Not Goldman's index. Spec:
docs/indicators/panic-index.md.

Source: Cboe CDN daily-price CSVs only. UW 25d skew is an overlay and is
never folded into the composite. Output is dual-written to Turso
panic_index_history + data/panic_index.json.

Usage:
    python3 scripts/fetch_panic_index.py                 # human summary (stderr)
    python3 scripts/fetch_panic_index.py --json          # JSON to stdout
    python3 scripts/fetch_panic_index.py --json --no-alert
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

from fetch_vixts import parse_index_csv  # noqa: E402
from lib.panic_index_math import (  # noqa: E402
    attach_delta,
    attach_level,
    attach_skew25d,
    attach_z_scores,
    build_current,
    compute_stats,
    ensure_plausible_series,
    format_alert_message,
    join_series,
    pearson_z_skew_overlay,
    should_fire_decline,
    strip_compute_fields,
)
from lib.panic_index_math import Z_WINDOW  # noqa: E402
from utils import index_live  # noqa: E402

PANIC_INDEX_JSON = _PROJECT_DIR / "data" / "panic_index.json"
SKEW_JSON = _PROJECT_DIR / "data" / "skew.json"

SERVICE = "panic-index"
_MAX_CACHE_LAG_DAYS = 4

_SYMBOLS = ("VIX", "VIX3M", "VVIX", "SKEW")
_VALUE_COLUMN = {"VIX": "CLOSE", "VIX3M": "CLOSE", "VVIX": "VVIX", "SKEW": "SKEW"}

# Provisional current-session row (radon-panic-index.timer fires every 5
# minutes in ET trading hours): one source for all four inputs, written apart
# from the ~1 MB official snapshot. Never alerts.
LIVE_SERVICE = index_live.live_service(SERVICE)

ALERT_URL = "https://app.radon.run/regime/panic-index"
ALERT_URL_TITLE = "Open Panic Proxy"
ALERT_TITLE = "radon panic proxy: record 1d decline"


# ── overlay (UW 25d, decoration only) ─────────────────────────────

def load_skew25d_rows() -> list[dict[str, Any]]:
    """Turso skew_history first; data/skew.json fallback. Never raises."""
    try:
        from db.client import get_db

        rows = get_db().execute(
            "SELECT date, ratio FROM skew_history ORDER BY date"
        ).fetchall()
        if rows:
            return [
                {"date": row[0], "value": float(row[1])}
                for row in rows
                if row[0] and row[1] is not None
            ]
    except Exception as exc:  # noqa: BLE001 — overlay must not fail the run
        print(f"[panic-index] skew25d turso overlay non-fatal: {exc}", file=sys.stderr)
    return _load_skew25d_from_json()


def _load_skew25d_from_json() -> list[dict[str, Any]]:
    try:
        payload = json.loads(SKEW_JSON.read_text())
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for row in payload.get("series") or []:
        if row.get("ratio") is None or row.get("is_intraday"):
            continue
        try:
            out.append({"date": row["date"], "value": float(row["ratio"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(PANIC_INDEX_JSON.read_text())
    except (OSError, ValueError):
        return None


def _write_json_cache(payload: dict[str, Any]) -> None:
    PANIC_INDEX_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = PANIC_INDEX_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    os.replace(tmp, PANIC_INDEX_JSON)


def _write_db(
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
    health_error: Optional[dict[str, Any]] = None,
    snapshot: bool = True,
) -> None:
    """Snapshot + heartbeat every cycle; row upserts only when the source moved.

    ``snapshot=False`` is the unchanged-source live tick: heartbeat only, the
    official snapshot stays as written (it is re-aged at least every 12h).
    """
    if writer is None:
        return
    row_error: Optional[dict[str, Any]] = None
    try:
        if rows_changed:
            writer.upsert_panic_index_rows(payload["series"], recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001
        print(f"[panic-index] row upsert failed: {exc}", file=sys.stderr)
        row_error = {
            "message": f"panic-index row upsert failed: {exc}",
            "class": "db_write_failed",
        }
    snapshot_error: Optional[dict[str, Any]] = None
    try:
        if snapshot:
            writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[panic-index] snapshot write failed: {exc}", file=sys.stderr)
        snapshot_error = {
            "message": f"panic-index snapshot write failed: {exc}",
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
        print(f"[panic-index] health heartbeat non-fatal: {exc}", file=sys.stderr)


def _record_startup_failure(scan_time: str, exc: Exception) -> None:
    if writer is None:
        return
    try:
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=scan_time,
            error={
                "message": f"panic-index client init failed: {exc}",
                "class": "client_init_failed",
            },
        )
    except Exception as inner:  # noqa: BLE001
        print(f"[panic-index] startup health write non-fatal: {inner}", file=sys.stderr)


def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    source_last_modified: dict[str, Optional[str]],
    dropped_dates: list[str],
    alert: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    strip_compute_fields(series)
    current = build_current(series)
    deltas = [row for row in series if row.get("delta_1d") is not None]
    return {
        "scan_time": scan_time,
        "source_last_modified": source_last_modified,
        "data_date": series[-1]["date"] if series else None,
        "count": len(series),
        "delta_count": len(deltas),
        "dropped_dates": dropped_dates,
        "z_window": 252,
        "rank_window": 2520,
        "current": current,
        "stats": compute_stats(series),
        "alert": alert or {"last_fired_date": None, "last_fired_kind": None},
        "series": series,
    }


def _fetch_all(client: Any, cached_stamps: dict[str, Any]) -> tuple[dict, dict]:
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
    for symbol in _SYMBOLS:
        if texts[symbol] is None:
            texts[symbol], stamps[symbol.lower()] = client.fetch_history(symbol)


def run(
    client: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
    no_alert: bool = False,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if client is None:
        try:
            from clients.cboe_client import CboeClient
            client = CboeClient()
        except Exception as exc:  # noqa: BLE001
            _record_startup_failure(scan_time, exc)
            raise
    try:
        return _run_cycle(client, scan_time=scan_time, now=now, no_alert=no_alert)
    except Exception as exc:  # noqa: BLE001
        _record_cycle_failure(scan_time, exc)
        raise


def _run_cycle(
    client: Any, *, scan_time: str, now: datetime, no_alert: bool
) -> dict[str, Any]:
    cached = _read_json_cache()
    texts, stamps = _fetch_all(client, (cached or {}).get("source_last_modified") or {})

    if cached and all(text is None for text in texts.values()):
        print("[panic-index] all sources unchanged (304); refreshing snapshot only", file=sys.stderr)
        payload, health_error = restate_cached_payload(cached, scan_time=scan_time, now=now)
        live = _live_payload(payload, scan_time=scan_time, now=now)
        if live["row"] is not None and not index_live.snapshot_is_old(cached.get("scan_time"), now):
            _write_db(payload, scan_time, rows_changed=False, health_error=health_error, snapshot=False)
        else:
            _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
            _write_json_cache(payload)
        _write_live(live, scan_time)
        return payload

    _refetch_unchanged(client, texts, stamps)
    parsed = {
        symbol: parse_index_csv(texts[symbol], _VALUE_COLUMN[symbol]) for symbol in _SYMBOLS
    }
    series, dropped_dates, base_count = join_series(
        parsed["VIX"], parsed["VIX3M"], parsed["VVIX"], parsed["SKEW"]
    )
    attach_z_scores(series)
    attach_level(series)
    attach_delta(series)
    attach_skew25d(series, load_skew25d_rows())
    ensure_plausible_series(series, dropped_dates, base_count)

    alert = dict((cached or {}).get("alert") or {})
    payload = build_payload(
        series,
        scan_time=scan_time,
        source_last_modified=stamps,
        dropped_dates=dropped_dates,
        alert=alert,
    )
    payload, health_error = _apply_freshness_verdict(
        payload, str(payload["data_date"] or ""), now=now
    )
    _maybe_alert(payload, cached, no_alert=no_alert)
    corr = pearson_z_skew_overlay(series)
    print(
        f"[panic-index] {payload['count']} joined sessions through {payload['data_date']} "
        f"(delta_count {payload['delta_count']}, level {payload['current']['level']}, "
        f"delta_1d {payload['current']['delta_1d']}) [{payload['status']}]"
        + (f" pearson(z_SKEW, z_25d)={corr:.4f}" if corr is not None else ""),
        file=sys.stderr,
    )
    _write_db(payload, scan_time, rows_changed=True, health_error=health_error)
    _write_json_cache(payload)
    _write_live(_live_payload(payload, scan_time=scan_time, now=now), scan_time)
    return payload


def _fetch_live(now: datetime) -> Optional[dict[str, Any]]:
    return index_live.fetch_live(_SYMBOLS, now, try_ib=index_live.ib_allowed(), label=SERVICE)


_LIVE_FIELDS = {"VIX": "vix", "VIX3M": "vix3m", "VVIX": "vvix", "SKEW": "skew"}


def _live_payload(
    payload: dict[str, Any],
    *,
    scan_time: str,
    now: datetime,
    fetched: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Provisional row for the session after the official close, or row None.

    Rebuilt with the same join / z / level / delta math over the last
    Z_WINDOW + 8 official sessions plus the live inputs, then carded against
    the full official series so the 10-year delta ranks stay exact. The UW
    25d overlay is not available live and stays None.
    """
    live = fetched if fetched is not None else _fetch_live(now)
    series = payload.get("series") or []
    row: Optional[dict[str, Any]] = None
    current: Optional[dict[str, Any]] = None
    if series and index_live.is_newer(live, payload.get("data_date"), now):
        tail = series[-(Z_WINDOW + 8):]
        raw = {symbol: [{"date": r["date"], "value": r[field]} for r in tail]
               + [{"date": live["date"], "value": live["values"][symbol]}]  # type: ignore[index]
               for symbol, field in _LIVE_FIELDS.items()}
        rebuilt, _dropped, _base = join_series(raw["VIX"], raw["VIX3M"], raw["VVIX"], raw["SKEW"])
        attach_z_scores(rebuilt)
        attach_level(rebuilt)
        attach_delta(rebuilt)
        attach_skew25d(rebuilt, [])
        strip_compute_fields(rebuilt)
        if rebuilt and rebuilt[-1]["date"] == live["date"] and rebuilt[-1]["level"] is not None:  # type: ignore[index]
            row = rebuilt[-1]
            current = build_current([*series, row])
    return {
        "scan_time": scan_time,
        "row": row,
        "current": current,
        "source": live["source"] if row else None,  # type: ignore[index]
        "quote_time": live["quote_time"] if row else None,  # type: ignore[index]
    }


def _write_live(live: dict[str, Any], scan_time: str) -> None:
    """Advisory: a failed live write never fails the official run.

    Only a real row is written; the API ignores a row not newer than the
    official close, so yesterday's provisional row needs no clearing write.
    """
    if writer is None or live["row"] is None:
        return
    try:
        writer.upsert_scan_snapshot(LIVE_SERVICE, scan_time, live)
    except Exception as exc:  # noqa: BLE001
        print(f"[panic-index] live snapshot write non-fatal: {exc}", file=sys.stderr)


def _maybe_alert(
    payload: dict[str, Any],
    cached: Optional[dict[str, Any]],
    *,
    no_alert: bool,
) -> None:
    """Evaluate the record-decline push on the rebuild branch only."""
    cached_alert = dict((cached or {}).get("alert") or {})
    payload.setdefault("alert", dict(cached_alert))
    if no_alert:
        return
    current = payload.get("current")
    if not should_fire_decline(
        current,
        status=str(payload.get("status") or ""),
        expected_session=str(payload.get("expected_session") or ""),
    ):
        return
    if cached_alert.get("last_fired_date") == (current or {}).get("date"):
        return
    message = format_alert_message(current)
    error = _dispatch_alert(message)
    if error:
        print(f"[panic-index] alert not sent: {error}", file=sys.stderr)
        return
    payload["alert"] = {
        "last_fired_date": current["date"],
        "last_fired_kind": "decline",
    }


def _dispatch_alert(message: str) -> Optional[str]:
    from watchdog.notify import send_signal_push

    return send_signal_push(
        title=ALERT_TITLE,
        message=message,
        url=ALERT_URL,
        url_title=ALERT_URL_TITLE,
    )


def restate_cached_payload(
    cached: dict[str, Any], *, scan_time: str, now: datetime
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    payload = {**cached, "scan_time": scan_time}
    data_date = str(cached.get("data_date") or "")
    if not data_date:
        payload["expected_session"] = _expected_session(now)
        payload["status"] = "stale_source"
        return payload, {
            "message": "panic-index cache carries no data_date; cannot age the 304 reuse",
            "class": "stale_source",
        }
    return _apply_freshness_verdict(payload, data_date, now=now)


def _expected_session(now: datetime) -> str:
    from utils.market_calendar import last_completed_session_date

    return last_completed_session_date(now)


def _apply_freshness_verdict(
    payload: dict[str, Any], data_date: str, *, now: datetime
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    expected_session = _expected_session(now)
    lag_days = _calendar_days_between(data_date, expected_session)
    payload["expected_session"] = expected_session
    payload["lag_days"] = lag_days
    if lag_days > _MAX_CACHE_LAG_DAYS:
        payload["status"] = "stale_source"
        return payload, {
            "message": (
                f"panic-index data is dated {data_date} against an expected "
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
    if writer is None:
        return
    try:
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=scan_time,
            error={
                "message": f"panic-index cycle failed: {exc}",
                "class": "cycle_failed",
            },
        )
    except Exception as inner:  # noqa: BLE001
        print(f"[panic-index] cycle health write non-fatal: {inner}", file=sys.stderr)


def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    print(
        f"\nPanic Proxy — {payload['count']} joined sessions through {payload.get('data_date')}",
        file=sys.stderr,
    )
    print(
        f"  level      {current.get('level')}  delta_1d {current.get('delta_1d')}  "
        f"z {current.get('delta_z')} vs 10y",
        file=sys.stderr,
    )
    if stats:
        print(
            f"  range      {stats['low']} ({stats['low_date']}) .. "
            f"{stats['high']} ({stats['high_date']})  "
            f"avg {stats['avg']}  stdev {stats['stddev']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Panic Proxy — Cboe VIX/VVIX/VIX3M/SKEW composite (not Goldman's index)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Skip the record-decline push (first production run / local repro)",
    )
    args = parser.parse_args()

    payload = run(no_alert=args.no_alert)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

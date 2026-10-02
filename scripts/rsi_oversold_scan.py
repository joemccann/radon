#!/usr/bin/env python3
"""RSI OVERSOLD indicator — percent of current S&P 500 members whose own
14-day Wilder RSI closed strictly below 30.

Per session, per member: Wilder RSI(14) on daily closes. Aggregate
``pct_below_30 = 100 * count(rsi14 < 30) / eligible``. The 10% line is
an oversold-cluster threshold (level condition only; no duration rule).

Member closes live in the shared Turso ``price_history_daily`` store and
are kept fresh by ``bpi_scan.ensure_member_history``. The ``^GSPC``
overlay rides ``ma_ratio_scan.fetch_spx_overlay_closes`` (imported, not
copied). No new fetcher.

CLI: ``--json`` (payload to stdout; progress to stderr), ``--no-db``
(skip ALL Turso I/O), ``--backfill`` (2y Yahoo range; run once to seed).
Spec: docs/indicators/rsi-oversold.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from bpi_scan import ensure_member_history, install_sigterm_unwind
from clients.index_constituents import resolve_constituents
from db import writer
from ma_ratio_scan import attach_spx_series, fetch_spx_overlay_closes
from utils.atomic_io import atomic_save

# ── constants ─────────────────────────────────────────────────────
SCHEMA_VERSION = 1
SERVICE = "rsi-oversold"
RSI_OVERSOLD_JSON = _PROJECT_DIR / "data" / "rsi_oversold.json"

SPX_OVERLAY_SYMBOL = "^GSPC"
RSI_PERIOD = 14
OVERSOLD_RSI = 30.0
THRESHOLD_PCT = 10.0

MIN_SESSIONS = 30
MIN_LATEST_COVERAGE = 0.80
MIN_ELIGIBLE_FRACTION = 0.80

# Same SPX-only sweep budget as ma-ratio (same universe, same store).
# 1500s + one in-flight FETCH_TIMEOUT_S nests inside TimeoutStartSec=2100.
SWEEP_BUDGET_S = 1500


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr, flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── pure computation ──────────────────────────────────────────────

def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def is_oversold(rsi: float) -> bool:
    """Strict: rsi == 30 is not oversold."""
    return rsi < OVERSOLD_RSI


def rsi_series(closes_by_date: dict[str, float]) -> tuple[list[str], list[Optional[float]]]:
    """Wilder RSI(14) on the member's own date axis.

    Seed = simple mean of the first 14 gains/losses. Then
    ``avg = (prev * 13 + x) / 14``. The first value appears after 15
    closes (index 14).
    """
    dates = sorted(closes_by_date)
    closes = [float(closes_by_date[d]) for d in dates]
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if n < RSI_PERIOD + 1:
        return dates, out
    gains = 0.0
    losses = 0.0
    for i in range(1, RSI_PERIOD + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / RSI_PERIOD
    avg_loss = losses / RSI_PERIOD
    out[RSI_PERIOD] = _rsi_from_avgs(avg_gain, avg_loss)
    for i in range(RSI_PERIOD + 1, n):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (RSI_PERIOD - 1) + gain) / RSI_PERIOD
        avg_loss = (avg_loss * (RSI_PERIOD - 1) + loss) / RSI_PERIOD
        out[i] = _rsi_from_avgs(avg_gain, avg_loss)
    return dates, out


def classify_state(pct_below_30: float) -> str:
    """OVERSOLD CLUSTER when pct > 10, else NORMAL. Strict at the boundary."""
    return "OVERSOLD CLUSTER" if pct_below_30 > THRESHOLD_PCT else "NORMAL"


def detect_cross_up(rows: list[dict[str, Any]]) -> bool:
    """Previous session <= 10 and latest > 10."""
    if len(rows) < 2:
        return False
    return rows[-2]["pct_below_30"] <= THRESHOLD_PCT < rows[-1]["pct_below_30"]


def highest_since(rows: list[dict[str, Any]]) -> Optional[str]:
    """Most recent prior session whose value is >= the latest. None if none."""
    if len(rows) < 2:
        return None
    latest = rows[-1]["pct_below_30"]
    for row in reversed(rows[:-1]):
        if row["pct_below_30"] >= latest:
            return row["date"]
    return None


def aggregate_rsi_oversold(
    member_series: dict[str, tuple[list[str], list[Optional[float]]]],
    sessions: list[str],
    member_count: int,
) -> list[dict[str, Any]]:
    """Per-session breadth rows over ``sessions`` (ascending).

    A member's RSI on a session is its value on the latest member date
    <= that session (carry-forward across missing member days). Members
    whose RSI window has not filled are excluded from the denominator.
    A session is emitted only when eligible covers >= MIN_ELIGIBLE_FRACTION
    of the constituent count.
    """
    trackers = list(member_series.values())
    pointers = [0] * len(trackers)
    state: list[Optional[float]] = [None] * len(trackers)
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for i, (dates, values) in enumerate(trackers):
            p = pointers[i]
            while p < len(dates) and dates[p] <= session:
                state[i] = values[p]
                p += 1
            pointers[i] = p
        eligible = sum(1 for s in state if s is not None)
        if eligible < MIN_ELIGIBLE_FRACTION * member_count or eligible == 0:
            continue
        count_below = sum(1 for s in state if s is not None and is_oversold(s))
        rows.append({
            "date": session,
            "pct_below_30": 100.0 * count_below / eligible,
            "count_below_30": count_below,
            "eligible": eligible,
        })
    return rows


def build_output(
    *,
    rows: list[dict[str, Any]],
    member_series: dict[str, tuple[list[str], list[Optional[float]]]],
    member_count: int,
    scan_time: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    """schema_version 1 payload, or the missing variant when the run fails
    the >=MIN_SESSIONS / >=80%-latest-coverage gate."""
    if len(rows) < MIN_SESSIONS:
        return _missing_payload("insufficient_history", scan_time)
    latest = rows[-1]
    members_fresh = sum(
        1 for dates, _values in member_series.values()
        if dates and dates[-1] >= latest["date"]
    )
    if member_count <= 0 or members_fresh < MIN_LATEST_COVERAGE * member_count:
        return _missing_payload("insufficient_coverage", scan_time)

    series = [
        {
            "date": row["date"],
            "pct_below_30": row["pct_below_30"],
            "count_below_30": row["count_below_30"],
            "eligible": row["eligible"],
            "spx_close": row.get("spx_close"),
        }
        for row in rows
    ]
    current = dict(latest)
    current["state"] = classify_state(latest["pct_below_30"])
    current["cross_up"] = detect_cross_up(rows)
    current["highest_since"] = highest_since(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "scan_time": scan_time,
        "data_date": latest["date"],
        "source": source,
        "threshold": THRESHOLD_PCT,
        "current": current,
        "series": series,
        "missing": False,
    }


def _missing_payload(reason: str, scan_time: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scan_time": scan_time,
        "missing": True,
        "reason": reason,
    }


# ── constituents ──────────────────────────────────────────────────

def resolve_spx_constituents() -> tuple[list[str], str]:
    """Current SPX members via the shared cache/seed chain (never fails)."""
    tickers, source = resolve_constituents(
        "SPX", cache_dir=_data_dir() / "constituents"
    )
    return tickers, source


# ── persistence ───────────────────────────────────────────────────

def _record_ok_heartbeat(finished_at: str) -> None:
    """Best-effort ok heartbeat. A Turso read timeout must not fail the
    oneshot after rows and the snapshot have committed (ma-ratio page
    265f8e2e). Row and snapshot failures still raise."""
    try:
        writer.record_service_health(SERVICE, "ok", finished_at=finished_at)
    except Exception as exc:  # noqa: BLE001 - heartbeat is telemetry
        _log(f"service_health heartbeat failed: {exc}")


def persist_result(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback."""
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if rows:
        writer.upsert_rsi_oversold_rows(rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    _record_ok_heartbeat(_now_iso())
    _write_json_cache(payload)


def _write_json_cache(payload: dict[str, Any]) -> None:
    RSI_OVERSOLD_JSON.parent.mkdir(parents=True, exist_ok=True)
    atomic_save(str(RSI_OVERSOLD_JSON), payload)


def _data_dir() -> Path:
    return Path(os.environ.get("RSI_OVERSOLD_DATA_DIR", str(_PROJECT_DIR / "data")))


# ── orchestration ─────────────────────────────────────────────────

def run(*, backfill: bool = False, no_db: bool = False) -> dict[str, Any]:
    scan_time = _now_iso()
    install_sigterm_unwind()
    tickers, constituents_source = resolve_spx_constituents()
    _log(f"constituents: {len(tickers)} tickers via {constituents_source}")

    sweep_deadline = time.monotonic() + SWEEP_BUDGET_S
    closes, fetch_counts = ensure_member_history(
        [*tickers, SPX_OVERLAY_SYMBOL],
        backfill=backfill,
        no_db=no_db,
        sweep_deadline=sweep_deadline,
    )
    spx_closes, overlay_source = fetch_spx_overlay_closes(
        closes.pop(SPX_OVERLAY_SYMBOL, {})
    )

    member_series = {
        member: rsi_series(series)
        for member, series in closes.items()
        if member in set(tickers) and len(series) >= 2
    }
    _log(
        f"members: {len(tickers)} constituents, {len(member_series)} with close history, "
        f"overlay sessions: {len(spx_closes)}"
    )

    sessions = sorted({d for dates, _values in member_series.values() for d in dates})
    rows = attach_spx_series(
        aggregate_rsi_oversold(member_series, sessions, member_count=len(tickers)),
        spx_closes,
    )
    payload = build_output(
        rows=rows,
        member_series=member_series,
        member_count=len(tickers),
        scan_time=scan_time,
        source={
            "constituents": constituents_source,
            "constituents_count": len(tickers),
            "member_close_fetches": fetch_counts,
            "spx_overlay": overlay_source,
        },
    )
    if payload.get("missing"):
        _log(f"gated: {payload.get('reason')}; no rows/snapshot written")
        if not no_db:
            try:
                writer.record_service_health(
                    SERVICE, "error", finished_at=_now_iso(),
                    error={"message": f"gated: {payload.get('reason')}"},
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"gated-heartbeat non-fatal: {exc}")
        return payload
    if no_db:
        _log("--no-db: skipping Turso writes and the JSON mirror")
        return payload
    persist_result(payload, payload["series"])
    latest = payload["current"]
    _log(
        f"persisted {len(payload['series'])} rows through {payload['data_date']} "
        f"(pct_below_30 {latest['pct_below_30']:.2f} {latest['state']})"
    )
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SPX percent of members with Wilder RSI(14) below 30",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill", action="store_true",
        help="2y Yahoo range for every member (run once to seed the store)",
    )
    parser.add_argument(
        "--no-db", dest="no_db", action="store_true", help="skip all Turso I/O"
    )
    args = parser.parse_args(argv)

    try:
        payload = run(backfill=args.backfill, no_db=args.no_db)
    except Exception as exc:
        from db.service_cycle import record_failed_cycle

        record_failed_cycle(SERVICE, exc)
        print(f"\nRSI OVERSOLD — failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if payload.get("missing"):
            print(f"\nRSI OVERSOLD — missing: {payload.get('reason')}", file=sys.stderr)
        else:
            current = payload["current"]
            print(
                f"\nRSI OVERSOLD — {len(payload['series'])} rows\n"
                f"  {current['date']}: {current['pct_below_30']:.2f}% "
                f"({current['count_below_30']}/{current['eligible']}) "
                f"{current['state']}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

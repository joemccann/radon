#!/usr/bin/env python3
"""MA RATIO indicator — SPX percent of members above their 50-day SMA over
percent above their 200-day SMA (the StockCharts $SPXA50R:$SPXA200R
construction, computed from constituent closes, never a vendor series).

Per session, per member: close strictly above its own 50/200-session simple
moving average. Ratio = pct_above_50 / pct_above_200 with a zero-denominator
guard (ratio is null when pct_above_200 is zero). The 0.25-0.5 zone painted
on the tab marks washed-out breadth; the buy-style signal is the ratio
turning up from inside that zone (web-side derivation).

Member closes live in the shared Turso ``price_history_daily`` store and are
kept fresh by ``bpi_scan.ensure_member_history`` (batched Yahoo spark with
per-symbol chart fallback — the sanctioned bulk Yahoo deviation per the bpi
plan; IB pacing and UW rate limits are wrong for ~500 daily-bar pulls). The
``^GSPC`` overlay symbol rides the same sweep and store.

CLI: ``--json`` (payload to stdout; progress to stderr), ``--no-db`` (skip
ALL Turso I/O), ``--backfill`` (2y Yahoo range for every member; run once to
seed). Spec: docs/indicators/ma-ratio.md.
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

# Load env before any db.* import so TURSO_DB_URL resolves.
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
from utils.atomic_io import atomic_save

# ── constants ─────────────────────────────────────────────────────
SCHEMA_VERSION = 1
SERVICE = "ma-ratio"
MA_RATIO_JSON = _PROJECT_DIR / "data" / "ma_ratio.json"

SPX_OVERLAY_SYMBOL = "^GSPC"
SMA_SHORT = 50
SMA_LONG = 200

# Zone confirmed against the 1 Sep 2026 StockCharts display chart's reference
# lines (and the sp3cul8r tweet): washed-out breadth prints 0.25-0.5.
ZONE_LOW = 0.25
ZONE_HIGH = 0.5

MIN_SESSIONS = 30            # below -> missing:true, no writes
MIN_LATEST_COVERAGE = 0.80   # members reporting the latest session (bpi R-224)
MIN_ELIGIBLE_FRACTION = 0.80  # 200-window-filled members required per row

# Wall-clock ceiling for the member-close sweep. SPX-only (~504 symbols incl.
# the ^GSPC overlay), one fifth of bpi's three-index universe: the incremental
# path is ~26 spark requests plus chart fallback for stragglers, and the
# divyield sibling (503 per-symbol chart calls) self-limits at 1800s inside
# the same TimeoutStartSec=2100. 1500s leaves ~570s of persist slack after
# one in-flight FETCH_TIMEOUT_S. Nesting pinned in test_ma_ratio.py.
SWEEP_BUDGET_S = 1500


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr, flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── pure computation ──────────────────────────────────────────────

def sma_flags_series(
    closes_by_date: dict[str, float],
) -> tuple[list[str], list[Optional[bool]], list[Optional[bool]]]:
    """Per-session above-50 / above-200 flags on the member's own date axis.

    A flag is None until the window fills; above = close STRICTLY greater
    than the SMA of the member's trailing ``window`` closes inclusive of the
    session (a close exactly on its SMA is not above — pinned in tests).
    Rolling sums, so the 252-session fixture and a 500-member universe both
    stay O(n).
    """
    dates = sorted(closes_by_date)
    closes = [float(closes_by_date[d]) for d in dates]

    def flags(window: int) -> list[Optional[bool]]:
        out: list[Optional[bool]] = []
        running = 0.0
        for i, close in enumerate(closes):
            running += close
            if i >= window:
                running -= closes[i - window]
            if i + 1 < window:
                out.append(None)
            else:
                out.append(close > running / window)
        return out

    return dates, flags(SMA_SHORT), flags(SMA_LONG)


def compute_ratio(pct50: float, pct200: float) -> Optional[float]:
    """pct_above_50 / pct_above_200 with the zero-denominator guard: a full
    200-day washout (pct200 == 0) yields None, stored as SQL NULL."""
    if pct200 == 0:
        return None
    return pct50 / pct200


def aggregate_ma_ratio(
    member_flags: dict[str, tuple[list[str], list[Optional[bool]], list[Optional[bool]]]],
    sessions: list[str],
    member_count: int,
) -> list[dict[str, Any]]:
    """Per-session breadth rows over ``sessions`` (ascending).

    A member's state on a session is its flags on the latest member date
    <= that session (carry-forward across missing member days, as in bpi's
    aggregate). Members whose window has not filled are excluded from that
    window's denominator. A session is emitted only when the 200-window
    denominator covers >= MIN_ELIGIBLE_FRACTION of the constituent count —
    early sessions with too little history produce no row.
    """
    trackers = list(member_flags.values())
    pointers = [0] * len(trackers)
    state50: list[Optional[bool]] = [None] * len(trackers)
    state200: list[Optional[bool]] = [None] * len(trackers)
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for i, (dates, above50, above200) in enumerate(trackers):
            p = pointers[i]
            while p < len(dates) and dates[p] <= session:
                state50[i] = above50[p]
                state200[i] = above200[p]
                p += 1
            pointers[i] = p
        eligible50 = sum(1 for s in state50 if s is not None)
        eligible200 = sum(1 for s in state200 if s is not None)
        if eligible200 < MIN_ELIGIBLE_FRACTION * member_count or eligible50 == 0:
            continue
        count50 = sum(1 for s in state50 if s)
        count200 = sum(1 for s in state200 if s)
        pct50 = 100.0 * count50 / eligible50
        pct200 = 100.0 * count200 / eligible200
        rows.append({
            "date": session,
            "pct_above_50": pct50,
            "pct_above_200": pct200,
            "ratio": compute_ratio(pct50, pct200),
            "count_above_50": count50,
            "count_above_200": count200,
            "eligible_50": eligible50,
            "eligible_200": eligible200,
        })
    return rows


def attach_spx_series(
    rows: list[dict[str, Any]], spx_close_by_date: dict[str, float]
) -> list[dict[str, Any]]:
    """Attach spx_close — the ^GSPC session close the chart overlays.

    Best-effort: a session the overlay sweep missed carries null and the
    chart hides the overlay for that row.
    """
    return [
        {**row, "spx_close": spx_close_by_date.get(row["date"])} for row in rows
    ]


def build_output(
    *,
    rows: list[dict[str, Any]],
    member_flags: dict[str, tuple[list[str], list[Optional[bool]], list[Optional[bool]]]],
    member_count: int,
    scan_time: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    """schema_version 1 payload, or the missing variant when the run fails
    the >=MIN_SESSIONS / >=80%-latest-coverage gate (never cache or persist
    an empty/degenerate payload)."""
    if len(rows) < MIN_SESSIONS:
        return _missing_payload("insufficient_history", scan_time)
    latest = rows[-1]
    # Count only members that actually reported the latest aggregated session
    # THIS run — carry-forward would otherwise mask a truncated sweep as
    # complete breadth (bpi R-224).
    members_fresh = sum(
        1 for dates, _a50, _a200 in member_flags.values()
        if dates and dates[-1] >= latest["date"]
    )
    if member_count <= 0 or members_fresh < MIN_LATEST_COVERAGE * member_count:
        return _missing_payload("insufficient_coverage", scan_time)

    series = [
        {
            "date": row["date"],
            "pct_above_50": row["pct_above_50"],
            "pct_above_200": row["pct_above_200"],
            "ratio": row["ratio"],
            "spx_close": row.get("spx_close"),
        }
        for row in rows
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "scan_time": scan_time,
        "data_date": latest["date"],
        "source": source,
        "zone": {"low": ZONE_LOW, "high": ZONE_HIGH},
        "current": dict(latest),
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

def persist_result(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.

    The full computed window is upserted every run (idempotent per date), so
    weekend/holiday runs recompute identical rows and act as unchanged-data
    heartbeats — the snapshot + service_health row still refresh every cycle
    (feedback_service_health_heartbeat).
    """
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if rows:
        writer.upsert_ma_ratio_rows(rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    _write_json_cache(payload)


def _write_json_cache(payload: dict[str, Any]) -> None:
    MA_RATIO_JSON.parent.mkdir(parents=True, exist_ok=True)
    atomic_save(str(MA_RATIO_JSON), payload)


def _data_dir() -> Path:
    return Path(os.environ.get("MA_RATIO_DATA_DIR", str(_PROJECT_DIR / "data")))


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
    spx_closes = closes.pop(SPX_OVERLAY_SYMBOL, {})

    member_flags = {
        member: sma_flags_series(series)
        for member, series in closes.items()
        if member in set(tickers) and len(series) >= 2
    }
    _log(
        f"members: {len(tickers)} constituents, {len(member_flags)} with close history, "
        f"overlay sessions: {len(spx_closes)}"
    )

    sessions = sorted({d for dates, _a50, _a200 in member_flags.values() for d in dates})
    rows = attach_spx_series(
        aggregate_ma_ratio(member_flags, sessions, member_count=len(tickers)),
        spx_closes,
    )
    payload = build_output(
        rows=rows,
        member_flags=member_flags,
        member_count=len(tickers),
        scan_time=scan_time,
        source={
            "constituents": constituents_source,
            "constituents_count": len(tickers),
            "member_close_fetches": fetch_counts,
        },
    )
    if payload.get("missing"):
        _log(f"gated: {payload.get('reason')}; no rows/snapshot written")
        return payload
    if no_db:
        _log("--no-db: skipping Turso writes and the JSON mirror")
        return payload
    persist_result(payload, payload["series"])
    latest = payload["current"]
    _log(
        f"persisted {len(payload['series'])} rows through {payload['data_date']} "
        f"(ratio {latest['ratio'] if latest['ratio'] is not None else 'null'})"
    )
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SPX pct-above-50d-MA over pct-above-200d-MA breadth ratio",
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

    payload = run(backfill=args.backfill, no_db=args.no_db)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if payload.get("missing"):
            print(f"\nMA RATIO — missing: {payload.get('reason')}", file=sys.stderr)
        else:
            current = payload["current"]
            ratio = current["ratio"]
            print(
                f"\nMA RATIO — {len(payload['series'])} rows\n"
                f"  {current['date']}: ratio "
                f"{f'{ratio:.4f}' if ratio is not None else 'null'} "
                f"(pct50 {current['pct_above_50']:.2f} / pct200 {current['pct_above_200']:.2f})",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

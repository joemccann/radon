#!/usr/bin/env python3
"""SKEW 2D Indicator — two-session change in SPX 1M put/call IV ratio.

Derives change_2d_t = ratio_t - ratio_{t-2} from the existing SKEW history
(Turso skew_history / data/skew.json). Network-free: no UW/IB/Yahoo calls.

Spec: docs/indicators/skew2d.md.

Output is dual-written to Turso skew2d_history + data/skew2d.json.

Usage:
    python3 scripts/fetch_skew2d.py            # human summary (stderr)
    python3 scripts/fetch_skew2d.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal
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

# ── constants ─────────────────────────────────────────────────────
SKEW2D_JSON = _PROJECT_DIR / "data" / "skew2d.json"
SKEW_JSON = _PROJECT_DIR / "data" / "skew.json"
_RATIO_KEYS = ("date", "expiry", "dte", "put_iv", "call_iv", "ratio")


# ── pure transforms ───────────────────────────────────────────────

def compute_change_2d_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending by date; attaches change = ratio_t - ratio_{t-2} (None first two).

    Difference via Decimal(str(...)) so short-decimal hand ratios (1.30 - 1.25)
    land on exact -0.05 etc.; full-precision Turso/fixture floats still round-trip
    within the 1e-12 pin tolerance.
    """
    ordered = sorted(rows, key=lambda r: r["date"])
    series: list[dict[str, Any]] = []
    for i, row in enumerate(ordered):
        change = None
        if i >= 2:
            change = float(
                Decimal(str(row["ratio"])) - Decimal(str(ordered[i - 2]["ratio"]))
            )
        series.append({**row, "change": change})
    return series


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, float]]:
    """{high, low, avg, stddev (population)} over non-null 2d changes."""
    changes = [row["change"] for row in series if row.get("change") is not None]
    if not changes:
        return None
    return {
        "high": max(changes),
        "low": min(changes),
        "avg": statistics.fmean(changes),
        "stddev": statistics.pstdev(changes),
    }


def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    source: str = "skew_history",
    market_status: str = "closed",
) -> dict[str, Any]:
    """Full API contract for the skew2d scan snapshot / JSON fallback."""
    current = None
    if series:
        last = series[-1]
        current = {
            "date": last["date"],
            "ratio": last["ratio"],
            "change": last.get("change"),
            "put_iv": last.get("put_iv"),
            "call_iv": last.get("call_iv"),
            "expiry": last.get("expiry"),
            "dte": last.get("dte"),
        }
    return {
        "scan_time": scan_time,
        "source": source,
        "market_status": market_status,
        "count": len(series),
        "current": current,
        "stats": compute_stats(series),
        "series": [
            {"date": row["date"], "ratio": row["ratio"], "change": row.get("change")}
            for row in series
        ],
    }


# ── load / persist ────────────────────────────────────────────────

def load_ratio_rows() -> list[dict[str, Any]]:
    """Turso skew_history first; data/skew.json series fallback when empty/unreachable."""
    try:
        from db.client import get_db

        rows = get_db().execute(
            "SELECT date, expiry, dte, put_iv, call_iv, ratio FROM skew_history ORDER BY date"
        ).fetchall()
        if rows:
            return [dict(zip(_RATIO_KEYS, row)) for row in rows]
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        print(f"[skew2d] turso rehydrate non-fatal: {exc}", file=sys.stderr)

    return _load_ratio_rows_from_json()


def _load_ratio_rows_from_json() -> list[dict[str, Any]]:
    try:
        payload = json.loads(SKEW_JSON.read_text())
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for row in payload.get("series") or []:
        if row.get("ratio") is None or row.get("is_intraday"):
            continue
        out.append(
            {
                "date": row["date"],
                "expiry": row.get("expiry"),
                "dte": row.get("dte"),
                "put_iv": row.get("put_iv"),
                "call_iv": row.get("call_iv"),
                "ratio": row["ratio"],
            }
        )
    out.sort(key=lambda r: r["date"])
    return out


def load_prior_payload() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/skew2d.json, if present."""
    try:
        return json.loads(SKEW2D_JSON.read_text())
    except (OSError, ValueError):
        return None


def persist_json(payload: dict[str, Any]) -> None:
    """Atomic write of data/skew2d.json."""
    SKEW2D_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = SKEW2D_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, SKEW2D_JSON)


def _fingerprint(payload: dict[str, Any]) -> tuple[Any, ...]:
    """Material-change key: count + last date + last ratio + last change."""
    current = payload.get("current") or {}
    return (
        payload.get("count"),
        current.get("date"),
        current.get("ratio"),
        current.get("change"),
    )


def _market_status(now: datetime) -> str:
    try:
        from utils.market_calendar import market_state

        state = market_state(now)
        return "open" if state.get("is_open") else "closed"
    except Exception:  # noqa: BLE001 — daily job default closed
        return "closed"


def _write_db(
    series: list[dict[str, Any]],
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
) -> None:
    """Snapshot + heartbeat every cycle; history upsert only when changed."""
    if writer is None:
        return
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_skew2d_rows(series, recorded_at=scan_time)
        writer.upsert_scan_snapshot("skew2d", scan_time, payload)
        writer.record_service_health("skew2d", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[skew2d] db cache non-fatal: {exc}", file=sys.stderr)


# ── orchestration ─────────────────────────────────────────────────

def run(*, now: Optional[datetime] = None) -> dict[str, Any]:
    """Load ratios → 2d series → dual-write (history only when fingerprint moves)."""
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    ratio_rows = load_ratio_rows()
    if not ratio_rows:
        raise ValueError("skew2d: zero ratio rows (never cache empty)")

    series = compute_change_2d_series(ratio_rows)
    payload = build_payload(
        series,
        scan_time=scan_time,
        source="skew_history",
        market_status=_market_status(now),
    )

    prior = load_prior_payload()
    rows_changed = prior is None or _fingerprint(prior) != _fingerprint(payload)
    if not rows_changed:
        print("[skew2d] source unchanged; refreshing snapshot only", file=sys.stderr)

    _write_db(series, payload, scan_time, rows_changed=rows_changed)
    persist_json(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    change = current.get("change")
    print(
        f"\nSKEW 2D — {payload['count']} sessions through {current.get('date')}",
        file=sys.stderr,
    )
    print(
        f"  change_2d  {change:+.6f}" if change is not None else "  change_2d  n/a",
        file=sys.stderr,
    )
    print(f"  ratio      {current.get('ratio')}", file=sys.stderr)
    if stats:
        print(
            f"  stats      high={stats['high']:+.4f} low={stats['low']:+.4f} "
            f"avg={stats['avg']:+.6f} stddev={stats['stddev']:.4f}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SKEW 2D — two-session change in SPX 1M put/call ratio"
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
